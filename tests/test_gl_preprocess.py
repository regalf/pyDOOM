"""Tests for glrender/preprocess.py (milestone H, phase 1, walls slice).

Tier selection must match renderer._store_wall_range verbatim; the
strongest pin renders E1M1 through the software raster, logs every
tier it actually draws, and requires the preprocessed quads to agree
on texture and texturemid exactly.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.preprocess import TIERS, build_walls
from pydoom.mapdata import (
    ML_DONTPEGBOTTOM,
    ML_DONTPEGTOP,
    Line,
    Map,
    Sector,
    Seg,
    Side,
    Vertex,
)

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def load_e1m1():
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    return wad, texman, game_map, sky


def mini_world(texman, front=(0, 128, 160), back=None, side_kw=None,
               flags=0, v1=(0, 0), v2=(128, 0)):
    """One-seg synthetic map (fixed-point dataclasses, real texman).

    front/back are (floor, ceil, light); side_kw sets texture numbers
    and offsets on the front sidedef.
    """
    side_kw = side_kw or {}

    def sec(spec):
        floor, ceil, light = spec
        return Sector(floorheight=floor << 16, ceilingheight=ceil << 16,
                      floorpic=0, ceilingpic=1, lightlevel=light)

    fsec = sec(front)
    bsec = sec(back) if back is not None else None
    mid = texman.texture_num_for_name("STARTAN3")
    side = Side(textureoffset=0, rowoffset=0, toptexture=0,
                midtexture=0, bottomtexture=0, sector=fsec)
    for key, val in side_kw.items():
        if val == "MID":
            val = mid
        setattr(side, key, val)
    vv1 = Vertex(x=v1[0] << 16, y=v1[1] << 16)
    vv2 = Vertex(x=v2[0] << 16, y=v2[1] << 16)
    line = Line(v1=vv1, v2=vv2, flags=flags, frontsector=fsec,
                backsector=bsec)
    seg = Seg(v1=vv1, v2=vv2, angle=0, offset=0, linedef=line,
              sidenum=0, sidedef=side, frontsector=fsec,
              backsector=bsec)
    game_map = Map(marker="MINI", vertexes=[vv1, vv2],
                   sectors=[fsec] + ([bsec] if bsec else []),
                   sides=[side], lines=[line], segs=[seg])
    return game_map, mid


def test_build_walls_needs_no_gl():
    """Preprocess imports no GL bindings (order-independent: only new
    modules appearing during the build itself are checked)."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    before = set(sys.modules)
    build_walls(game_map, texman, sky)
    new_gl = {m.split(".")[0] for m in set(sys.modules) - before}
    assert not (new_gl & {"OpenGL", "OpenGL_accelerate", "moderngl"})


@requires_wad
def test_e1m1_quad_invariants():
    _, texman, game_map, sky = load_e1m1()
    quads = build_walls(game_map, texman, sky).quads
    assert quads
    ntex = len(texman.textures)
    for q in quads:
        assert q.tier in TIERS
        assert 0 < q.texnum < ntex
        assert q.z_top > q.z_bottom
        assert q.tweak in (-1, 0, 1)  # NOTE: static orient tweak
        assert 0 <= q.sector < len(game_map.sectors)  # NOTE: base
        # lightnum rides the sector-light texture now
        base = min(max(game_map.sectors[q.sector].lightlevel >> 4,
                       0), 15)
        assert 0 <= min(max(base + q.tweak, 0), 15) <= 15
        seg = game_map.segs[q.seg]
        length = ((seg.v2.x - seg.v1.x) ** 2
                  + (seg.v2.y - seg.v1.y) ** 2) ** 0.5 / 65536.0
        assert q.u2 - q.u1 == pytest.approx(length, abs=1e-3)


@requires_wad
def test_e1m1_golden_tier_counts():
    """Regression tripwire: any tier-rule change moves these."""
    from collections import Counter
    _, texman, game_map, sky = load_e1m1()
    counts = Counter(q.tier for q in
                     build_walls(game_map, texman, sky).quads)
    assert dict(counts) == {"mid": 335, "bottom": 115, "top": 96,
                            "masked": 13}


@requires_wad
def test_to_arrays_shape_and_index():
    _, texman, game_map, sky = load_e1m1()
    geo = build_walls(game_map, texman, sky)
    arr = geo.to_arrays()
    n = len(geo.quads)
    assert arr["positions"].shape == (n * 4, 3)
    assert arr["index"].shape == (n * 6,)
    assert arr["index"].max() < n * 4
    assert str(arr["positions"].dtype) == "float32"
    # NOTE: first triangle of quad 0 references its own four verts.
    assert list(arr["index"][:6]) == [0, 1, 2, 0, 2, 3]
    assert arr["sector"].shape == (n * 4,)
    assert arr["tweak"].shape == (n * 4,)
    assert set(arr["tweak"].tolist()) <= {0.0, 1.0, 2.0}  # tweak+1


