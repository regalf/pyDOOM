# OpenGL renderer — specs and implementation

Branch `opengl-renderer`, milestone H. This document describes the
required API, the format of every GPU resource, the frame pipeline,
the lighting formulas — and, at the end, why OOB geometry is visible
through windows while the software renderer hides it.

Engine reference: vanilla `r_bsp.c` / `r_segs.c` / `r_plane.c` /
`r_things.c` / `r_sky.c`, replicated in `pydoom/renderer.py` (frozen
reference: the software side is never touched, GL chases it).

## 1. Requirements and context

- **OpenGL 3.3+ core** profile (GLSL `#version 330 core`), verified
  via `GL_VERSION`; no immediate mode (`glBegin` is unusable from
  Python, unlike dsda which uses it in C).
- Libraries: stable `PyOpenGL` (mandatory for the GL path),
  `PyOpenGL_accelerate` optional speed-up only.
- `pygame.OPENGL | DOUBLEBUF` window (default 960×600); the window
  owns the context, PyOpenGL just talks to it.
- Backend selection in `pydoom/glrender/state.py`: `resolve_api()` is
  pure (headless-testable) and forces software on dummy video,
  `--frames` smokes, timedemo, or missing PyOpenGL; `try_init()`
  never raises out of the viewer (every failure recreates the
  software window).
- Default `software`; demos and checksums always run on software.
- Headless-safe: no GL imports at module import time
  (`from OpenGL import GL` only inside functions with a live
  context); tests without a context skip.

## 2. Key idea: everything in palette indices

Like the software path (`cmap[texel]` in `fastdraw.py`, then PLAYPAL
at present), the fragment shaders also work in two steps over
indices:

1. `lit = colormap(lightlevel, idx)` from the `COLORMAP` LUT,
2. `rgb = palette[lit]` from the `PLAYPAL` LUT.

So textures upload **indices, never RGB**: walls/sprites `GL_RG8`
(R = index, G = hole mask), flats/colormap/lights `GL_R8`, palette
`GL_RGB8`. Lighting then reproduces vanilla instead of approximating
it.

## 3. GPU resources (`pydoom/glrender/upload.py`, `GlResources`)

| resource | format | contents |
|---|---|---|
| wall VBO | 9 floats/vertex: `[x,y,z, u,texbase, sector,tweak01, nx,ny]` | one quad per non-degenerate tier |
| wall IBO | `uint32`, 6 indices/quad, reordered by texture | batches of `(texnum, start, count)` |
| wall tex | one `GL_RG8` per used texture (+ sky + switch pairs) | R = palette index, G = hole mask |
| plane VBO | 7 floats/vertex: `[x,y,z, u,v, layer,sector]` | non-indexed; sky tris (`flat=-1`) excluded |
| flat array | `GL_R8` 64×64×N (`GL_TEXTURE_2D_ARRAY`) | every decodable flat (donuts swap pics at runtime) |
| sprite tex | one `GL_RG8` per patch | like walls |
| colormap | `GL_R8` 256×32 | first 32 lump rows (the renderer clamps at 31) |
| palette | `GL_RGB8` 256×1×14 (`GL_TEXTURE_2D_ARRAY`) | all 14 flash palettes, `uPalIndex` selects |
| sector light | `GL_R8` W=numsectors×1 | base `lightlevel>>4` clamped 0..15, for walls+planes |
| scalelight | `GL_R8` 48×16 | row = lightnum, column = scale index (walls) |
| zlight | `GL_R8` 128×16 | row = lightnum, column = distance index (planes) |
| fuzz LUT | `GL_R8` 50×1 | `FUZZOFFSETS` table (uniform arrays only upload their first element under PyOpenGL) |
| overlay | `GL_R8` 320×200 | software index fb; **index 255 = transparent** (verified never emitted by art) |
| FBO | `RGB8` + `R8` (indices) + `DEPTH_COMPONENT24`, 2 draw buffers | world; window gets blit + overlay + text |

Fixed texture units: 0 wall/sprite/sky/flat/overlay/text (taking
turns per program), 1 scalelight/zlight, 2 colormap, 3 palette,
4 fuzz backdrop copy, 5 fuzz LUT, 6 sector lights. `GL_DITHER` off
(LSB parity), depth `LESS`, front `CCW`, `CULL_FACE` only in the wall
pass.

