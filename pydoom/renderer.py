"""Software wall renderer: frame setup, BSP traversal, walls, lighting.

Ports R_SetupFrame / R_RenderPlayerView (r_main.c), the BSP walker and
clipper (r_bsp.c: R_RenderBSPNode, R_AddLine, R_ClipSolidWallSegment,
R_ClipPassWallSegment, R_CheckBBox), the wall tiers (r_segs.c:
R_StoreWallRange, R_RenderSegLoop) and the column drawer (r_draw.c:
R_DrawColumn). Lighting tables come from R_InitLightTables /
R_ExecuteSetViewSize.

Scope of this step (documented, not silent):

* Opaque walls only (single-sided mid, two-sided top/bottom) with
  distance lighting. No floors/ceilings (r_plane.c), no masked mid
  textures, no sprites, no sky rendering: uncovered pixels stay black.
* Fixed 320x200 fullscreen view (detail 0, blocks 11). No status bar
  reduction, no low-detail mode.
* ``texturetranslation``/``flattranslation`` are identity (no level
  animation yet). ``extralight``/``fixedcolormap`` are 0.
* The framebuffer is a NumPy array of palette indices; PLAYPAL mapping
  happens at save/display time.
* BSP traversal uses Python recursion. Real maps are far below the
  recursion limit here.
* Two guards where the C code reads out of bounds (undefined
  behavior): R_PointToDist with dx == 0 returns 0, and the
  finetangent index is clamped to 4095.

Like the C code, rendering sets ML_MAPPED on drawn linedefs.
"""

from __future__ import annotations

import numpy as np

from pydoom import tables
from pydoom.angles import point_on_seg_side, point_on_side, point_to_angle
from pydoom.fastdraw import draw_column as _kernel_column
from pydoom.fastdraw import draw_fuzz as _kernel_fuzz
from pydoom.fastdraw import draw_span as _kernel_span
from pydoom.fixed import (
    ANG45,
    ANG90,
    ANG180,
    FRACBITS,
    FRACUNIT,
    MAXINT,
    MININT,
    c_div,
    fixed_div,
    fixed_mul,
)
from pydoom.info import (
    FF_FRAMEMASK,
    FF_FULLBRIGHT,
    MF_SHADOW,
    SPRITE_NAMES,
    spawn_visual,
)
from pydoom.mapdata import (
    ML_DONTPEGBOTTOM,
    ML_DONTPEGTOP,
    ML_MAPPED,
    NF_SUBSECTOR,
    Map,
)
from pydoom.textures import TextureManager, texture_height_fixed

__all__ = [
    "SCREENWIDTH",
    "SCREENHEIGHT",
    "VIEWHEIGHT",
    "Renderer",
]

SCREENWIDTH = 320
SCREENHEIGHT = 200
VIEWHEIGHT = 41 * FRACUNIT  # p_local.h

FIELDOFVIEW = 2048  # r_main.c, fineangle units

LIGHTLEVELS = 16
LIGHTSEGSHIFT = 4
LIGHTSCALESHIFT = 12
MAXLIGHTSCALE = 48
MAXLIGHTZ = 128
LIGHTZSHIFT = 20
NUMCOLORMAPS = 32
DISTMAP = 2

MAXDRAWSEGS = 256
MAXVISPLANES = 128  # r_plane.c
MAXVISSPRITES = 128  # r_things.c
MAXOPENINGS = SCREENWIDTH * 64  # r_plane.c (kept for the overflow check)
MINZ = 4 * FRACUNIT  # r_things.c: behind-view-plane cutoff

# R_DrawFuzzColumn table (r_draw.c): ±1 row offsets cycling per pixel.
FUZZOFFSETS = [
    1, -1, 1, -1, 1, 1, -1, 1, 1, -1, 1, 1, 1, -1, 1, 1, 1, -1, -1, -1, -1,
    1, -1, -1, 1, 1, 1, 1, -1, 1, -1, 1, 1, -1, -1, 1, 1, -1, -1, -1, -1, 1,
    1, 1, 1, -1, 1, 1, -1, 1,
]
MASKED_SENTINEL = 0x7FFF  # MAXSHORT: no masked column here

# Map thing types with no world sprite in a static view: player starts
# (rendered only as mobjs in a live game), deathmatch starts and the
# teleport destination (S_NULL spawnstate, invisible in vanilla too).
SKIP_THING_TYPES = frozenset({1, 2, 3, 4, 11, 14})

HEIGHTBITS = 12
HEIGHTUNIT = 1 << HEIGHTBITS

ANGLETOSKYSHIFT = 22  # r_sky.h
SKYTEXTUREMID = 100 * FRACUNIT  # R_InitSkyMap

SIL_NONE = 0
SIL_BOTTOM = 1
SIL_TOP = 2
SIL_BOTH = 3

_U32 = 0xFFFFFFFF

# checkcoord table from R_CheckBBox (r_bsp.c).
_CHECKCOORD = (
    (3, 0, 2, 1),
    (3, 0, 2, 0),
    (3, 1, 2, 0),
    (0, 0, 0, 0),
    (2, 0, 2, 1),
    (0, 0, 0, 0),
    (3, 1, 3, 0),
    (0, 0, 0, 0),
    (2, 0, 3, 1),
    (2, 1, 3, 1),
    (2, 1, 3, 0),
)


class _Visplane:
    """One merged floor/ceiling span list (visplane_t).

    top/bottom have two pad slots: index x+1 addresses column x, so
    the C writes to top[minx-1] and top[maxx+1] stay in bounds.
    """

    __slots__ = ("height", "picnum", "lightlevel", "minx", "maxx",
                 "top", "bottom")

    def __init__(self, height: int, picnum: int, lightlevel: int,
                 width: int = SCREENWIDTH) -> None:
        self.height = height
        self.picnum = picnum
        self.lightlevel = lightlevel
        self.minx = width
        self.maxx = -1
        self.top = [0xFF] * (width + 2)
        self.bottom = [0] * (width + 2)


def _posts_from_column(
    pixels: bytes, mask: bytes
) -> list[tuple[int, bytes]]:
    """Rebuild post runs from a decoded (pixels, mask) texture column."""
    posts: list[tuple[int, bytes]] = []
    i, n = 0, len(pixels)
    while i < n:
        if mask[i]:
            j = i
            while j < n and mask[j]:
                j += 1
            posts.append((i, pixels[i:j]))
            i = j
        else:
            i += 1
    return posts