@requires_wad
def test_matches_software_tiers_and_texturemid():
    """Every tier the software raster draws on real views must exist
    in the preprocessed set with the same texture and texturemid."""
    from pydoom.renderer import Renderer
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    quads = {(q.seg, q.tier): q
             for q in build_walls(game_map, texman, sky).quads}
    logged: dict = {}

    orig = Renderer._render_seg_loop

    def rec(self, seg, *args):
        # NOTE: positional layout of _render_seg_loop (mid/top/bottom
        # are args 12-14, maskedtexture is arg 24); fails loudly if
        # the software signature ever moves. texturemid is stored
        # view-independent (T_static + rowoffset): viewz changes with
        # every viewpoint, so raw texturemid values are incomparable
        # across renders.
        si = game_map.segs.index(seg)
        entry = logged.setdefault(si, {})
        if args[12]:
            entry["mid"] = (args[12], args[15] + self.viewz)
        if args[13]:
            entry["top"] = (args[13], args[16] + self.viewz)
        if args[14]:
            entry["bottom"] = (args[14], args[17] + self.viewz)
        if args[24]:
            entry["masked"] = (seg.sidedef.midtexture, None)
        return orig(self, seg, *args)

    renderer = Renderer(wad, texman)
    Renderer._render_seg_loop = rec
    try:
        # NOTE: spread viewpoints across the map (every Nth thing) at
        # two angles each: rooms, corridors, doors and windows all get
        # drawn, so most tier kinds meet the software raster.
        things = game_map.things
        step = max(1, len(things) // 12)
        for t in things[::step][:12]:
            for i in range(2):
                renderer.render_view(
                    game_map, t.x << 16, t.y << 16,
                    (i * 0x80000000) & 0xFFFFFFFF, mobjs=[])
    finally:
        Renderer._render_seg_loop = orig
    assert logged, "software drew nothing to compare against"
    checked = 0
    for si, tiers in logged.items():
        for tier, (texnum, texturemid) in tiers.items():
            quad = quads.get((si, tier))
            assert quad is not None, (si, tier)
            assert quad.texnum == texnum, (si, tier)
            if texturemid is not None:
                # NOTE: software texturemid is view-relative
                # (T_static + rowoffset - viewz); the log already added
                # viewz back, so this is an exact integer check.
                assert texturemid == int(quad.texbase * 65536.0), \
                    (si, tier)
            checked += 1
    assert checked > 100, checked  # NOTE: both views drew real walls


@requires_wad
def test_single_sided_mid_pegging():
    from pydoom.textures import texture_height_fixed
    _, texman, _, _ = load_e1m1()
    theight = texture_height_fixed(
        texman.textures[texman.texture_num_for_name("STARTAN3")])
    game_map, _ = mini_world(texman, side_kw={"midtexture": "MID"})
    (quad,) = build_walls(game_map, texman, 999).quads
    assert (quad.tier, quad.z_bottom, quad.z_top) == ("mid", 0.0, 128.0)
    assert quad.texbase == pytest.approx(128.0)  # NOTE: pegged top
    game_map, _ = mini_world(texman, side_kw={"midtexture": "MID"},
                             flags=ML_DONTPEGBOTTOM)
    (quad,) = build_walls(game_map, texman, 999).quads
    assert quad.texbase == pytest.approx(theight / 65536.0)
    game_map, _ = mini_world(texman, side_kw={"midtexture": "MID",
                                              "rowoffset": 5 << 16})
    (quad,) = build_walls(game_map, texman, 999).quads
    assert quad.texbase == pytest.approx(133.0)


@requires_wad
def test_two_sided_top_bottom_tiers():
    from pydoom.textures import texture_height_fixed
    _, texman, _, _ = load_e1m1()
    toptex = texman.texture_num_for_name("STARTAN3")
    theight = texture_height_fixed(texman.textures[toptex]) / 65536.0
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(16, 64, 160),
                             side_kw={"toptexture": "MID",
                                      "bottomtexture": "MID"})
    by_tier = {q.tier: q
               for q in build_walls(game_map, texman, 999).quads}
    assert set(by_tier) == {"top", "bottom"}
    assert (by_tier["top"].z_bottom, by_tier["top"].z_top) == (64.0,
                                                               128.0)
    assert by_tier["top"].texbase == pytest.approx(64.0 + theight)
    assert (by_tier["bottom"].z_bottom,
            by_tier["bottom"].z_top) == (0.0, 16.0)
    assert by_tier["bottom"].texbase == pytest.approx(16.0)
    # NOTE: DONTPEGTOP anchors the top at the front ceiling...
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(16, 64, 160),
                             side_kw={"toptexture": "MID"},
                             flags=ML_DONTPEGTOP)
    (quad,) = build_walls(game_map, texman, 999).quads
    assert quad.texbase == pytest.approx(128.0)
    # NOTE: ...while DONTPEGBOTTOM anchors the bottom at the front
    # ceiling too (vanilla quirk, kept verbatim).
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(16, 64, 160),
                             side_kw={"bottomtexture": "MID"},
                             flags=ML_DONTPEGBOTTOM)
    (quad,) = build_walls(game_map, texman, 999).quads
    assert quad.texbase == pytest.approx(128.0)