## 4. Static geometry (`pydoom/glrender/preprocess.py`)

- **Walls**: one `WallQuad` per non-empty tier of every seg, using the
  exact `_store_wall_range` rules (fixed-point comparisons, float
  output): single-sided mid; two-sided top/bottom; masked mid;
  `ML_DONTPEGTOP`/`DONTPEGBOTTOM` flags; the vanilla unpegged-bottom
  quirk (anchored at the front ceiling); **outdoor sky hack** (sky
  ceilings on both sides ⇒ no top, like the software); degenerate
  spans (`zt <= zb`, e.g. closed doors) skipped. World coords in map
  units, `u` in texels from `v1 + textureoffset + seg.offset`,
  `v(z) = texbase - z` (world-pinned textures: the `viewz` inside
  texturemid cancels itself). Front-unit normal `(dy,-dx)/len`.
- **Planes**: fan triangulation of the BSP leaf polygons (exact
  `Fraction` clipping of the vertex bbox down the node tree; leaves
  tile the bbox, areas asserted; collinear tris and degenerate slivers
  skipped). Each tri carries `sector` + `flat`; world UVs `(x,-y)`
  with vanilla-mirrored Y like `R_MapPlane` (`mod 64` in-shader).
  Sky tris become holes filled by the sky cylinder, not by the VBO.
- **Batches**: `plan_wall_batches` sorts indices per `texnum` into
  three lists: `opaque` (non-masked two-sided, culled), `single`
  (single-sided, double-sided), `masked` (culled + alpha-tested,
  depth written like opaque: Doom has no translucency, order is
  irrelevant).

## 5. Camera and projection (`camera_frame` in `draw.py`)

- Basis from the fine tables (`finecosine/finesine(angle>>19)`),
  never `math.sin/cos`; world coords turn float only at the upload
  boundary.
- Vanilla projection: x-focal
  `centerxfrac/finetangent(45°+FOV/2)` ≈ 0.99924, y-focal `w/h`
  (square pixels: 160 at 320×200).
- `NEAR 0.5`, `FAR 32768`; `viewz` = sector floor + 41.
- `pitch` (±, freecam-only, default 0 = bit-identical matrix):
  rotates up/forward around the right axis; lighting and sky stay
  yaw-locked when pitched (debug approximation).

## 6. Frame pass (`FrameRenderer.render`, in order)

1. **Sky** first, no depth write (walls overdraw it): a 64-segment
   camera-following cylinder (R=20000 inside the far plane, ±25000
   in z), U from one `point_to_angle2>>22` anchor + 16 columns per
   segment (a full turn is exactly 1024 columns), V from the row like
   the software sky column (`100 + (row-cy)·200/H`), colormap row 0.
2. **Opaque + masked walls** with culling (fronts wind CCW: partner-seg
   backs, which the BSP never draws, would otherwise z-fight their
   coplanar fronts with a wrong light row).
3. **Single-sided** double-sided (vanilla draws single backs
   mirrored).
4. **Planes** double-sided (`glDrawArrays`, no IBO).
5. **Sprites**: per-frame rebuilt camera-facing billboards (dynamic
   VBO, `MAXVISSPRITES` = 128 cap like vanilla, extras dropped);
   `tz < MINZ` (4.0) and `|tx| > tz<<2` culling; each sprite graded
   by a single scalelight entry (sector lightnum + orient tweak +
   extralight); flip mirrors U; depth-tested like everything else.
6. **Fuzz** (`MF_SHADOW` spectres) last over the finished backdrop:
   copied index-target backdrop (avoids a feedback loop), ± row from
   `FUZZOFFSETS`, colormap row 6; the global pixel counter is
   approximated by frame+row (shimmer differs, colors match).
7. **Weapon psprite**: screen-space quad from the vanilla anchor
   (`1+bobx-leftoff`, `32+boby-topoff` in 320×200 scaled native),
   raw indices with no light and no depth test, on top of all.
8. 320×200 overlay fb (nearest, chunky look kept), RGBA text and
   automap (`GL_LINES`, 4096-segment cap) on the window; `blit_world`
   copies the FBO 1:1 NEAREST before the overlay.

## 7. Programs (9 + auto, `shaders.py`, all with `uPalIndex`)

