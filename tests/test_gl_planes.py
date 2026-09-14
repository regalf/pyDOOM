"""Tests for glrender/preprocess.py planes slice (milestone H, phase 1).

Floors/ceilings/sky come from BSP-leaf convex polygons (the engine's
own space partition), so pillars, islands, disjoint parts and id's
overlapping oddities (E3M8) triangulate with no special cases. The
strongest pins: leaf tiling is asserted inside the builder, and leaf
attribution is cross-checked against the renderer's own BSP walk.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.preprocess import SURFACES, build_planes
from pydoom.mapdata import Map

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")
REG_WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "doom.wad")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)
requires_reg = pytest.mark.skipif(
    not os.path.exists(REG_WAD_PATH), reason="doom.wad not found"
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


def test_build_planes_needs_no_gl():
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
    build_planes(game_map, sky)
    new_gl = {m.split(".")[0] for m in set(sys.modules) - before}
    assert not (new_gl & {"OpenGL", "OpenGL_accelerate", "moderngl"})


@requires_wad
def test_e1m1_plane_invariants():
    _, texman, game_map, sky = load_e1m1()
    tris = build_planes(game_map, sky).tris
    assert tris
    nflat = texman.numflats
    for t in tris:
        assert t.surface in SURFACES
        assert 0 <= t.light <= 15
        sec = game_map.sectors[t.sector]
        if t.surface == "sky":
            assert t.flat == -1 and t.light == 0
            assert sec.ceilingpic == sky or sec.floorpic == sky
            assert t.z == sec.ceilingheight / 65536.0
        else:
            assert 0 <= t.flat < nflat
            if t.surface == "floor":
                assert t.flat == sec.floorpic
                assert t.z == sec.floorheight / 65536.0
            else:
                assert t.flat == sec.ceilingpic
                assert t.z == sec.ceilingheight / 65536.0
            assert t.light == min(max(sec.lightlevel >> 4, 0), 15)
        area2 = abs((t.x2 - t.x1) * (t.y3 - t.y1)
                    - (t.y2 - t.y1) * (t.x3 - t.x1))
        assert area2 > 0  # NOTE: degenerate fan tris are skipped


@requires_wad
def test_e1m1_golden_surface_counts():
    """Regression tripwire: any plane-rule change moves these."""
    from collections import Counter
    _, _texman, game_map, sky = load_e1m1()
    counts = Counter(t.surface for t in
                     build_planes(game_map, sky).tris)
    assert dict(counts) == {"floor": 463, "ceiling": 390, "sky": 73}


@requires_wad
def test_e1m1_uv_matches_software_flat_mapping():
    """R_MapPlane negates viewy: uv holds world rows (x, -y), the
    shader mods by 64 like (xfrac>>16)&63."""
    _, _texman, game_map, sky = load_e1m1()
    geo = build_planes(game_map, sky)
    arr = geo.to_arrays()
    n = len(geo.tris)
    assert arr["positions"].shape == (n * 3, 3)
    assert arr["uv"].shape == (n * 3, 2)
    assert str(arr["positions"].dtype) == "float32"
    import numpy as np
    assert np.allclose(arr["uv"][:, 0],
                       arr["positions"][:, 0], atol=1e-6)
    assert np.allclose(arr["uv"][:, 1],
                       -arr["positions"][:, 1], atol=1e-6)


@requires_wad
def test_start_sector_floor_is_concrete():
    """E1M1 start sector: floor z=0 with the FLOOR4_8 flat."""
    from pydoom.renderer import Renderer
    wad, texman, game_map, sky = load_e1m1()
    renderer = Renderer(wad, texman)
    start = next(t for t in game_map.things if t.type == 1)
    sec = renderer.sector_at(game_map, start.x << 16,
                             start.y << 16).sector
    si = game_map.sectors.index(sec)
    floors = [t for t in build_planes(game_map, sky).tris
              if t.sector == si and t.surface == "floor"]
    assert floors
    assert {t.z for t in floors} == {0.0}
    names = {texman.wad.lumps[texman.firstflat + t.flat].name
             for t in floors}
    assert names == {"FLOOR4_8"}


@requires_wad
def test_leaves_match_renderer_attribution():
    """Every map thing sits strictly inside exactly one leaf whose
    subsector is the one the renderer's BSP walk returns (on-line
    points live on shared edges: either neighbor is correct)."""
    from fractions import Fraction

    from pydoom.renderer import Renderer
    wad, texman, game_map, sky = load_e1m1()
    build_planes(game_map, sky)  # NOTE: tiling assert runs here
    renderer = Renderer(wad, texman)
    renderer.map = game_map
    from pydoom.glrender.preprocess import _leaf_polys
    polys = _leaf_polys(game_map)

    def containing(x, y):
        hits = []
        for leaf, poly in polys.items():
            inside = False
            n = len(poly)
            for i in range(n):
                ax, ay = poly[i]
                bx, by = poly[(i + 1) % n]
                ax, ay, bx, by = (float(ax), float(ay),
                                  float(bx), float(by))
                if (ay > y) != (by > y) and x < (bx - ax) * (
                        y - ay) / (by - ay) + ax:
                    inside = not inside
            if inside:
                hits.append(leaf)
        return hits

    def on_edge(x, y, poly):
        X, Y = Fraction(x), Fraction(y)
        n = len(poly)
        for i in range(n):
            (ax, ay), (bx, by) = poly[i], poly[(i + 1) % n]
            if (bx - ax) * (Y - ay) == (by - ay) * (X - ax) \
                    and min(ax, bx) <= X <= max(ax, bx) \
                    and min(ay, by) <= Y <= max(ay, by):
                return True
        return False

    for t in game_map.things:
        hits = containing(float(t.x * 65536), float(t.y * 65536))
        assert len(hits) == 1, (t.x, t.y, hits)  # NOTE: tiling
        if renderer.point_in_subsector(t.x << 16, t.y << 16) \
                is not game_map.subsectors[hits[0]]:
            # NOTE: exactly-on-partition-line points are claimed by
            # either neighbor (shared edge); anything else is real.
            assert on_edge(t.x * 65536, t.y * 65536,
                           polys[hits[0]]), (t.x, t.y)


@requires_wad
def test_all_shareware_maps_build():
    """Every E1 map triangulates (floor+ceiling/sky coverage)."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    total = 0
    for marker in wad.list_maps():
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        sky = texman.flat_num_for_name("F_SKY1")
        tris = build_planes(game_map, sky).tris
        assert tris, marker
        total += len(tris)
    assert total > 10000


@requires_reg
def test_registered_maps_build():
    """Every E1-E3 map triangulates (local doom.wad, skipped in CI
    where only the shareware IWAD exists)."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(REG_WAD_PATH)
    texman = TextureManager(wad)
    total = 0
    for marker in wad.list_maps():
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        sky = texman.flat_num_for_name("F_SKY1")
        tris = build_planes(game_map, sky).tris
        assert tris, marker
        total += len(tris)
    assert total > 30000