def init_sprite_defs(wad, firstsprite: int, lastsprite: int,
                     names: list[str] = SPRITE_NAMES) -> list[list[dict]]:
    """Port of R_InitSpriteDefs: frame/rotation tables per sprite name.

    Returns, per sprite, a list of frames; each frame is a dict with
    rotate (bool), lump (8 sprite-relative lump indices) and flip
    (8 bools). PWAD override (modifiedgame) is not handled: the first
    match wins, like an unmodified game.
    """
    lump_names = [wad.lumps[i].name for i in range(firstsprite, lastsprite + 1)]

    def install(tmp: dict, maxframe: int, frame: int, rotation: int,
                li: int, flipped: bool, name: str) -> int:
        # One R_InstallSpriteLump call: rotation 0 covers all views.
        if frame >= 29 or rotation > 8 or frame < 0 or rotation < 0:
            raise ValueError(
                f"R_InstallSpriteLump: bad frame chars ({name})"
            )
        entry = tmp.setdefault(
            frame, {"rotate": None, "lump": [-1] * 8, "flip": [False] * 8}
        )
        if rotation == 0:
            if entry["rotate"] is not None:
                raise ValueError(
                    f"Sprite {name} frame {frame}: rot=0 mixed with rotations"
                )
            entry["rotate"] = False
            entry["lump"] = [li] * 8
            entry["flip"] = [flipped] * 8
        else:
            if entry["rotate"] is False:
                raise ValueError(
                    f"Sprite {name} frame {frame}: rotations mixed with rot=0"
                )
            entry["rotate"] = True
            if entry["lump"][rotation - 1] != -1:
                raise ValueError(
                    f"Sprite {name} frame {frame}: two lumps for one rotation"
                )
            entry["lump"][rotation - 1] = li
            entry["flip"][rotation - 1] = flipped
        return max(maxframe, frame)

    sprites: list[list[dict]] = []
    for name in names:
        tmp: dict[int, dict] = {}
        maxframe = -1
        for li, lumpname in enumerate(lump_names):
            if len(lumpname) < 6 or lumpname[:4] != name:
                continue
            # First pair is never flipped; an optional second pair
            # (NNNFFxFy) reuses the lump mirrored.
            maxframe = install(
                tmp, maxframe, ord(lumpname[4]) - ord("A"),
                ord(lumpname[5]) - ord("0"), li, False, lumpname,
            )
            if len(lumpname) > 6:
                if len(lumpname) < 8:
                    raise ValueError(
                        f"R_InstallSpriteLump: bad frame chars ({lumpname})"
                    )
                maxframe = install(
                    tmp, maxframe, ord(lumpname[6]) - ord("A"),
                    ord(lumpname[7]) - ord("0"), li, True, lumpname,
                )
        if maxframe == -1:
            sprites.append([])
            continue
        frames = []
        for frame in range(maxframe + 1):
            entry = tmp.get(frame)
            if entry is None or entry["rotate"] is None:
                raise ValueError(f"Sprite {name}: no patches for frame {frame}")
            if entry["rotate"] and any(v == -1 for v in entry["lump"]):
                raise ValueError(
                    f"Sprite {name} frame {frame} is missing rotations"
                )
            frames.append(entry)
        sprites.append(frames)
    return sprites


