"""Tests for mobjs.py: spawn, thing links, thinker-lite, collisions."""

import os

import numpy as np
import pytest

from pydoom.automap import thing_degrees_to_bam
from pydoom.info import MF_FLAGS
from pydoom.mapdata import Map
from pydoom.mobjs import (
    ThingIndex,
    set_mobj_state,
    spawn_map,
    think_mobj,
)
from pydoom.physics import Mover, Physics
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
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    mobjs = spawn_map(game_map, phys, index)
    renderer = Renderer(wad, texman)
    return game_map, phys, index, mobjs, renderer


@requires_wad
def test_spawn_population(setup):
    game_map, _, _, mobjs, _ = setup
    assert len(mobjs) == 91  # 138 things minus players/skill/dm-only
    assert all(mo.doomednum not in (1, 2, 3, 4, 11) for mo in mobjs)
    barrel = next(mo for mo in mobjs if mo.doomednum == 2035)
    assert barrel.radius == 10 * 65536 and barrel.height == 42 * 65536
    assert barrel.flags & MF_FLAGS["MF_SOLID"]


@requires_wad
def test_solid_blocks_walkable_passes(setup):
    _, phys, _, mobjs, _ = setup
    barrel = next(mo for mo in mobjs if mo.doomednum == 2035)
    mo = Mover(x=barrel.x, y=barrel.y - (40 << 16), z=barrel.z)
    assert phys.try_move(mo, barrel.x, barrel.y)[0] is False
    shell = next(mo for mo in mobjs if mo.doomednum == 2007)
    mo2 = Mover(x=shell.x, y=shell.y - (30 << 16), z=shell.z)
    assert phys.try_move(mo2, shell.x, shell.y)[0] is True


@requires_wad
def test_thing_links_cover_mover(setup):
    _, phys, index, mobjs, _ = setup
    barrel = next(mo for mo in mobjs if mo.doomednum == 2035)
    found = any(barrel in cell
                for cell in index.blocks.values())
    assert found
    assert barrel in barrel.sector.thinglist


@requires_wad
def test_thinker_gravity_and_states(setup):
    from pydoom.info import STATES
    from pydoom.mobjs import ThingIndex, spawn_map
    game_map, phys, _, _, _ = setup
    mobjs = spawn_map(game_map, phys, ThingIndex(game_map))
    mo = next(m for m in mobjs if m.doomednum == 2035)
    mo.z += 64 * 65536  # tossed in the air
    mo.momz = 0
    for _ in range(200):
        think_mobj(mo, phys)
    assert mo.z == mo.floorz and mo.momz == 0  # landed
    # State countdown advances without actions.
    mo.tics = 1
    think_mobj(mo, phys)
    assert mo.tics == STATES[mo.state][2]
    # S_NULL removes.
    assert set_mobj_state(mo, 0) is False
    assert mo.dead


@requires_wad
def test_mobjs_frame_matches_static_frame(setup):
    game_map, _, _, mobjs, renderer = setup
    assert all(not mo.dead for mo in mobjs)
    start = next(t for t in game_map.things if t.type == 1)
    args = (game_map, start.x << 16, start.y << 16,
            thing_degrees_to_bam(start.angle))
    fb_static = renderer.render_view(*args)
    fb_live = renderer.render_view(*args, mobjs=mobjs)
    assert np.array_equal(fb_static, fb_live)
