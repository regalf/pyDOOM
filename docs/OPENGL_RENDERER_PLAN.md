# OpenGL renderer — implementation plan

Branch: `opengl-renderer`. Milestone section H of `milestone.md`.

One complete GL renderer behind a `video api = software|opengl` setting
(default **software**). No hybrid presenter: the GL path owns wall/flat/
sky/sprite drawing at native resolution; the software raster stays as
the reference and as the auto-fallback. Modeled on dsda-doom's
`gl_main.c` / `gl_preprocess.c`, but written for modern GL (core
profile) because dsda's `glBegin` immediate mode is unusable from
Python (per-vertex Python calls).

## Hard constraints

- **Sim is off-limits.** Every checksum/demo/RNG test stays on the
  software path (`tools/doom_view.py` already only *presents* a
  framebuffer; GL replaces presentation, never the sim).
- **Headless-safe.** Missing/PyOpenGL-less install, dummy SDL video
  (CI), context-creation failure, and `--frames` smoke runs must never
  touch the GL path: auto-fallback to software, silently.
- **Reference parity gate.** GL must be *compared*, tolerance-based,
  against the software framebuffer on fixed scenes before it can be
  claimed faithful.
- **Fixed-point camera basis.** Camera basis keeps coming from the
  `finesine`/`finecosine` tables (like `renderer.render_view`), not
  from `math.sin/cos`; fixed-point world coords become floats only at
  the upload boundary.

## Current software architecture to map onto GL

| software | role | GL counterpart |
|---|---|---|
| `renderer.py:render_view` | per-frame BSP walk + raster | per-frame camera uniform + draw |
| `renderer.py:_render_bsp_node/.../_clip_*` | BSP traversal, clipping (stays: it feeds seg visibility in software order) | static VBO + per-seg draw; traversal/visibility via GL clip volume or kept per-column clip |
| `renderer.py:_store_wall_range` + `_render_seg_loop` | wall tiers (top/mid/bottom), `draw_column` | per-seg quads (top/mid/bottom), palette-index wall textures |
| `renderer.py:_find_plane/_draw_planes` + `draw_span` | floors/ceilings | plane polygons (sector triangulation), flat textures, sky plane |
| `renderer.py:_project_things/_project_sprite/_draw_masked` + `draw_fuzz` | sprites + masked mid | per-frame billboards, sprite textures, fuzz shader |
| `renderer.py:_init_zlight/_init_scalelight` | light → colormap selection | colormap LUT texture + per-vertex light ramp |
| `palette.py` + `palette_luts[palette][fb]` (doom_view:1727) | index → RGB at present | palette LUT texture in fragment shader |
| `doom_view.py:1728` `pygame.transform.scale` | 320x200 → window | GL frame at native res; overlay quad scaled nearest |
| `wipe.py` melt, menu, statusbar, intermission, automap | 320x200 index overlays | keep index-buffer path, upload as quad texture (nearest) |

Key idea: **textures stay in palette-index space** (`GL_R8`), and the
fragment shader does `colormap(lightlevel, idx)` then `palette(lit)`,
i.e. the exact two steps the software raster does per pixel
(`cmap[texel]` in `fastdraw.py`, then PLAYPAL at present). Lighting
then reproduces vanilla instead of approximating per-surface.

## Staged delivery

### Phase 0 — plumbing, safety, setting (day 1-2)

- `video api` setting: `Settings` dataclass (`menu.py`), `pydoom.cfg`
  key, CLI flag `--video-api`, launcher option (`pyDOOM.py`).
- New package `pydoom/glrender/`: `state.py`, `textures.py`,
  `preprocess.py`, `draw.py`, `light.py`, `present.py`.
- `glrender/state.py:try_init()` → GL version string or `None`;
  conditions that force software: no `PyOpenGL`,
  `SDL_VIDEODRIVER=dummy`, `--frames`, `--video-api=software`,
  context error. All failures log once, never raise out of the viewer.
- `tools/doom_view.py`: window creation becomes `software` (current
  `pygame` path) vs `opengl` (`pygame.OPENGL|DOUBLEBUF`, the window
  owns the context and PyOpenGL only verifies `GL_VERSION`); on GL
  init failure recreate the software window.
