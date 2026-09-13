"""Tests for mobjs.py: spawn, thing links, thinker-lite, collisions."""

import os

import numpy as np
import pytest

from pydoom.automap import thing_degrees_to_bam
from pydoom.info import MF_FLAGS
from pydoom.mapdata import Map
from pydoom.mobjs import (
    ThingIndex,
    level_totals,
    set_mobj_state,
    spawn_map,
    spawn_mobj,
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


@requires_wad
def test_corpse_rides_lift_down(setup):
    """Stationary bodies track moving platforms instead of hovering."""
    from pydoom.combat import damage_mobj, register_combat_actions
    from pydoom.doors import World
    from pydoom.info import MT_INDEX
    from pydoom.textures import TextureManager
    register_combat_actions()
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, TextureManager(wad))
    from pydoom.ai import AIContext
    ctx = AIContext(physics=phys, world=world, players=[])
    ctx.mobjs = []
    line = game_map.lines[195]
    assert line.special == 88
    sec = game_map.sectors[70]
    corpse = spawn_mobj(game_map, phys, index, 3546 << 16, -3872 << 16, -1,
                        MT_INDEX["POSSESSED"])
    assert corpse.sector is sec
    damage_mobj(corpse, None, None, 1000, ctx)
    assert corpse.health <= 0 and corpse.z == sec.floorheight
    top = sec.floorheight
    assert world.do_plat(line, "downWaitUpStay", 0)
    for _ in range(120):
        world.tick()
        think_mobj(corpse, phys, ctx)
        assert corpse.z <= top  # never hovers above the start height
    assert sec.floorheight < top  # the lift really moved underneath
    for _ in range(60):
        world.tick()
        think_mobj(corpse, phys, ctx)
    assert corpse.z == sec.floorheight  # ...and the body landed on it


@requires_wad
def test_nightmare_spawns_alert_and_hard_cast():
    from pydoom.info import MT_INDEX
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    for skill, want_rifle in (("normal", 8), ("nightmare", 0)):
        phys = Physics(game_map)
        index = ThingIndex(game_map)
        phys.things = index
        mobjs = spawn_map(game_map, phys, index, skill)
        zombies = [mo for mo in mobjs if mo.type == MT_INDEX["POSSESSED"]]
        assert zombies
        assert all(z.reactiontime == want_rifle for z in zombies)
        rifles = [mo for mo in mobjs if mo.doomednum == 9]
        assert (len(rifles) > 0) == (skill == "nightmare")


@requires_wad
def test_nightmare_corpse_returns(monkeypatch):
    import pydoom.mobjs as mobjs_mod
    from pydoom.ai import AIContext
    from pydoom.combat import damage_mobj, register_combat_actions
    from pydoom.doors import World
    from pydoom.info import MT_INDEX
    from pydoom.textures import TextureManager
    register_combat_actions()
    monkeypatch.setattr(mobjs_mod, "p_random", lambda: 0)
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, TextureManager(wad))
    ctx = AIContext(physics=phys, world=world, players=[])
    ctx.mobjs = []
    ctx.skill = "nightmare"
    start = next(t for t in game_map.things if t.type == 1)
    dummy = spawn_mobj(game_map, phys, index, start.x << 16,
                       start.y << 16, 0, MT_INDEX["PLAYER"])
    dummy.is_player = True
    ctx.players = [dummy]
    ctx.mobjs.append(dummy)
    zombie = spawn_mobj(game_map, phys, index, 2272 << 16, -2352 << 16, -1,
                        MT_INDEX["POSSESSED"])
    zombie.sector = phys.subsector_at(zombie.x, zombie.y).sector
    from types import SimpleNamespace
    zombie.spawnpoint = SimpleNamespace(x=2272, y=-2352, angle=180,
                                        options=15)
    ctx.mobjs = [zombie]
    damage_mobj(zombie, None, None, 1000, ctx)
    assert zombie.health <= 0
    for _ in range(600):
        world.tick()
        for mo in list(ctx.mobjs):
            think_mobj(mo, phys, ctx)
    risen = [mo for mo in ctx.mobjs
             if mo.type == MT_INDEX["POSSESSED"] and mo.health > 0]
    assert len(risen) == 1  # NOTE: back at its spawnpoint, fogged
    assert (risen[0].x, risen[0].y) == (2272 << 16, -2352 << 16)
    assert risen[0].reactiontime == 18
    assert zombie.dead  # NOTE: the old body is gone


@requires_wad
def test_quiet_skills_leave_corpses_down():
    from pydoom.ai import AIContext
    from pydoom.combat import damage_mobj, register_combat_actions
    from pydoom.doors import World
    from pydoom.info import MT_INDEX
    from pydoom.textures import TextureManager
    register_combat_actions()
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, TextureManager(wad))
    ctx = AIContext(physics=phys, world=world, players=[])
    ctx.mobjs = []
    zombie = spawn_mobj(game_map, phys, index, 2272 << 16, -2352 << 16, -1,
                        MT_INDEX["POSSESSED"])
    zombie.sector = phys.subsector_at(zombie.x, zombie.y).sector
    ctx.mobjs = [zombie]
    damage_mobj(zombie, None, None, 1000, ctx)
    for _ in range(2000):
        world.tick()
        think_mobj(zombie, phys, ctx)
    assert zombie.health <= 0  # NOTE: normal skill, stays dead
    assert not [mo for mo in ctx.mobjs
                if mo.type == MT_INDEX["POSSESSED"] and mo.health > 0]


@requires_wad
def test_level_totals_match_spawned_counts():
    from pydoom.info import MF_FLAGS
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    mobjs = spawn_map(game_map, phys, index, "normal")
    kills, items, secrets = level_totals(mobjs, game_map.sectors)
    assert kills == sum(1 for mo in mobjs
                        if mo.flags & MF_FLAGS["MF_COUNTKILL"]) > 0
    assert items == sum(1 for mo in mobjs
                        if mo.flags & MF_FLAGS["MF_COUNTITEM"]) > 0
    assert secrets == sum(1 for s in game_map.sectors
                          if s.special == 9) > 0