@requires_wad
def test_masked_mid_rule():
    from pydoom.textures import texture_height_fixed
    _, texman, _, _ = load_e1m1()
    theight = texture_height_fixed(
        texman.textures[texman.texture_num_for_name("STARTAN3")])
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(16, 64, 160),
                             side_kw={"midtexture": "MID"})
    (quad,) = build_walls(game_map, texman, 999).quads
    assert quad.tier == "masked"
    assert (quad.z_bottom, quad.z_top) == (16.0, 64.0)
    assert quad.texbase == pytest.approx(64.0)  # NOTE: lower ceiling
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(16, 64, 160),
                             side_kw={"midtexture": "MID"},
                             flags=ML_DONTPEGBOTTOM)
    (quad,) = build_walls(game_map, texman, 999).quads
    assert quad.texbase == pytest.approx(16.0 + theight / 65536.0)


@requires_wad
def test_sky_hack_kills_top():
    _, texman, _, sky = load_e1m1()
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(0, 64, 160),
                             side_kw={"toptexture": "MID"})
    game_map.sectors[0].ceilingpic = sky
    game_map.sectors[1].ceilingpic = sky
    assert build_walls(game_map, texman, sky).quads == []
    game_map.sectors[1].ceilingpic = 1  # NOTE: one sky only: top stays
    assert [q.tier for q in
            build_walls(game_map, texman, sky).quads] == ["top"]


@requires_wad
def test_closed_door_masked_skipped():
    """Closed door (back ceil at front floor): no opening, so no
    masked quad; the solid face still draws its top tier."""
    _, texman, _, _ = load_e1m1()
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(0, 0, 160),
                             side_kw={"toptexture": "MID",
                                      "midtexture": "MID"})
    assert [q.tier for q in
            build_walls(game_map, texman, 999).quads] == ["top"]


@requires_wad
def test_single_sided_mids_flagged_double_sided():
    """Single-sided mids (no back sector) render double-sided
    (vanilla draws their backs mirrored); every other tier has a
    partner seg covering the back and stays backface-culled."""
    _, texman, _, _ = load_e1m1()
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             side_kw={"midtexture": "MID"})
    quads = build_walls(game_map, texman, 999).quads
    assert len(quads) == 1 and quads[0].tier == "mid"
    assert quads[0].twosided is False
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(0, 128, 160),
                             side_kw={"midtexture": "MID"})
    quads = build_walls(game_map, texman, 999).quads
    # NOTE: full opening: masked mid only, still two-sided
    assert [(q.tier, q.twosided) for q in quads] == [("masked",
                                                     True)]
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             back=(0, 0, 160),
                             side_kw={"toptexture": "MID",
                                      "midtexture": "MID"})
    quads = build_walls(game_map, texman, 999).quads
    assert [(q.tier, q.twosided) for q in quads] == [("top", True)]


@requires_wad
def test_light_orientation_tweak():
    """Orient tweak stays static (-1/0/+1); the sector base rides the
    sector-light texture (row = base + tweak, clamped, as before:
    10-1=9, 10+1=11, 0-1->0)."""
    from pydoom.glrender.dynamic import sector_light_bases
    _, texman, _, _ = load_e1m1()
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             side_kw={"midtexture": "MID"},
                             v1=(0, 0), v2=(128, 0))
    q = build_walls(game_map, texman, 999).quads[0]
    assert (q.sector, q.tweak) == (0, -1)
    assert sector_light_bases(game_map)[0] == 10
    game_map, _ = mini_world(texman, front=(0, 128, 160),
                             side_kw={"midtexture": "MID"},
                             v1=(0, 0), v2=(0, 128))
    q = build_walls(game_map, texman, 999).quads[0]
    assert (q.sector, q.tweak) == (0, 1)
    game_map, _ = mini_world(texman, front=(0, 128, 8),
                             side_kw={"midtexture": "MID"},
                             v1=(0, 0), v2=(128, 0))
    q = build_walls(game_map, texman, 999).quads[0]
    assert (q.sector, q.tweak) == (0, -1)
    base = sector_light_bases(game_map)[0]
    assert base == 0 and min(max(base - 1, 0), 15) == 0


@requires_wad
def test_wall_normals_face_front():
    """Front-unit normals (front is RIGHT of v1->v2): east-running
    segs face south, north-running segs face east."""
    _, texman, _, _ = load_e1m1()
    game_map, _ = mini_world(texman, side_kw={"midtexture": "MID"},
                             v1=(0, 0), v2=(128, 0))
    quad = build_walls(game_map, texman, 999).quads[0]
    assert (quad.nx, quad.ny) == pytest.approx((0.0, -1.0))
    game_map, _ = mini_world(texman, side_kw={"midtexture": "MID"},
                             v1=(0, 0), v2=(0, 128))
    quad = build_walls(game_map, texman, 999).quads[0]
    assert (quad.nx, quad.ny) == pytest.approx((1.0, 0.0))
    arr = build_walls(game_map, texman, 999).to_arrays()
    assert arr["normal"].shape == (4, 2)