- `requirements.txt` + `pyDOOM.spec`: add `PyOpenGL==3.1.10` stable
  plus `PyOpenGL_accelerate==3.1.10` (optional C speed-up, never
  required). CI stays on software (Phase 5 adds an optional llvmpipe
  smoke job).

### Phase 1 — static geometry preprocess (gl_preprocess.c analog)

Once per map load (not per frame; invalidated on level change):

- **Wall quads.** Walk `mapdata.Map` segs/sidedefs exactly like
  `_store_wall_range` computes tiers: one quad per non-empty
  top/mid/bottom region per seg, honoring `ML_DONTPEGTOP`/
  `ML_DONTPEGBOTTOM` and the vanilla midtexture column offset rule.
  Vertices in fixed-point world space + texture column parameter.
- **Plane polygons.** BSP-leaf convex polygons (clip the vertex
  bbox down the node tree with exact Fractions; children[0] keeps
  the RIGHT side per point_on_side, on-line vertices join both
  children watertight), fan-triangulated per subsector sector. The
  engine's own partition tiles the map, so pillars, islands,
  disjoint parts, stub-wall mouths and E3M8's overlapping sectors
  need no special cases (loop-walking from linedefs was tried and
  abandoned: id's maps are not edge-closed per sector). Leaf tiling
  is asserted (areas sum to the bbox); leaf attribution is
  cross-checked against the renderer's BSP walk (on-line points may
  pick either neighbor: shared edge).
- **Per-vertex light ramp.** Walls store the scalelight base
  (sector>>4 + N/S tweak); planes the zlight base (sector>>4, sky
  forces 0 like _find_plane). Distance grading happens shader-side
  via fragment depth (no interpolation keys needed).
- **Textures into atlases.** Using `TextureManager`: wall textures as
  `GL_R8` palette-index textures in a `GL_TEXTURE_2D_ARRAY` array of
  arrays; flats identically; keep pixel data *indices* (no PLAYPAL).
  Animated flats (`AF_*`) and glowing textures (`FF_FRAMEMASK`,
  `GLOW*`/`BLODR*`) resolved per tic: swap the array layer after
  `flow`/`texture` tick, as `info.py` frame masks dictate.
- **Sky.** Build one sky surface per frame from
  `Renderer.skytexture` + `ANGLETOSKYSHIFT` parallax (r_sky idea);
  software `_draw_sky_plane` is the parity target.
- Everything baked on CPU with numpy/numba; uploaded once to VBOs.

### Phase 2 — shaders (the parity core)

- **Vertex shader**: fixed→float view transform (projection must match
  vanilla `projection = centerxfrac`, `yslope`); pass light ramp +
  texture layer/uv + per-vertex palette-gamma flags.
- **Fragment shader** (pixel = software `_draw_column`/`_draw_span`):
  - sample `GL_R8` palette index;
  - `lit = colormap(lightlevel, idx)` from a `32x256` colormap LUT
    texture (fed from the `COLORMAP` lump bytes already cached at
    `renderer.py:264`);
  - `rgb = palette[lit]` from a `256x3` PAL (`palette.load_playpal`);
  - `fullbright` (light amplification visor) bypasses colormap;
  - `extralight` (muzzle flash) offsets the colormap row, clamped to
    `NUMCOLORMAPS - 1` like `_init_scalelight`.
- **Fuzz** (`MF_SHADOW`, spectres): deferred to Phase 3 together
  with the spectre billboards that use it (untestable without
  sprites); shader stipple using the `FUZZOFFSETS` table stays the
  approach, tolerance-based like the software `draw_fuzz`.
- **Depth / draw order**: depth buffer for plane+wall boundary (with a
  small epsilon for wall/floor abutment, the classic prBoom GL
  problem), painter-order for two-sided masked mids and sprites from
  `project_mobjs`.

### Phase 3 — dynamic objects (per frame)

- **Sprites.** Keep `renderer.py`'s projection math
  (`_project_sprite`, `sprite_num_for_base`, rotation tables from
  `init_sprite_defs`) as the CPU feed: it yields screen-space
  billboards; GL draws them as camera-facing quads with the patch
  texture at native res. Sprite textures again `GL_R8` palette-index.
