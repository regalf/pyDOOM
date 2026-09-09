"""Tests for renderer.py: tables, frame rendering, determinism."""

import os

import numpy as np
import pytest

from pydoom.automap import thing_degrees_to_bam
from pydoom.fixed import ANG90
from pydoom.mapdata import ML_MAPPED, Map
from pydoom.renderer import Renderer, SCREENHEIGHT, SCREENWIDTH
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
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    start = next(t for t in game_map.things if t.type == 1)
    return renderer, game_map, start


@requires_wad
def test_mapping_tables(setup):
    renderer, _, _ = setup
    assert len(renderer.viewangletox) == 4096
    assert len(renderer.xtoviewangle) == SCREENWIDTH + 1
    assert 0 < renderer.clipangle < ANG90
    assert renderer.viewangletox[2048] == SCREENWIDTH // 2


@requires_wad
def test_render_player_view(setup):
    renderer, game_map, start = setup
    fb = renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    assert fb.shape == (SCREENHEIGHT, SCREENWIDTH)
    assert fb.dtype == np.uint8
    assert len(renderer.drawsegs) > 0
    assert int(np.count_nonzero(fb)) > 5000
    # Player looks into the level: the middle column shows wall.
    assert int(np.count_nonzero(fb[:, SCREENWIDTH // 2])) > 0
    # Rendering marks seen lines for the automap, like vanilla.
    assert any(li.flags & ML_MAPPED for li in game_map.lines)


@requires_wad
def test_render_is_deterministic(setup):
    renderer, game_map, start = setup
    args = (game_map, start.x << 16, start.y << 16,
            thing_degrees_to_bam(start.angle))
    assert np.array_equal(renderer.render_view(*args), renderer.render_view(*args))


@requires_wad
def test_point_in_subsector(setup):
    renderer, game_map, start = setup
    sub = renderer.point_in_subsector(start.x << 16, start.y << 16)
    assert sub.sector is not None
    assert 0 <= sub.firstline < len(game_map.segs)
