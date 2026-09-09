"""Tests for sprite defs, projection, masked textures and fuzz."""

import math
import os

import numpy as np
import pytest

from pydoom.automap import thing_degrees_to_bam
from pydoom.mapdata import Map
from pydoom.renderer import Renderer
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@pytest.fixture(scope="module")
def setup():
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    renderer = Renderer(wad, texman)
    return wad, texman, renderer


@requires_wad
def test_sprite_defs_complete(setup):
    _, texman, renderer = setup
    assert len(renderer.sprites) == 138
    # Imp has 8 rotations on frame A...
    troo = renderer.sprites[0]
    assert troo[0]["rotate"] is True
    assert all(v != -1 for v in troo[0]["lump"])
    # ...while the barrel is a single rotation-0 lump.
    bar1 = renderer.sprites[57]
    assert bar1[0]["rotate"] is False
    assert len({v for v in bar1[0]["lump"]}) == 1


@requires_wad
def test_e1m1_projects_things(setup):
    wad, texman, renderer = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    start = next(t for t in game_map.things if t.type == 1)
    fb = renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    assert len(renderer.vissprites) > 5
    # No sprites for player starts.
    starts = {(t.x << 16, t.y << 16) for t in game_map.things
              if t.type in (1, 2, 3, 4, 11)}
    assert all((v["gx"], v["gy"]) not in starts for v in renderer.vissprites)
    # Sorted draw keeps determinism.
    again = renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    assert np.array_equal(fb, again)


@requires_wad
def test_barrel_projection_matches_float_math(setup):
    # Independent float-maths check of the fixed-point projection chain.
    wad, texman, renderer = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer.render_view(game_map, 1056 << 16, -3616 << 16,
                         thing_degrees_to_bam(90))
    vis = next(v for v in renderer.vissprites
               if (v["gx"], v["gy"]) == (864 << 16, -3328 << 16))
    patch = texman.get_sprite_patch(vis["patch"])
    dx, dy = 864.0 - 1056.0, -3328.0 + 3616.0
    a = math.pi / 2
    tz = dx * math.cos(a) + dy * math.sin(a)
    tx = dx * math.sin(a) - dy * math.cos(a)
    xscale = 160.0 / tz
    x1 = 160 + (tx - patch.leftoffset) * xscale
    x2 = 160 + (tx - patch.leftoffset + patch.width) * xscale - 1
    assert abs(vis["x1"] - x1) <= 3, (vis["x1"], x1)
    assert abs(vis["x2"] - x2) <= 3, (vis["x2"], x2)
    assert abs(vis["scale"] / 65536 - xscale) / xscale < 0.02


@requires_wad
def test_sprite_in_front_of_masked_stays_visible(setup):
    # End-to-end draw-order check: a sprite nearer than a masked
    # grate must survive the final masked pass (regression: unmarked
    # columns were redrawn OVER nearer sprites).
    wad, texman, renderer = setup
    game_map = Map.from_wad(wad, "E1M6")
    texman.resolve_map(game_map)
    start = next(t for t in game_map.things if t.type == 1)
    args = (game_map, start.x << 16, start.y << 16,
            thing_degrees_to_bam(30))
    fb_with = renderer.render_view(*args).copy()
    target = None
    for vis in renderer.vissprites:
        for ds in renderer.drawsegs:
            if ds["maskedtexturecol"] is None:
                continue
            if ds["x2"] < vis["x1"] or ds["x1"] > vis["x2"]:
                continue
            if max(ds["scale1"], ds["scale2"]) < vis["scale"]:
                target = (max(ds["x1"], vis["x1"]),
                          min(ds["x2"], vis["x2"]))
    assert target is not None  # need an overlapping pair for this test
    original = renderer._project_things
    renderer._project_things = lambda: None
    try:
        fb_without = renderer.render_view(*args)
    finally:
        renderer._project_things = original
    x1, x2 = target
    assert bool((fb_with != fb_without)[:, x1:x2 + 1].any())


@requires_wad
def test_masked_columns_marked_drawn(setup):
    # Regression: R_RenderMaskedSegRange must mark drawn columns
    # MAXSHORT, or the final pass redraws grates OVER nearer sprites.
    wad, texman, renderer = setup
    game_map = Map.from_wad(wad, "E1M6")
    texman.resolve_map(game_map)
    start = next(t for t in game_map.things if t.type == 1)
    renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    masked = [ds for ds in renderer.drawsegs
              if ds["maskedtexturecol"] is not None]
    assert masked
    for ds in masked:
        assert all(c == 0x7FFF for c in ds["maskedtexturecol"])


@requires_wad
def test_masked_frame_deterministic(setup):
    wad, texman, renderer = setup
    game_map = Map.from_wad(wad, "E1M6")
    texman.resolve_map(game_map)
    start = next(t for t in game_map.things if t.type == 1)
    fb = renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    again = renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    assert np.array_equal(fb, again)


@requires_wad
def test_fuzz_column_is_deterministic(setup):
    _, _, renderer = setup
    renderer.fb[:] = 0
    renderer.fb[5, 5] = 100
    renderer._fuzzpos = 0
    renderer._draw_fuzz_column(5, 2, 7)
    first = renderer.fb[:, 5].copy()
    assert any(first[2:8] != 0)
    renderer.fb[:] = 0
    renderer.fb[5, 5] = 100
    renderer._fuzzpos = 0
    renderer._draw_fuzz_column(5, 2, 7)
    assert np.array_equal(first, renderer.fb[:, 5])
