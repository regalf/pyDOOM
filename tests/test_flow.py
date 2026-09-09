"""Tests for episode flow (g_game.c lite): exits, BossDeath, teleport,
sector specials and stairs. Ground truth: linuxdoom-1.10 sources."""

import os

import pytest

from pydoom.ai import AIContext
from pydoom.combat import damage_mobj, register_combat_actions
from pydoom.doors import World
from pydoom.flow import next_map
from pydoom.info import MF_FLAGS, MT_INDEX
from pydoom.mapdata import Map
from pydoom.mobjs import ThingIndex, refresh_sector, spawn_map, spawn_mobj
from pydoom.mobjs import think_mobj
from pydoom.physics import Physics
from pydoom.player import PlayerState
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def load_world(marker):
    register_combat_actions()  # idempotent
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, marker)
    texman = TextureManager(wad)
    texman.resolve_map(game_map)
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, texman)
    ctx = AIContext(physics=phys, world=world, players=[],
                    sector_index={id(s): i for i, s in
                                  enumerate(game_map.sectors)})
    ctx.mobjs = []
    ctx.skyflatnum = None
    return game_map, phys, index, world, ctx


def test_next_map_routing():
    assert next_map("E1M1", False) == "E1M2"
    assert next_map("E1M7", False) == "E1M8"
    assert next_map("E1M8", False) is None  # episode complete
    assert next_map("E1M3", True) == "E1M9"  # secret exit
    assert next_map("E1M9", False) == "E1M4"  # back from secret


@requires_wad
def test_bossdeath_drops_e1m8_walls():
    game_map, phys, index, world, ctx = load_world("E1M8")
    mobjs = spawn_map(game_map, phys, index)
    ctx.mobjs = mobjs
    start = next(t for t in game_map.things if t.type == 1)
    player = spawn_mobj(game_map, phys, index, start.x << 16,
                        start.y << 16, 0, MT_INDEX["PLAYER"])
    player.is_player = True
    ctx.players = [player]
    barons = [mo for mo in mobjs if mo.type == MT_INDEX["BRUISER"]]
    assert len(barons) == 2
    wall = game_map.sectors[30]
    assert wall.floorheight == 208 << 16
    damage_mobj(barons[0], None, None, 5000, ctx)
    for _ in range(60):
        world.tick()
        for mo in list(mobjs):
            think_mobj(mo, phys, ctx)
    assert not [t for t in world.thinkers
                if getattr(t, "sector", None) is wall]  # twin still lives
    damage_mobj(barons[1], None, None, 5000, ctx)
    for _ in range(60):
        world.tick()
        for mo in list(mobjs):
            think_mobj(mo, phys, ctx)
    assert [t for t in world.thinkers
            if getattr(t, "sector", None) is wall]  # tag-666 lowering
    for _ in range(600):
        world.tick()
    assert wall.floorheight < 208 << 16


@requires_wad
def test_teleport_hops_to_tagged_pad():
    game_map, phys, index, world, ctx = load_world("E1M8")
    mobjs = spawn_map(game_map, phys, index)
    ctx.mobjs = mobjs
    pads = [mo for mo in mobjs if mo.type == MT_INDEX["TELEPORTMAN"]]
    assert pads  # destinations spawn (invisible: state S_NULL)
    assert all(mo.state == 0 for mo in pads)
    player = spawn_mobj(game_map, phys, index, 0, 0, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    line = next(li for li in game_map.lines
                if li.special == 97 and li.tag == 3)
    assert world.teleport(line, player, phys, mobjs)
    pad = next(mo for mo in pads if mo.sector is not None
               and mo.sector.tag == 3)
    assert (player.x, player.y) == (pad.x, pad.y)
    assert player.angle == pad.angle
    assert player.reactiontime == 18  # NOTE: don't move for a bit
    assert player.momx == player.momy == player.momz == 0
    fogs = [mo for mo in mobjs if mo.type == MT_INDEX["TFOG"]]
    assert len(fogs) == 2  # source + destination


@requires_wad
def test_teleport_refuses_missiles_and_backside():
    game_map, phys, index, world, ctx = load_world("E1M8")
    mobjs = spawn_map(game_map, phys, index)
    line = next(li for li in game_map.lines
                if li.special == 97 and li.tag == 3)
    player = spawn_mobj(game_map, phys, index, 0, 0, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    assert not world.teleport(line, player, phys, mobjs, side=1)
    player.flags |= MF_FLAGS["MF_MISSILE"]
    assert not world.teleport(line, player, phys, mobjs)


@requires_wad
def test_sector_damage_and_secrets():
    game_map, phys, index, world, ctx = load_world("E1M1")
    player = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    ps = PlayerState()
    world.time = 32  # NOTE: damage ticks every 32 (leveltime & 0x1f)
    slime = next(s for s in game_map.sectors if s.special == 7)
    player.sector = slime
    player.z = slime.floorheight
    world.player_in_special_sector(player, ps, ctx)
    assert player.health == 95  # nukage burns 5
    player.z = slime.floorheight + (10 << 16)  # airborne
    world.player_in_special_sector(player, ps, ctx)
    assert player.health == 95  # NOTE: falling takes nothing
    player.z = slime.floorheight
    ps.powers["ironfeet"] = 100
    world.player_in_special_sector(player, ps, ctx)
    assert player.health == 95  # suit holds
    secret = next(s for s in game_map.sectors if s.special == 9)
    player.sector = secret
    player.z = secret.floorheight
    assert world.player_in_special_sector(player, ps, ctx) == (
        "A SECRET IS REVEALED!")
    assert secret.special == 0


@requires_wad
def test_sector11_burn_requests_finale():
    game_map, phys, index, world, ctx = load_world("E1M8")
    player = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    ps = PlayerState()
    ctx.player_state = ps
    burn = next(s for s in game_map.sectors if s.special == 11)
    player.sector = burn
    player.z = burn.floorheight
    world.time = 32
    world.player_in_special_sector(player, ps, ctx)
    assert player.health == 80  # super-hellslime burns 20
    assert world.exit_kind is None  # healthy: no exit yet
    player.health = 11
    world.player_in_special_sector(player, ps, ctx)
    assert world.exit_kind == "normal"  # E1M8 burn-out: episode complete
    assert next_map("E1M8", False) is None


@requires_wad
def test_stairs_build_from_tag():
    game_map, phys, index, world, ctx = load_world("E1M8")
    line = next(li for li in game_map.lines if li.special == 7)
    assert world.use_special_line(line, 0, True) is None
    movers = [t for t in world.thinkers if type(t).__name__ == "FloorMover"]
    assert movers  # tagged sectors stepping up
    tagged = world.find_sectors_from_tag(line.tag)
    before = [s.floorheight for s in tagged]
    for _ in range(200):
        world.tick()
    after = [s.floorheight for s in tagged]
    assert any(a > b for a, b in zip(after, before))