class Renderer:
    """Resolution-independent state plus per-frame render state."""

    def __init__(self, wad, texman: TextureManager) -> None:
        self.wad = wad
        self.texman = texman
        self.colormaps = bytes(wad.cache_lump("COLORMAP"))
        self._cmap_np = np.frombuffer(self.colormaps, dtype=np.uint8)
        self._fuzzoff_np = np.array(FUZZOFFSETS, dtype=np.int64)
        self.skyflatnum = texman.flat_num_for_name("F_SKY1")

        self.viewwidth = SCREENWIDTH
        self.viewheight = SCREENHEIGHT
        self.centerx = self.viewwidth // 2
        self.centery = self.viewheight // 2
        self.centerxfrac = self.centerx << FRACBITS
        self.centeryfrac = self.centery << FRACBITS
        self.projection = self.centerxfrac

        self.zlight = self._init_zlight()
        self.scalelight = self._init_scalelight()
        self.viewangletox, self.xtoviewangle, self.clipangle = (
            self._init_texture_mapping()
        )
        # Sky texture for this episode (g_game.c picks SKY1/2/3/4 per
        # episode; our WAD is episode 1).
        self.skytexture = texman.texture_num_for_name("SKY1")

        # Sprite rotation tables (R_InitSprites) and shared full-width
        # clip sentinels (screenheightarray / negonearray).
        self.sprites = init_sprite_defs(
            wad, texman.firstsprite, texman.lastsprite
        )
        # NOTE: lump names (e.g. PISGA0) for psprite/weapon lookup.
        self.sprite_lump_names = [
            wad.lumps[i].name
            for i in range(texman.firstsprite, texman.lastsprite + 1)
        ]
        self._screenheight = [SCREENHEIGHT] * SCREENWIDTH
        self._negone = [-1] * SCREENWIDTH
        # Persistent fuzz table position (C static across frames).
        self._fuzzpos = 0

        # R_ExecuteSetViewSize: row distance slopes and per-column
        # perspective correction (view-size dependent only).
        self.yslope = []
        for i in range(SCREENHEIGHT):
            dy = abs(((i - self.viewheight // 2) << FRACBITS) + FRACUNIT // 2)
            self.yslope.append(
                fixed_div((self.viewwidth // 2) * FRACUNIT, dy)
            )
        self.distscale = []
        for i in range(self.viewwidth):
            # NOTE: xtoviewangle can be negative here; both C (gcc
            # arithmetic shift + symmetric cosine table) and this code
            # read finesine[2048 + (xt >> 19)], so plain >> matches.
            cosadj = abs(tables.finecosine(self.xtoviewangle[i] >> 19))
            self.distscale.append(fixed_div(FRACUNIT, cosadj))

        # Persistent span starts (C static, initialized to 0 at startup).
        self.spanstart = [0] * SCREENHEIGHT

        # Per-frame state (reset in render_view).
        self.fb: np.ndarray = np.zeros(
            (SCREENHEIGHT, SCREENWIDTH), dtype=np.uint8
        )

    # -- one-time tables (R_InitLightTables / R_ExecuteSetViewSize /
    #    R_InitTextureMapping) --

    def _init_zlight(self) -> list[list[int]]:
        table = []
        for i in range(LIGHTLEVELS):
            startmap = ((LIGHTLEVELS - 1 - i) * 2) * NUMCOLORMAPS // LIGHTLEVELS
            row = []
            for j in range(MAXLIGHTZ):
                scale = fixed_div(
                    (SCREENWIDTH // 2 * FRACUNIT), (j + 1) << LIGHTZSHIFT
                )
                scale >>= LIGHTSCALESHIFT
                level = startmap - scale // DISTMAP
                row.append(min(max(level, 0), NUMCOLORMAPS - 1))
            table.append(row)
        return table

    def _init_scalelight(self) -> list[list[int]]:
        table = []
        for i in range(LIGHTLEVELS):
            startmap = ((LIGHTLEVELS - 1 - i) * 2) * NUMCOLORMAPS // LIGHTLEVELS
            row = []
            for j in range(MAXLIGHTSCALE):
                level = (
                    startmap
                    - j * SCREENWIDTH // (self.viewwidth) // DISTMAP
                )
                row.append(min(max(level, 0), NUMCOLORMAPS - 1))
            table.append(row)
        return table

    def _init_texture_mapping(self):
        focallength = fixed_div(
            self.centerxfrac,
            tables.finetangent[tables.FINEANGLES // 4 + FIELDOFVIEW // 2],
        )
        viewangletox = [0] * (tables.FINEANGLES // 2)
        for i in range(tables.FINEANGLES // 2):
            if tables.finetangent[i] > FRACUNIT * 2:
                t = -1
            elif tables.finetangent[i] < -FRACUNIT * 2:
                t = self.viewwidth + 1
            else:
                t = fixed_mul(tables.finetangent[i], focallength)
                t = (self.centerxfrac - t + FRACUNIT - 1) >> FRACBITS
                if t < -1:
                    t = -1
                elif t > self.viewwidth + 1:
                    t = self.viewwidth + 1
            viewangletox[i] = t
        xtoviewangle = [0] * (self.viewwidth + 1)
        for x in range(self.viewwidth + 1):
            i = 0
            while viewangletox[i] > x:
                i += 1
            xtoviewangle[x] = (i << 19) - ANG90  # ANGLETOFINESHIFT
        for i in range(tables.FINEANGLES // 2):
            if viewangletox[i] == -1:
                viewangletox[i] = 0
            elif viewangletox[i] == self.viewwidth + 1:
                viewangletox[i] = self.viewwidth
        return viewangletox, xtoviewangle, xtoviewangle[0]

    # -- per-frame entry point (R_RenderPlayerView, walls part) --

    def render_view(
        self,
        game_map: Map,
        x: int,
        y: int,
        angle: int,
        viewz: int | None = None,
        mobjs=None,
        extra_light: int = 0,
    ) -> np.ndarray:
        """Render opaque walls from (x, y, viewz, angle); palette indices.

        mobjs projects live mobjs (viewer); None keeps the legacy
        static projection from map things (tests). extra_light is the
        player muzzle-flash boost (A_Light1/2), added to every light
        level like vanilla, clamped as usual.
        """
        self.map = game_map
        self.viewx = x
        self.viewy = y
        self.viewangle = angle & _U32
        self.viewsin = tables.finesine[self.viewangle >> 19]
        self.viewcos = tables.finecosine(self.viewangle >> 19)
        if viewz is None:
            sub = self.point_in_subsector(x, y)
            assert sub.sector is not None
            viewz = sub.sector.floorheight + VIEWHEIGHT
        self.viewz = viewz
        self._extralight = extra_light

        self.fb = np.zeros((SCREENHEIGHT, SCREENWIDTH), dtype=np.uint8)
        self.solidsegs: list[list[int]] = [
            [-0x7FFFFFFF, -1],
            [self.viewwidth, 0x7FFFFFFF],
        ]
        self.drawsegs: list[dict] = []
        self.ceilingclip = [-1] * self.viewwidth
        self.floorclip = [self.viewheight] * self.viewwidth
        self._clear_planes()
        self.vissprites: list[dict] = []

        self._render_bsp_node(
            len(game_map.nodes) - 1 if game_map.nodes else -1
        )
        if mobjs is None:
            self._project_things()
        else:
            self.project_mobjs(mobjs)
        self._draw_planes()
        self._draw_masked()
        return self.fb

    def point_in_subsector(self, x: int, y: int):
        """R_PointInSubsector: walk the BSP to the leaf containing (x, y)."""
        return self.sector_at(self.map, x, y)

    def sector_at(self, game_map: Map, x: int, y: int):
        """R_PointInSubsector on an explicit map (camera queries)."""
        nodes = game_map.nodes
        if not nodes:
            return game_map.subsectors[0]
        nodenum = len(nodes) - 1
        while not (nodenum & NF_SUBSECTOR):
            node = nodes[nodenum]
            nodenum = node.children[point_on_side(x, y, node)]
        return game_map.subsectors[nodenum & ~NF_SUBSECTOR]

    # -- BSP traversal (R_RenderBSPNode / R_Subsector) --

    def _render_bsp_node(self, bspnum: int) -> None:
        if bspnum & NF_SUBSECTOR:
            self._subsector(0 if bspnum == -1 else bspnum & ~NF_SUBSECTOR)
            return
        bsp = self.map.nodes[bspnum]
        side = point_on_side(self.viewx, self.viewy, bsp)
        self._render_bsp_node(bsp.children[side])
        if self._check_bbox(bsp.bbox[side ^ 1]):
            self._render_bsp_node(bsp.children[side ^ 1])

    def _subsector(self, num: int) -> None:
        sub = self.map.subsectors[num]
        frontsector = sub.sector
        assert frontsector is not None
        # NOTE: sprites (R_AddSprites) belong to a later step.
        if frontsector.floorheight < self.viewz:
            self.floorplane = self._find_plane(
                frontsector.floorheight, frontsector.floorpic,
                frontsector.lightlevel,
            )
        else:
            self.floorplane = None
        if (frontsector.ceilingheight > self.viewz
                or frontsector.ceilingpic == self.skyflatnum):
            self.ceilingplane = self._find_plane(
                frontsector.ceilingheight, frontsector.ceilingpic,
                frontsector.lightlevel,
            )
        else:
            self.ceilingplane = None
        for i in range(sub.numlines):
            self._add_line(self.map.segs[sub.firstline + i])

    # -- seg clipping (R_AddLine / R_ClipSolidWallSegment /
    #    R_ClipPassWallSegment / R_CheckBBox) --

    def _add_line(self, seg) -> None:
        assert seg.v1 is not None and seg.v2 is not None
        angle1 = point_to_angle(seg.v1.x, seg.v1.y, self.viewx, self.viewy)
        angle2 = point_to_angle(seg.v2.x, seg.v2.y, self.viewx, self.viewy)

        span = (angle1 - angle2) & _U32
        if span >= ANG180:  # backface cull
            return

        self.rw_angle1 = angle1
        angle1 = (angle1 - self.viewangle) & _U32
        angle2 = (angle2 - self.viewangle) & _U32

        tspan = (angle1 + self.clipangle) & _U32
        if tspan > 2 * self.clipangle:
            tspan = (tspan - 2 * self.clipangle) & _U32
            if tspan >= span:  # totally off the left edge
                return
            angle1 = self.clipangle
        tspan = (self.clipangle - angle2) & _U32
        if tspan > 2 * self.clipangle:
            tspan = (tspan - 2 * self.clipangle) & _U32
            if tspan >= span:  # totally off the left edge
                return
            angle2 = (-self.clipangle) & _U32

        angle1 = ((angle1 + ANG90) & _U32) >> 19
        angle2 = ((angle2 + ANG90) & _U32) >> 19
        x1 = self.viewangletox[angle1]
        x2 = self.viewangletox[angle2]
        if x1 == x2:  # does not cross a pixel
            return

        frontsector = seg.frontsector
        backsector = seg.backsector
        assert frontsector is not None

        if backsector is None:
            self._clip_solid_wall_segment(seg, x1, x2 - 1)
            return
        if (
            backsector.ceilingheight <= frontsector.floorheight
            or backsector.floorheight >= frontsector.ceilingheight
        ):
            self._clip_solid_wall_segment(seg, x1, x2 - 1)  # closed door
            return
        if (
            backsector.ceilingheight != frontsector.ceilingheight
            or backsector.floorheight != frontsector.floorheight
        ):
            self._clip_pass_wall_segment(seg, x1, x2 - 1)  # window
            return
        # Reject empty trigger lines.
        if (
            backsector.ceilingpic == frontsector.ceilingpic
            and backsector.floorpic == frontsector.floorpic
            and backsector.lightlevel == frontsector.lightlevel
            and seg.sidedef is not None
            and seg.sidedef.midtexture == 0
        ):
            return
        self._clip_pass_wall_segment(seg, x1, x2 - 1)

    def _clip_solid_wall_segment(self, seg, first: int, last: int) -> None:
        segs = self.solidsegs
        i = 0
        while segs[i][1] < first - 1:
            i += 1
        if first < segs[i][0]:
            if last < segs[i][0] - 1:
                # Entirely visible: store and insert a new clippost.
                self._store_wall_range(seg, first, last)
                segs.insert(i, [first, last])
                return
            # Fragment above *start.
            self._store_wall_range(seg, first, segs[i][0] - 1)
            segs[i][0] = first
        if last <= segs[i][1]:
            return  # bottom contained in start
        start = i
        nxt = i
        while last >= segs[nxt + 1][0] - 1:
            # Fragment between two posts.
            self._store_wall_range(seg, segs[nxt][1] + 1, segs[nxt + 1][0] - 1)
            nxt += 1
            if last <= segs[nxt][1]:
                segs[start][1] = segs[nxt][1]
                del segs[start + 1 : nxt + 1]
                return
        # Fragment after *next.
        self._store_wall_range(seg, segs[nxt][1] + 1, last)
        segs[start][1] = last
        if nxt != start:
            del segs[start + 1 : nxt + 1]

    def _clip_pass_wall_segment(self, seg, first: int, last: int) -> None:
        segs = self.solidsegs
        i = 0
        while segs[i][1] < first - 1:
            i += 1
        if first < segs[i][0]:
            if last < segs[i][0] - 1:
                self._store_wall_range(seg, first, last)
                return
            self._store_wall_range(seg, first, segs[i][0] - 1)
        if last <= segs[i][1]:
            return
        while last >= segs[i + 1][0] - 1:
            self._store_wall_range(seg, segs[i][1] + 1, segs[i + 1][0] - 1)
            i += 1
            if last <= segs[i][1]:
                return
        self._store_wall_range(seg, segs[i][1] + 1, last)

    def _check_bbox(self, bbox: list[int]) -> bool:
        if self.viewx <= bbox[2]:  # BOXLEFT
            boxx = 0
        elif self.viewx < bbox[3]:  # BOXRIGHT
            boxx = 1
        else:
            boxx = 2
        if self.viewy >= bbox[0]:  # BOXTOP
            boxy = 0
        elif self.viewy > bbox[1]:  # BOXBOTTOM
            boxy = 1
        else:
            boxy = 2
        boxpos = (boxy << 2) + boxx
        if boxpos == 5:
            return True
        c = _CHECKCOORD[boxpos]
        x1, y1, x2, y2 = bbox[c[0]], bbox[c[1]], bbox[c[2]], bbox[c[3]]

        angle1 = (point_to_angle(x1, y1, self.viewx, self.viewy)
                  - self.viewangle) & _U32
        angle2 = (point_to_angle(x2, y2, self.viewx, self.viewy)
                  - self.viewangle) & _U32
        span = (angle1 - angle2) & _U32
        if span >= ANG180:  # sitting on a line
            return True
        tspan = (angle1 + self.clipangle) & _U32
        if tspan > 2 * self.clipangle:
            tspan = (tspan - 2 * self.clipangle) & _U32
            if tspan >= span:
                return False
            angle1 = self.clipangle
        tspan = (self.clipangle - angle2) & _U32
        if tspan > 2 * self.clipangle:
            tspan = (tspan - 2 * self.clipangle) & _U32
            if tspan >= span:
                return False
            angle2 = (-self.clipangle) & _U32
        angle1 = ((angle1 + ANG90) & _U32) >> 19
        angle2 = ((angle2 + ANG90) & _U32) >> 19
        sx1 = self.viewangletox[angle1]
        sx2 = self.viewangletox[angle2]
        if sx1 == sx2:
            return False
        sx2 -= 1
        start = self.solidsegs[0]
        for cand in self.solidsegs:
            if cand[1] >= sx2:
                start = cand
                break
        if sx1 >= start[0] and sx2 <= start[1]:
            return False
        return True

    # -- helpers (R_PointToDist / R_ScaleFromGlobalAngle) --

    def _point_to_dist(self, x: int, y: int) -> int:
        dx = abs(x - self.viewx)
        dy = abs(y - self.viewy)
        if dy > dx:
            dx, dy = dy, dx
        if dx == 0:
            # Guard: the C code divides by zero / indexes out of bounds
            # when a seg vertex sits exactly on the viewpoint.
            return 0
        angle = (
            tables.tantoangle[fixed_div(dy, dx) >> tables.DBITS] + ANG90
        ) >> 19  # ANGLETOFINESHIFT
        return fixed_div(dx, tables.finesine[angle])

    def _scale_from_global_angle(self, visangle: int) -> int:
        anglea = (ANG90 + (visangle - self.viewangle)) & _U32
        angleb = (ANG90 + (visangle - self.rw_normalangle)) & _U32
        sinea = tables.finesine[anglea >> 19]
        sineb = tables.finesine[angleb >> 19]
        num = fixed_mul(self.projection, sineb)
        den = fixed_mul(self.rw_distance, sinea)
        if den > num >> 16:
            scale = fixed_div(num, den)
            if scale > 64 * FRACUNIT:
                scale = 64 * FRACUNIT
            elif scale < 256:
                scale = 256
        else:
            scale = 64 * FRACUNIT
        return scale

    # -- wall range (R_StoreWallRange / R_RenderSegLoop / R_DrawColumn) --

    def _store_wall_range(self, seg, start: int, stop: int) -> None:
        if len(self.drawsegs) >= MAXDRAWSEGS:
            return  # don't overflow and crash
        assert seg.sidedef is not None and seg.linedef is not None
        assert seg.frontsector is not None
        sidedef = seg.sidedef
        linedef = seg.linedef
        frontsector = seg.frontsector
        backsector = seg.backsector

        linedef.flags |= ML_MAPPED  # mark visible for the automap

        self.rw_normalangle = (seg.angle + ANG90) & _U32
        raw = (self.rw_normalangle - self.rw_angle1) & _U32
        signed = raw if raw < 0x80000000 else raw - 0x100000000
        offsetangle = abs(signed)
        if offsetangle > ANG90:
            offsetangle = ANG90
        distangle = ANG90 - offsetangle
        hyp = self._point_to_dist(seg.v1.x, seg.v1.y)
        sineval = tables.finesine[distangle >> 19]
        self.rw_distance = fixed_mul(hyp, sineval)

        rw_x = start
        rw_stopx = stop + 1
        rw_scale = self._scale_from_global_angle(
            (self.viewangle + self.xtoviewangle[start]) & _U32
        )
        if stop > start:
            scale2 = self._scale_from_global_angle(
                (self.viewangle + self.xtoviewangle[stop]) & _U32
            )
            rw_scalestep = c_div(scale2 - rw_scale, stop - start)
        else:
            scale2 = rw_scale
            rw_scalestep = 0

        worldtop = frontsector.ceilingheight - self.viewz
        worldbottom = frontsector.floorheight - self.viewz

        midtexture = toptexture = bottomtexture = 0
        maskedtexture = False
        maskedcols: list[int] | None = None
        sprtopclip: list[int] | None = None
        sprbottomclip: list[int] | None = None
        markfloor = markceiling = False
        silhouette = SIL_NONE
        bsilheight = MAXINT
        tsilheight = MININT

        if backsector is None:
            midtexture = sidedef.midtexture
            markfloor = markceiling = True
            if linedef.flags & ML_DONTPEGBOTTOM:
                vtop = (frontsector.floorheight
                        + texture_height_fixed(self.texman.textures[midtexture]))
                rw_midtexturemid = vtop - self.viewz
            else:
                rw_midtexturemid = worldtop
            rw_midtexturemid += sidedef.rowoffset
            silhouette = SIL_BOTH
        else:
            if frontsector.floorheight > backsector.floorheight:
                silhouette = SIL_BOTTOM
                bsilheight = frontsector.floorheight
            elif backsector.floorheight > self.viewz:
                silhouette = SIL_BOTTOM
                bsilheight = MAXINT
            else:
                bsilheight = 0
            if frontsector.ceilingheight < backsector.ceilingheight:
                silhouette |= SIL_TOP
                tsilheight = frontsector.ceilingheight
            elif backsector.ceilingheight < self.viewz:
                silhouette |= SIL_TOP
                tsilheight = MININT
            else:
                tsilheight = 0
            if backsector.ceilingheight <= frontsector.floorheight:
                silhouette |= SIL_BOTTOM
                bsilheight = MAXINT
                sprbottomclip = self._negone
            if backsector.floorheight >= frontsector.ceilingheight:
                silhouette |= SIL_TOP
                tsilheight = MININT
                sprtopclip = self._screenheight

            worldhigh = backsector.ceilingheight - self.viewz
            worldlow = backsector.floorheight - self.viewz

            # Outdoor sky hack.
            if (frontsector.ceilingpic == self.skyflatnum
                    and backsector.ceilingpic == self.skyflatnum):
                worldtop = worldhigh

            markfloor = (
                worldlow != worldbottom
                or backsector.floorpic != frontsector.floorpic
                or backsector.lightlevel != frontsector.lightlevel
            )
            markceiling = (
                worldhigh != worldtop
                or backsector.ceilingpic != frontsector.ceilingpic
                or backsector.lightlevel != frontsector.lightlevel
            )
            if (backsector.ceilingheight <= frontsector.floorheight
                    or backsector.floorheight >= frontsector.ceilingheight):
                markceiling = markfloor = True  # closed door

            rw_toptexturemid = rw_bottomtexturemid = 0
            if worldhigh < worldtop:
                toptexture = sidedef.toptexture
                if linedef.flags & ML_DONTPEGTOP:
                    rw_toptexturemid = worldtop
                else:
                    vtop = (backsector.ceilingheight
                            + texture_height_fixed(
                                self.texman.textures[toptexture]))
                    rw_toptexturemid = vtop - self.viewz
            if worldlow > worldbottom:
                bottomtexture = sidedef.bottomtexture
                if linedef.flags & ML_DONTPEGBOTTOM:
                    rw_bottomtexturemid = worldtop
                else:
                    rw_bottomtexturemid = worldlow
            rw_toptexturemid += sidedef.rowoffset
            rw_bottomtexturemid += sidedef.rowoffset

            # Masked mid textures exist only on two-sided lines (opaque
            # mids belong to single-sided lines). Space for the per-column
            # texturecolumn table, drawn later by R_DrawMasked.
            if sidedef.midtexture:
                maskedtexture = True
                maskedcols = [MASKED_SENTINEL] * self.viewwidth

        segtextured = (
            midtexture | toptexture | bottomtexture | int(maskedtexture)
        )
        rw_offset = 0
        rw_centerangle = 0
        walllights = self.scalelight[0]
        if segtextured:
            off = (self.rw_normalangle - self.rw_angle1) & _U32
            if off > ANG180:
                off = (-off) & _U32
            if off > ANG90:
                off = ANG90
            sineval = tables.finesine[off >> 19]
            rw_offset = fixed_mul(hyp, sineval)
            if ((self.rw_normalangle - self.rw_angle1) & _U32) < ANG180:
                rw_offset = -rw_offset
            rw_offset += sidedef.textureoffset + seg.offset
            rw_centerangle = (
                ANG90 + self.viewangle - self.rw_normalangle
            ) & _U32

            lightnum = ((frontsector.lightlevel >> LIGHTSEGSHIFT)
                        + self._extralight)
            if seg.v1.y == seg.v2.y:
                lightnum -= 1
            elif seg.v1.x == seg.v2.x:
                lightnum += 1
            walllights = self.scalelight[
                min(max(lightnum, 0), LIGHTLEVELS - 1)
            ]

        if frontsector.floorheight >= self.viewz:
            markfloor = False  # above view plane
        if (frontsector.ceilingheight <= self.viewz
                and frontsector.ceilingpic != self.skyflatnum):
            markceiling = False  # below view plane

        worldtop >>= 4
        worldbottom >>= 4
        topstep = -fixed_mul(rw_scalestep, worldtop)
        topfrac = (self.centeryfrac >> 4) - fixed_mul(worldtop, rw_scale)
        bottomstep = -fixed_mul(rw_scalestep, worldbottom)
        bottomfrac = (self.centeryfrac >> 4) - fixed_mul(worldbottom, rw_scale)
        pixhigh = pixhighstep = pixlow = pixlowstep = 0
        if backsector is not None:
            worldhigh >>= 4
            worldlow >>= 4
            if worldhigh < worldtop:
                pixhigh = (self.centeryfrac >> 4) - fixed_mul(worldhigh, rw_scale)
                pixhighstep = -fixed_mul(rw_scalestep, worldhigh)
            if worldlow > worldbottom:
                pixlow = (self.centeryfrac >> 4) - fixed_mul(worldlow, rw_scale)
                pixlowstep = -fixed_mul(rw_scalestep, worldlow)

        # NOTE: the sprite-clipping save (openings) belongs to the
        # sprite step.
        if markceiling and self.ceilingplane is not None:
            self.ceilingplane = self._check_plane(
                self.ceilingplane, start, stop
            )
        if markfloor and self.floorplane is not None:
            self.floorplane = self._check_plane(self.floorplane, start, stop)

        self._render_seg_loop(
            seg, rw_x, rw_stopx, rw_scale, rw_scalestep,
            topfrac, topstep, bottomfrac, bottomstep,
            pixhigh, pixhighstep, pixlow, pixlowstep,
            midtexture, toptexture, bottomtexture,
            rw_midtexturemid if backsector is None else 0,
            rw_toptexturemid if backsector is not None else 0,
            rw_bottomtexturemid if backsector is not None else 0,
            segtextured, rw_offset, rw_centerangle, walllights,
            markfloor, markceiling, maskedtexture, maskedcols,
        )
        # Save sprite clipping info (used by R_DrawSprite).
        if backsector is None:
            sprtopclip = self._screenheight
            sprbottomclip = self._negone
        else:
            if ((silhouette & SIL_TOP) or maskedtexture) \
                    and sprtopclip is None:
                sprtopclip = list(self.ceilingclip)
            if ((silhouette & SIL_BOTTOM) or maskedtexture) \
                    and sprbottomclip is None:
                sprbottomclip = list(self.floorclip)
            if maskedtexture and not (silhouette & SIL_TOP):
                silhouette |= SIL_TOP
                tsilheight = MININT
            if maskedtexture and not (silhouette & SIL_BOTTOM):
                silhouette |= SIL_BOTTOM
                bsilheight = MAXINT
        self.drawsegs.append(
            {
                "curline": seg, "x1": start, "x2": stop,
                "scale1": rw_scale, "scale2": scale2,
                "scalestep": rw_scalestep, "silhouette": silhouette,
                "bsilheight": bsilheight, "tsilheight": tsilheight,
                "maskedtexturecol": maskedcols,
                "sprtopclip": sprtopclip, "sprbottomclip": sprbottomclip,
            }
        )

    def _render_seg_loop(
        self, seg, rw_x: int, rw_stopx: int, rw_scale: int, rw_scalestep: int,
        topfrac: int, topstep: int, bottomfrac: int, bottomstep: int,
        pixhigh: int, pixhighstep: int, pixlow: int, pixlowstep: int,
        midtexture: int, toptexture: int, bottomtexture: int,
        rw_midtexturemid: int, rw_toptexturemid: int, rw_bottomtexturemid: int,
        segtextured: int, rw_offset: int, rw_centerangle: int,
        walllights: list[int], markfloor: bool, markceiling: bool,
        maskedtexture: bool, maskedcols: list[int] | None,
    ) -> None:
        x = rw_x
        while x < rw_stopx:
            yl = (topfrac + HEIGHTUNIT - 1) >> HEIGHTBITS
            if yl < self.ceilingclip[x] + 1:
                yl = self.ceilingclip[x] + 1
            if markceiling and self.ceilingplane is not None:
                top = self.ceilingclip[x] + 1
                bottom = yl - 1
                if bottom >= self.floorclip[x]:
                    bottom = self.floorclip[x] - 1
                if top <= bottom:
                    self.ceilingplane.top[x + 1] = top
                    self.ceilingplane.bottom[x + 1] = bottom
            yh = bottomfrac >> HEIGHTBITS
            if yh >= self.floorclip[x]:
                yh = self.floorclip[x] - 1
            if markfloor and self.floorplane is not None:
                top = yh + 1
                bottom = self.floorclip[x] - 1
                if top <= self.ceilingclip[x]:
                    top = self.ceilingclip[x] + 1
                if top <= bottom:
                    self.floorplane.top[x + 1] = top
                    self.floorplane.bottom[x + 1] = bottom

            colormap_base = 0
            texturecolumn = 0
            if segtextured:
                angle = ((rw_centerangle + self.xtoviewangle[x]) & _U32) >> 19
                if angle > 4095:
                    angle = 4095  # guard: C would read out of bounds
                texturecolumn = rw_offset - fixed_mul(
                    tables.finetangent[angle], self.rw_distance
                )
                texturecolumn >>= FRACBITS
                index = rw_scale >> LIGHTSCALESHIFT
                if index >= MAXLIGHTSCALE:
                    index = MAXLIGHTSCALE - 1
                colormap_base = walllights[index] * 256

            if midtexture:
                self._draw_column(
                    x, yl, yh, rw_midtexturemid,
                    self.texman.get_column(midtexture, texturecolumn)[0],
                    colormap_base, rw_scale,
                )
                self.ceilingclip[x] = self.viewheight
                self.floorclip[x] = -1
            else:
                if toptexture:
                    mid = pixhigh >> HEIGHTBITS
                    pixhigh += pixhighstep
                    if mid >= self.floorclip[x]:
                        mid = self.floorclip[x] - 1
                    if mid >= yl:
                        self._draw_column(
                            x, yl, mid, rw_toptexturemid,
                            self.texman.get_column(toptexture, texturecolumn)[0],
                            colormap_base, rw_scale,
                        )
                        self.ceilingclip[x] = mid
                    else:
                        self.ceilingclip[x] = yl - 1
                elif markceiling:
                    self.ceilingclip[x] = yl - 1
                if bottomtexture:
                    mid = (pixlow + HEIGHTUNIT - 1) >> HEIGHTBITS
                    pixlow += pixlowstep
                    if mid <= self.ceilingclip[x]:
                        mid = self.ceilingclip[x] + 1
                    if mid <= yh:
                        self._draw_column(
                            x, mid, yh, rw_bottomtexturemid,
                            self.texman.get_column(bottomtexture, texturecolumn)[0],
                            colormap_base, rw_scale,
                        )
                        self.floorclip[x] = mid
                    else:
                        self.floorclip[x] = yh + 1
                elif markfloor:
                    self.floorclip[x] = yh + 1

            if maskedtexture:
                # Save texturecol for backdrawing of masked mid texture.
                assert maskedcols is not None
                maskedcols[x] = texturecolumn

            rw_scale += rw_scalestep
            topfrac += topstep
            bottomfrac += bottomstep
            x += 1

    def _draw_column(
        self, x: int, yl: int, yh: int, texturemid: int,
        source: bytes, colormap_base: int, rw_scale: int,
        iscale_override: int | None = None,
    ) -> None:
        """R_DrawColumn: scaled texel copy with lighting lookup."""
        count = yh - yl
        if count < 0 or not source:
            return
        if iscale_override is not None:
            # Sky columns use pspriteiscale directly (65536), which no
            # rw_scale maps to via 0xFFFFFFFF // rw_scale.
            iscale = iscale_override
        else:
            if rw_scale <= 0:
                return  # guard: C divides by zero (0xffffffffu/0 traps)
            iscale = 0xFFFFFFFF // rw_scale
        frac = texturemid + (yl - self.centery) * iscale
        texh = len(source)
        _kernel_column(
            self.fb, x, yl, count, frac, iscale,
            np.frombuffer(source, dtype=np.uint8),
            self._cmap_np[colormap_base : colormap_base + 256], texh,
        )

    # -- floors / ceilings (r_plane.c: visplanes, spans, sky) --

    def _clear_planes(self) -> None:
        self.visplanes: list[_Visplane] = []
        self.floorplane: _Visplane | None = None
        self.ceilingplane: _Visplane | None = None
        self._cachedheight = [0] * SCREENHEIGHT
        self._cacheddistance = [0] * SCREENHEIGHT
        self._cachedxstep = [0] * SCREENHEIGHT
        self._cachedystep = [0] * SCREENHEIGHT
        # Left-to-right mapping coefficients (viewangle dependent).
        angle = ((self.viewangle - ANG90) & _U32) >> 19
        self._basexscale = fixed_div(tables.finecosine(angle), self.centerxfrac)
        self._baseyscale = -fixed_div(tables.finesine[angle], self.centerxfrac)

    def _find_plane(self, height: int, picnum: int, lightlevel: int) -> _Visplane:
        if picnum == self.skyflatnum:
            height = 0  # all skys map together
            lightlevel = 0
        for pl in self.visplanes:
            if (pl.height == height and pl.picnum == picnum
                    and pl.lightlevel == lightlevel):
                return pl
        if len(self.visplanes) >= MAXVISPLANES:
            raise OverflowError("R_FindPlane: no more visplanes")
        pl = _Visplane(height, picnum, lightlevel)
        self.visplanes.append(pl)
        return pl

    def _check_plane(self, pl: _Visplane, start: int, stop: int) -> _Visplane:
        if start < pl.minx:
            intrl, unionl = pl.minx, start
        else:
            unionl, intrl = pl.minx, start
        if stop > pl.maxx:
            intrh, unionh = pl.maxx, stop
        else:
            unionh, intrh = pl.maxx, stop
        for x in range(intrl, intrh + 1):
            if pl.top[x + 1] != 0xFF:
                break
        else:
            pl.minx, pl.maxx = unionl, unionh
            return pl
        # Make a new visplane.
        new = _Visplane(pl.height, pl.picnum, pl.lightlevel)
        new.minx, new.maxx = start, stop
        self.visplanes.append(new)
        if len(self.visplanes) > MAXVISPLANES:
            raise OverflowError("R_FindPlane: no more visplanes")
        return new

    def _make_spans(self, x: int, t1: int, b1: int, t2: int, b2: int) -> None:
        while t1 < t2 and t1 <= b1:
            self._map_plane(t1, self.spanstart[t1], x - 1)
            t1 += 1
        while b1 > b2 and b1 >= t1:
            self._map_plane(b1, self.spanstart[b1], x - 1)
            b1 -= 1
        while t2 < t1 and t2 <= b2:
            self.spanstart[t2] = x
            t2 += 1
        while b2 > b1 and b2 >= t2:
            self.spanstart[b2] = x
            b2 -= 1

    def _map_plane(self, y: int, x1: int, x2: int) -> None:
        if self._plane_height != self._cachedheight[y]:
            self._cachedheight[y] = self._plane_height
            distance = self._cacheddistance[y] = fixed_mul(
                self._plane_height, self.yslope[y]
            )
            self._cachedxstep[y] = fixed_mul(distance, self._basexscale)
            self._cachedystep[y] = fixed_mul(distance, self._baseyscale)
        else:
            distance = self._cacheddistance[y]
        xstep = self._cachedxstep[y]
        ystep = self._cachedystep[y]

        length = fixed_mul(distance, self.distscale[x1])
        # Unsigned wrap like the C angle_t arithmetic, then table lookup.
        angle = ((self.viewangle + self.xtoviewangle[x1]) & _U32) >> 19
        xfrac = self.viewx + fixed_mul(tables.finecosine(angle), length)
        yfrac = -self.viewy - fixed_mul(tables.finesine[angle], length)

        index = distance >> 20  # LIGHTZSHIFT
        # NOTE: C indexes this table with an unsigned value; Python
        # signed wrap can hand us negatives on extreme slopes, so clamp
        # both sides instead of relying on list wrap-around.
        index = min(max(index, 0), MAXLIGHTZ - 1)
        colormap_base = self._plane_zlight[index] * 256
        self._draw_span(y, x1, x2, xfrac, yfrac, xstep, ystep, colormap_base)

    def _draw_span(
        self, y: int, x1: int, x2: int, xfrac: int, yfrac: int,
        xstep: int, ystep: int, colormap_base: int,
    ) -> None:
        """R_DrawSpan: flat texel copy over a horizontal span."""
        _kernel_span(
            self.fb, y, x1, x2, xfrac, yfrac, xstep, ystep,
            np.frombuffer(self._plane_flat, dtype=np.uint8),
            self._cmap_np[colormap_base : colormap_base + 256],
        )

    def _draw_planes(self) -> None:
        for pl in self.visplanes:
            if pl.minx > pl.maxx:
                continue
            if pl.picnum == self.skyflatnum:
                self._draw_sky_plane(pl)
                continue
            self._plane_flat = bytes(
                self.texman.get_flat(pl.picnum)
            )
            self._plane_height = abs(pl.height - self.viewz)
            light = (pl.lightlevel >> 4) + self._extralight  # LIGHTSEGSHIFT
            light = min(max(light, 0), LIGHTLEVELS - 1)
            self._plane_zlight = self.zlight[light]
            pl.top[pl.maxx + 2] = 0xFF
            pl.top[pl.minx] = 0xFF
            stop = pl.maxx + 1
            for x in range(pl.minx, stop + 1):
                self._make_spans(
                    x, pl.top[x], pl.bottom[x], pl.top[x + 1], pl.bottom[x + 1]
                )

    def _draw_sky_plane(self, pl: _Visplane) -> None:
        for x in range(pl.minx, pl.maxx + 1):
            yl = pl.top[x + 1]
            yh = pl.bottom[x + 1]
            if yl <= yh:
                # Sky wraps every 1024 angle units (>>22); the texture
                # widthmask folds it, like R_GetColumn.
                angle = ((self.viewangle + self.xtoviewangle[x]) & _U32) >> 22
                column = self.texman.get_column(self.skytexture, angle)[0]
                self._draw_column(
                    x, yl, yh, SKYTEXTUREMID, column, 0, FRACUNIT,
                    iscale_override=FRACUNIT,  # pspriteiscale, exact C value
                )

    def _draw_sky_plane(self, pl: _Visplane) -> None:
        for x in range(pl.minx, pl.maxx + 1):
            yl = pl.top[x + 1]
            yh = pl.bottom[x + 1]
            if yl <= yh:
                # Sky wraps every 1024 angle units (>>22); the texture
                # widthmask folds it, like R_GetColumn.
                angle = ((self.viewangle + self.xtoviewangle[x]) & _U32) >> 22
                column = self.texman.get_column(self.skytexture, angle)[0]
                self._draw_column(
                    x, yl, yh, SKYTEXTUREMID, column, 0, FRACUNIT,
                    iscale_override=FRACUNIT,  # pspriteiscale, exact C value
                )

    # -- masked mid textures and sprites (r_things.c: R_DrawMasked and
    #    friends; weapon psprites belong to the weapon step) --

    def _project_things(self) -> None:
        # Static-view equivalent of R_AddSprites: project every map
        # thing. (No sector thinglists or validcounts exist yet; order
        # is irrelevant because drawing sorts back-to-front.)
        # Unknown thing types are skipped (vanilla errors out in
        # P_SpawnMapThing); hanging/corpses-on-ceiling flags are
        # ignored, so everything stands on the floor.
        # NOTE: legacy path for tests; the viewer projects live mobjs.
        for thing in self.map.things:
            if thing.type in SKIP_THING_TYPES:
                continue
            visual = spawn_visual(thing.type)
            if visual is None:
                continue
            sub = self.point_in_subsector(thing.x << FRACBITS, thing.y << FRACBITS)
            sector = sub.sector
            assert sector is not None
            thing_bam = ((thing.angle % 360) * 0x100000000) // 360
            self._project_sprite(
                thing.x << FRACBITS, thing.y << FRACBITS,
                sector.floorheight, thing_bam, *visual,
                sector.lightlevel,
            )

    def project_mobjs(self, mobjs) -> None:
        """Project live mobjs (R_AddSprites over real things)."""
        for mo in mobjs:
            if mo.dead or mo.state == 0:  # NOTE: S_NULL never draws
                continue  # (teleport destinations stay invisible)
            lightlevel = (mo.sector.lightlevel if mo.sector is not None
                          else 0)
            self._project_sprite(mo.x, mo.y, mo.z, mo.angle, mo.sprite,
                                 mo.frame, mo.flags, lightlevel)

    def sprite_num_for_base(self, base: str, frame: str = "A") -> int | None:
        """First sprite index whose lump is base+frame+rotation0."""
        want = (base + frame + "0").upper()
        for i, name in enumerate(self.sprite_lump_names):
            if name.upper() == want:
                return i
        return None

    def draw_psprite(self, fb, base: str, bobx: int = 0,
                     boby: int = 0, frame: str = "A") -> bool:
        """R_DrawPSprite lite: blit a weapon sprite (flipped, vanilla
        anchor: x0 = 1+bobx-leftoffset, y0 = 32+boby-topoffset, from
        psp->sx/sy with WEAPONTOP). Frame B lumps back the firing kick;
        missing sprites (plasma/BFG in shareware) skip silently."""
        spritenum = self.sprite_num_for_base(base, frame)
        if spritenum is None:
            return False
        patch = self.texman.get_sprite_patch(spritenum)
        # NOTE: R_DrawPSprite uses the frame flip flag, false for all
        # rotation-0 weapon lumps: unlike V_DrawPatchFlipped art, guns
        # draw unmirrored so multi-frame kicks stay aligned.
        x0 = 1 + bobx - patch.leftoffset
        y0 = 32 + boby - patch.topoffset
        height = patch.height
        for sx in range(patch.width):
            dx = x0 + sx
            if dx < 0 or dx >= SCREENWIDTH:
                continue
            pixels, mask = patch.column_pixels(sx)
            for sy in range(height):
                if not mask[sy]:
                    continue
                dy = y0 + sy
                if 0 <= dy < SCREENHEIGHT:
                    fb[dy, dx] = pixels[sy]
        return True

    def _project_sprite(self, px: int, py: int, z: int, angle_bam: int,
                        sprite: int, frame: int, flags: int,
                        lightlevel: int) -> None:
        tr_x = px - self.viewx
        tr_y = py - self.viewy

        gxt = fixed_mul(tr_x, self.viewcos)
        gyt = -fixed_mul(tr_y, self.viewsin)
        tz = gxt - gyt
        if tz < MINZ:  # behind view plane
            return
        xscale = fixed_div(self.projection, tz)

        gxt = -fixed_mul(tr_x, self.viewsin)
        gyt = fixed_mul(tr_y, self.viewcos)
        tx = -(gyt + gxt)
        if abs(tx) > (tz << 2):  # too far off the side
            return

        sprframes = self.sprites[sprite]
        if (frame & FF_FRAMEMASK) >= len(sprframes):
            return  # guard: vanilla errors out here
        sprframe = sprframes[frame & FF_FRAMEMASK]
        if sprframe["rotate"]:
            ang = point_to_angle(px, py, self.viewx, self.viewy)
            rot = ((ang - angle_bam + (ANG45 // 2) * 9) & _U32) >> 29
            lump = sprframe["lump"][rot]
            flip = sprframe["flip"][rot]
        else:
            lump = sprframe["lump"][0]
            flip = sprframe["flip"][0]

        patch = self.texman.get_sprite_patch(lump)
        sprwidth = patch.width << FRACBITS
        tx -= patch.leftoffset << FRACBITS
        x1 = (self.centerxfrac + fixed_mul(tx, xscale)) >> FRACBITS
        if x1 > self.viewwidth:  # off the right side
            return
        tx += sprwidth
        x2 = ((self.centerxfrac + fixed_mul(tx, xscale)) >> FRACBITS) - 1
        if x2 < 0:  # off the left side
            return
        if len(self.vissprites) >= MAXVISSPRITES:
            return  # overflowsprite: extras are dropped, like vanilla

        gzt = z + (patch.topoffset << FRACBITS)
        iscale = fixed_div(FRACUNIT, xscale)
        if flip:
            startfrac = sprwidth - 1
            xiscale = -iscale
        else:
            startfrac = 0
            xiscale = iscale
        visx1 = max(x1, 0)
        if visx1 > x1:
            startfrac += xiscale * (visx1 - x1)

        if flags & MF_SHADOW:
            colormap = None  # fuzz draw
        elif frame & FF_FULLBRIGHT:
            colormap = 0
        else:
            lightnum = (lightlevel >> LIGHTSEGSHIFT) + self._extralight
            index = xscale >> LIGHTSCALESHIFT
            if index >= MAXLIGHTSCALE:
                index = MAXLIGHTSCALE - 1
            colormap = self.scalelight[
                min(max(lightnum, 0), LIGHTLEVELS - 1)
            ][index]
        self.vissprites.append(
            {
                "mobjflags": flags, "scale": xscale,
                "gx": px, "gy": py,
                "gz": z, "gzt": gzt, "texturemid": gzt - self.viewz,
                "x1": visx1, "x2": min(x2, self.viewwidth - 1),
                "startfrac": startfrac, "xiscale": xiscale,
                "patch": lump, "colormap": colormap,
            }
        )

    def _draw_masked(self) -> None:
        # Far to near (ascending scale), like R_SortVisSprites.
        for vis in sorted(self.vissprites, key=lambda v: v["scale"]):
            self._draw_sprite(vis)
        # Remaining masked mid textures, back to front.
        for ds in reversed(self.drawsegs):
            if ds["maskedtexturecol"] is not None:
                self._render_masked_seg_range(ds, ds["x1"], ds["x2"])

    def _draw_sprite(self, vis: dict) -> None:
        x1, x2 = vis["x1"], vis["x2"]
        clipbot = [-2] * self.viewwidth
        cliptop = [-2] * self.viewwidth
        # Scan drawsegs back to front for obscuring segs.
        for ds in reversed(self.drawsegs):
            if (ds["x1"] > x2 or ds["x2"] < x1
                    or (ds["silhouette"] == 0
                        and ds["maskedtexturecol"] is None)):
                continue  # does not cover sprite
            r1 = max(ds["x1"], x1)
            r2 = min(ds["x2"], x2)
            s1, s2 = ds["scale1"], ds["scale2"]
            scale = max(s1, s2)
            lowscale = min(s1, s2)
            if scale < vis["scale"] or (
                lowscale < vis["scale"]
                and not point_on_seg_side(
                    vis["gx"], vis["gy"], ds["curline"]
                )
            ):
                if ds["maskedtexturecol"] is not None:
                    self._render_masked_seg_range(ds, r1, r2)
                continue  # seg is behind sprite
            silhouette = ds["silhouette"]
            if vis["gz"] >= ds["bsilheight"]:
                silhouette &= ~SIL_BOTTOM
            if vis["gzt"] <= ds["tsilheight"]:
                silhouette &= ~SIL_TOP
            top = ds["sprtopclip"]
            bot = ds["sprbottomclip"]
            if silhouette == SIL_BOTTOM:
                for x in range(r1, r2 + 1):
                    if clipbot[x] == -2:
                        clipbot[x] = bot[x] if bot is not None \
                            else self.viewheight
            elif silhouette == SIL_TOP:
                for x in range(r1, r2 + 1):
                    if cliptop[x] == -2:
                        cliptop[x] = top[x] if top is not None else -1
            elif silhouette == SIL_BOTH:
                for x in range(r1, r2 + 1):
                    if clipbot[x] == -2:
                        clipbot[x] = bot[x] if bot is not None \
                            else self.viewheight
                    if cliptop[x] == -2:
                        cliptop[x] = top[x] if top is not None else -1
        for x in range(x1, x2 + 1):
            if clipbot[x] == -2:
                clipbot[x] = self.viewheight
            if cliptop[x] == -2:
                cliptop[x] = -1
        self._draw_vis_sprite(vis, clipbot, cliptop)

    def _draw_vis_sprite(
        self, vis: dict, mfloorclip: list[int], mceilingclip: list[int]
    ) -> None:
        patch = self.texman.get_sprite_patch(vis["patch"])
        iscale = abs(vis["xiscale"])
        frac = vis["startfrac"]
        spryscale = vis["scale"]
        sprtopscreen = self.centeryfrac - fixed_mul(
            vis["texturemid"], spryscale
        )
        x = vis["x1"]
        step = vis["xiscale"]
        while x <= vis["x2"]:
            tc = frac >> FRACBITS
            # Guard: vanilla trusts the math (RANGECHECK only).
            if 0 <= tc < patch.width:
                self._draw_masked_posts(
                    x, patch.columns[tc], vis["texturemid"], iscale,
                    spryscale, sprtopscreen, vis["colormap"],
                    mfloorclip, mceilingclip,
                )
            frac += step
            x += 1

    def _draw_masked_posts(
        self, x: int, posts: list[tuple[int, bytes]], texturemid: int,
        iscale: int, spryscale: int, sprtopscreen: int,
        colormap: int | None, mfloorclip: list[int],
        mceilingclip: list[int],
    ) -> None:
        """R_DrawMaskedColumn over decoded posts (mask-safe)."""
        for topdelta, pixels in posts:
            n = len(pixels)
            if not n:
                continue
            topscreen = sprtopscreen + spryscale * topdelta
            bottomscreen = topscreen + spryscale * n
            yl = (topscreen + FRACUNIT - 1) >> FRACBITS
            yh = (bottomscreen - 1) >> FRACBITS
            if yh >= mfloorclip[x]:
                yh = mfloorclip[x] - 1
            if yl <= mceilingclip[x]:
                yl = mceilingclip[x] + 1
            if yl > yh:
                continue
            if colormap is None:
                self._draw_fuzz_column(x, yl, yh)
                continue
            base = colormap * 256
            frac = (texturemid - (topdelta << FRACBITS)
                    + (yl - self.centery) * iscale)
            _kernel_column(
                self.fb, x, yl, yh - yl,
                frac, iscale,
                np.frombuffer(pixels, dtype=np.uint8),
                self._cmap_np[base : base + 256], n,
            )

    def _render_masked_seg_range(self, ds: dict, x1: int, x2: int) -> None:
        seg = ds["curline"]
        assert seg.sidedef is not None and seg.frontsector is not None
        assert seg.backsector is not None
        texnum = seg.sidedef.midtexture  # translation is identity
        lightnum = ((seg.frontsector.lightlevel >> LIGHTSEGSHIFT)
                    + self._extralight)
        if seg.v1.y == seg.v2.y:
            lightnum -= 1
        elif seg.v1.x == seg.v2.x:
            lightnum += 1
        walllights = self.scalelight[min(max(lightnum, 0), LIGHTLEVELS - 1)]

        maskedcols = ds["maskedtexturecol"]
        assert maskedcols is not None
        spryscale = ds["scale1"] + (x1 - ds["x1"]) * ds["scalestep"]
        mfloorclip = ds["sprbottomclip"]
        mceilingclip = ds["sprtopclip"]
        assert mfloorclip is not None and mceilingclip is not None

        if seg.linedef is not None and seg.linedef.flags & ML_DONTPEGBOTTOM:
            ceiling = max(seg.frontsector.floorheight,
                          seg.backsector.floorheight)
            texturemid = (ceiling + texture_height_fixed(
                self.texman.textures[texnum]) - self.viewz)
        else:
            ceiling = min(seg.frontsector.ceilingheight,
                          seg.backsector.ceilingheight)
            texturemid = ceiling - self.viewz
        texturemid += seg.sidedef.rowoffset

        for x in range(x1, x2 + 1):
            if maskedcols[x] != MASKED_SENTINEL:
                index = spryscale >> LIGHTSCALESHIFT
                if index >= MAXLIGHTSCALE:
                    index = MAXLIGHTSCALE - 1
                posts = _posts_from_column(
                    *self.texman.get_column(texnum, maskedcols[x])
                )
                topscreen = (self.centeryfrac
                             - fixed_mul(texturemid, spryscale))
                self._draw_masked_posts(
                    x, posts, texturemid, 0xFFFFFFFF // spryscale,
                    spryscale, topscreen, walllights[index],
                    mfloorclip, mceilingclip,
                )
                # Mark drawn so the final pass (and other sprites)
                # don't draw this column again, over nearer pixels.
                maskedcols[x] = MASKED_SENTINEL
            spryscale += ds["scalestep"]

    def _draw_fuzz_column(self, x: int, yl: int, yh: int) -> None:
        """R_DrawFuzzColumn: spectre shimmer from neighboring pixels."""
        if yl == 0:
            yl = 1
        if yh == self.viewheight - 1:
            yh = self.viewheight - 2
        if yh - yl < 0:
            return
        self._fuzzpos = int(
            _kernel_fuzz(
                self.fb, x, yl, yh - yl,
                self._cmap_np[6 * 256 : 7 * 256], self._fuzzoff_np,
                self._fuzzpos,
            )
        )