- **Masked mids.** Two-sided segs with a midmask texture become
  transparent quads drawn in painter order after opaque walls.
- **Weapon psprite.** Fullscreen quad at native res using the `P_*`
  sprite; bob/offsets scaled from the 320x200 anchor
  (`draw_psprite` anchor math, tests in `test_renderer.py`).
- **Fuzz** on shadowed sprites: shader variant from Phase 2.

### Phase 4 — overlay compositing (keep index path)

- Menu, status bar, HUD, automap, intermission, pause, and the melt
  (`wipe.py`) all keep drawing into the existing `(200,320)` index
  framebuffer. GL uploads that fb as an overlay texture and draws it
  over the world plane at native-res scale with **nearest neighbor**
  (preserves the chunky Doom look; matches the current
  `pygame.transform.scale` feel but at real native res for the world).
- Palette flash (`palette_luts[palette_index]`): overlays get the same
  index→palette LUT shader so damage/bonus flashes match software.
- Melt stays in the overlay plane; no GL-specific wipe needed initially
  (parity with current asset behavior).

### Phase 5 — parity gate, fallback tests, CI

- `tests/test_gl.py` behind `pytest.mark.skipif(no context)`: render a
  fixed scene (fixed sim, fixed camera from `tables`) on both paths and
  compare. Tolerance metric: full exact-match fraction on wall interiors
  + allowed pixel counts for documented classes (fuzz stripes, sky
  parallax edges, distance-light ramp near columns). Enforced by
  regression on, e.g., E1M1 start + a scripted camera walk.
- Fallback unit tests: `--video-api=opengl` under dummy SDL and with
  `OpenGL` import blocked ⇒ software path, exit code/--frames
  unaffected.
- CI: the normal job stays software-only. Optional separate job uses
  `xvfb + llvmpipe` to exercise the GL path (`libgl1-mesa-dri`) with
  dummy audio; tagged `workflow_dispatch`/`--frames` smoke so normal
  PR runs stay fast and deterministic.
- `docs/DIVERGENCES.md`: add the OpenGL parity table and the known
  divergence classes (fuzz, sky, distance-light grading).

### Phase 6 — performance

- One draw call per texture-array layer where possible; dynamic sprite
  VBO refilled once per frame from `project_mobjs`.
- Preprocess cost measured with `--frames` + `--extra-hud` fps readout
  (already in `doom_view.py`); target 1080p ≥ 60 fps on the dev box.
- numba kernels stay the CPU side of preprocess/projection; add
  `glrender/preprocess.py` njit variants where profiling asks.

## Known parity risks (expected, tolerance-based)

1. **Wall distance lighting**: per-column vs interpolated per-vertex
   light ramp (mitigated by Phase-1 vertex keys on quads).
2. **Fuzz (spectres)**: could not be a pixel-identical index transform
   in a fragment program.
3. **Sky**: column-driven software span vs textured surface; UV mapping
   chosen to match `ANGLETOSKYSHIFT` parallax at the horizon.
4. **Span-boundary teers**: software `MAXOPENINGS`/clip artifacts that
   are bugs, not features — GL is allowed to be *cleaner* here.
5. Intermission/statusbar/menu art remain 320x200 nearest-scaled (as
   today), never re-rendered natively.

## Commit sequence (each lands independently-testable on this branch)

1. `video api` setting + headless fallback plumbing (`glrender/state.py`
   returns None, viewer chooses window path).
2. Palette-index texture builders + colormap/palette LUT upload
   (`glrender/textures.py`, `light.py`).
3. Wall-quad preprocess + first triangles replacing `_draw_column` for
   single-sided walls (parity red/green on E1M1).
4. Planes + flats (+ animated flat layer swap).
5. Two-sided masked mids + sprites + fuzz + weapon psprite.
6. Sky.
7. Sky/flash overlay compositor (menu/HUD/statusbar/melt on overlay
   plane).
8. Parity gate tests + fallback tests + optional CI GL smoke job.

Merge policy: back to `main` only when the parity gate passes and the
setting defaults to `software`.