wall, wall_double (same frag with an `abs(den)` guard), plane,
sprite, fuzz, sky, psprite, overlay, text, auto. Every main program
also emits the raw index to the second target (the fuzz pass needs
it).

## 8. Lighting (formulas)

- **Walls**: `den = dot(f,n)·dot(fdir,viewdir)` (front-unit normals
  point back at the viewer, so visible faces have `den < 0`); on
  `den >= 0` keep brightest index 47 (the software never draws those
  silhouettes). Else `li = clamp(160·dot(fdir,n)/den·16, 0, 47);
  base from the sector-light texture (`lightlevel>>4`), orient tweak
  −1/0/+1 carried as attribute 0/1/2, **double clamp** like the old
  baked formula (`clamp(base+tweak)` then `+extralight`, clamped
  again); row = `scalelight[li][row]`; fullbright skips everything.
  Single-sided backs use `abs(den)`: a mirrored camera sees the same
  ratio.
- **Planes**: `dy = |H/2−½−fragy|·200/H`, `hu = |z−viewz|`,
  `li = 127` at the horizon, else `clamp(hu·10/dy, 0, 127)`; row =
  `zlight[li][row]` with `row = clamp(base+extralight)`.
- Muzzle flash = `extralight`, visor = `fullbright`, damage/bonus =
  `uPalIndex` on every program (world included).

## 9. Dynamic sectors (`dynamic.py` + viewer)

Snapshot of every baked mutable field (heights, pics, lights, sidedef
texnums + fan topology) classified per frame, read-only w.r.t. the
sim: `CLEAN` (zero GL work), `LIGHT` (re-upload the light texture
only), `GEO` (rebuild walls + re-emit planes from the fans, VBOs
refilled in place under the same ids so VAOs stay valid). New
textures mid-game: prebuilt switch pairs (`SW1*`/`SW2*`), ALL flats
prebuilt (donuts), load-degenerate tiers uploaded on demand.

## 10. Why OOB shows (windows onto the void)

The software and the GL renderer **do not decide visibility the same
way**:

1. **Software**: front-to-back BSP walk + per-column clip
   (`solidsegs`). A single-sided wall **closes the columns**:
   whatever sits behind it — even if it would geometrically peek over
   the top — is never visited. Void is *absence of data*: never
   written pixels stay black; from outside the map, bboxes stay
   behind = HOM/black. Single-sided backs are still drawn (mirrored;
   the walk does no facing cull).
2. **GL**: one static mesh of EVERYTHING + depth buffer.
   Geometrically right, vanilla-different in three structural cases:
   - **peek-over**: tall/far geometry stays visible over a low wall
     whose columns SW had closed;
   - **void tris**: fans tile the whole vertex bbox, void included,
     tagged with the leaf's sector — mapper exterior slabs and black
     untextured backs really exist in the mesh, and depth shows them
     through windows/over walls;
   - **coplanar ties**: depth picks arbitrarily where the software
     painter was deterministic.
3. **Measured on E1M1** (aerial `outofbounds.png`: x=220 y=−5145
   z=1215 — 281 units *outside* the north bbox edge, `sec=24`):
   rim sectors `[1,28,62]` are portal-reachable **because id wired
   them up** (real windows), so no static culler may remove them;
   SW grid around void spots = 50–83% uniformly black vs 0–8% for
   interiors (walled-in camera, unreachable under collision). PVS
   flood experiment done and reverted (`bd83dfd`→`4c5437c`): it
   dropped 13/85 sectors but changed ~zero pixels at the reported
   spots — the visible margin is portal-reachable.
4. **Accepted**: collision keeps the player in-bounds forever (under
   noclip vanilla shows HOM, worse); the collisionless freecam may
   leave, and from there every view is out of spec by definition —
   inspect it with `tools/gl_freecam.py` (`--pos=x,y,z,ang[,pitch]`).
5. **Open** (same geometry, light/tier gaps — not OOB): north-room
   fence `(1169,−2274,247°)` and dark northern interiors; table and
   gate in `docs/DIVERGENCES.md` + `tests/test_gl.py`.

Known limits: sprites past 128 drop (like vanilla), overlay stays
320×200 nearest, text/extra-HUD ride GL quads with per-frame upload.
