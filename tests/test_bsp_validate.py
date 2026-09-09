"""Cross-validate the BSP renderer with brute-force ray casting.

For sampled screen columns, an independent float ray caster finds the
nearest front-facing seg; its linedef must match the first drawseg (in
store order, i.e. front-to-back) covering that column. This checks BSP
traversal, backface culling and solid/ pass clipping without reusing
any of that machinery (only point_to_angle, already tested alone).
"""

import math
import os

import pytest

from pydoom.angles import point_to_angle
from pydoom.automap import thing_degrees_to_bam
from pydoom.fixed import ANG180
from pydoom.mapdata import Map
from pydoom.renderer import Renderer
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

_U32 = 0xFFFFFFFF


def brute_force_hit(game_map, ox, oy, bam_angle):
    """Nearest front-facing seg along the ray, or None."""
    dx = math.cos(bam_angle / 2**32 * 2 * math.pi)
    dy = math.sin(bam_angle / 2**32 * 2 * math.pi)
    best_t = float("inf")
    best_seg = None
    for seg in game_map.segs:
        assert seg.v1 is not None and seg.v2 is not None
        # Same backface cull as R_AddLine (spans use tested angle code).
        a1 = point_to_angle(seg.v1.x, seg.v1.y, ox, oy)
        a2 = point_to_angle(seg.v2.x, seg.v2.y, ox, oy)
        if (a1 - a2) & _U32 >= ANG180:
            continue
        # Reject empty trigger lines, like R_AddLine.
        front, back = seg.frontsector, seg.backsector
        if back is not None and front is not None and (
            back.ceilingheight == front.ceilingheight
            and back.floorheight == front.floorheight
            and back.ceilingpic == front.ceilingpic
            and back.floorpic == front.floorpic
            and back.lightlevel == front.lightlevel
            and seg.sidedef is not None
            and seg.sidedef.midtexture == 0
        ):
            continue
        x1, y1 = seg.v1.x / 65536, seg.v1.y / 65536
        x2, y2 = seg.v2.x / 65536, seg.v2.y / 65536
        ex, ey = x2 - x1, y2 - y1
        denom = dx * ey - dy * ex
        if abs(denom) < 1e-12:
            continue
        oxf, oyf = ox / 65536, oy / 65536
        t = ((x1 - oxf) * ey - (y1 - oyf) * ex) / denom
        u = ((x1 - oxf) * dy - (y1 - oyf) * dx) / denom
        if t > 0 and 0 <= u <= 1 and t < best_t:
            best_t = t
            best_seg = seg
    return best_seg


@requires_wad
def test_bsp_matches_brute_force():
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    start = next(t for t in game_map.things if t.type == 1)
    ox, oy = start.x << 16, start.y << 16
    line_index = {id(li): i for i, li in enumerate(game_map.lines)}

    for deg in (0, 90, 180, 270):
        angle = thing_degrees_to_bam(deg)
        renderer.viewangle = angle  # for column ray angles below
        renderer.render_view(game_map, ox, oy, angle)
        checked = 0
        for x in range(0, 320, 4):
            ray_angle = (angle + renderer.xtoviewangle[x]) & _U32
            seg = brute_force_hit(game_map, ox, oy, ray_angle)
            covering = next(
                (
                    ds
                    for ds in renderer.drawsegs
                    if ds["x1"] <= x <= ds["x2"]
                ),
                None,
            )
            if seg is None:
                assert covering is None, (deg, x)
            else:
                assert covering is not None, (deg, x)
                assert covering["curline"].linedef is seg.linedef, (
                    deg,
                    x,
                    line_index.get(id(covering["curline"].linedef)),
                    line_index.get(id(seg.linedef)),
                )
                checked += 1
        assert checked > 40, (deg, checked)
