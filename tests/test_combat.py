"""Tests for combat.py: damage, missiles, hitscan, attacks."""

import os

import pytest

from pydoom.ai import AIContext
from pydoom.combat import (
    damage_mobj,
    fire_pistol,
    kill_mobj,
    line_attack,
    register_combat_actions,
    spawn_missile,
)
from pydoom.info import MF_FLAGS, MOBJ_TYPES, MT_INDEX
from pydoom.mapdata import Map
from pydoom.mobjs import (
    refresh_sector,
    spawn_map,
    spawn_mobj,
    think_mobj,
)
from pydoom.physics import Physics
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

_MF_SOLID = MF_FLAGS["MF_SOLID"]
_MF_SHOOTABLE = MF_FLAGS["MF_SHOOTABLE"]
_MF_CORPSE = MF_FLAGS["MF_CORPSE"]


@pytest.fixture()
def setup():
    # Function-scoped: kills and missiles must not leak between tests.
    from pydoom.mobjs import ThingIndex
    register_combat_actions()  # idempotent
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    ctx = AIContext(physics=phys)
    ctx.mobjs = []
    ctx.skyflatnum = None
    return game_map, phys, index, ctx


def make_duel(setup, dist_units=100):
    """Trooper facing the player dummy across open hangar floor."""
    game_map, phys, index, ctx = setup
    troop = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                       MT_INDEX["TROOP"])
    player = spawn_mobj(game_map, phys, index,
                        (900 + dist_units) << 16, -3500 << 16, 0,
                        MT_INDEX["PLAYER"])
    return troop, player, ctx, index


@requires_wad
def test_pistol_kills_trooper(setup):
    game_map, phys, index, ctx = setup
    troop, player, ctx, _ = make_duel(setup)
    player.angle = 0x80000000  # face west toward the trooper
    for _ in range(60):
        if troop.health <= 0:
            break
        fire_pistol(player, phys, index, ctx.mobjs, None, True, ctx)
    assert troop.health <= 0
    assert troop.flags & _MF_CORPSE
    assert not (troop.flags & _MF_SHOOTABLE)
    assert troop.height == 56 * 65536 // 4  # quartered corpse
    assert not troop.dead  # corpses persist


@requires_wad
def test_overkill_gibs(setup):
    game_map, phys, index, ctx = setup
    troop, _, ctx, _ = make_duel(setup)
    damage_mobj(troop, None, None, 1000, ctx)
    rec = MOBJ_TYPES[troop.type]
    assert troop.state == rec[13]  # xdeathstate


@requires_wad
def test_pain_retaliation_side_effects(setup):
    game_map, phys, index, ctx = setup
    troop, player, ctx, _ = make_duel(setup)
    troop.reactiontime = 8
    damage_mobj(troop, player, player, 5, ctx)
    assert troop.health == 55
    assert troop.reactiontime == 0  # we're awake now...
    assert troop.target is player  # ...and after this one
    # NOTE: waking may tick A_Chase synchronously (threshold 99) when
    # the pain roll does not preempt it first; stream-position dependent.
    assert troop.threshold in (99, 100)


@requires_wad
def test_missile_flies_and_explodes(setup):
    game_map, phys, index, ctx = setup
    troop, player, ctx, _ = make_duel(setup, dist_units=400)
    missile = spawn_missile(troop, player, MT_INDEX["TROOPSHOT"], phys,
                            index, ctx.mobjs)
    assert missile.momx != 0 or missile.momy != 0
    rec = MOBJ_TYPES[missile.type]
    seen = set()
    for _ in range(300):
        think_mobj(missile, phys, ctx)
        seen.add(missile.state)
        if missile.state == rec[12]:
            break
    assert rec[12] in seen  # deathstate entered (anim may advance past)


@requires_wad
def test_barrel_chain_hurts_bystander(setup):
    from pydoom.mobjs import think_mobj
    game_map, phys, index, ctx = setup
    barrel = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                        MT_INDEX["BARREL"])
    troop = spawn_mobj(game_map, phys, index, 950 << 16, -3500 << 16, 0,
                       MT_INDEX["TROOP"])
    assert troop.health == 60
    damage_mobj(barrel, None, None, 100, ctx)  # detonates (A_Explode later)
    assert barrel.health <= 0
    for _ in range(25):  # death anim reaches the A_Explode frame
        think_mobj(barrel, phys, ctx)
    assert troop.health < 60  # splash damage, deterministic amount


@requires_wad
def test_exploded_barrel_leaves_no_hitbox(setup):
    """S_NULL barrels sweep out (vanilla P_RemoveMobj): solid through
    the blast, then no phantom blocker (think_mobj + sweep_dead)."""
    from pydoom.mobjs import sweep_dead, think_mobj
    game_map, phys, index, ctx = setup
    barrel = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                        MT_INDEX["BARREL"])
    mobjs = [barrel]
    damage_mobj(barrel, None, None, 100, ctx)
    for _ in range(60):  # BEXP chain runs out to S_NULL
        think_mobj(barrel, phys, ctx)
        if barrel.dead:
            break
    assert barrel.dead and barrel.state == 0
    assert barrel.flags & _MF_CORPSE  # vanilla still flags the corpse...
    assert barrel.flags & _MF_SOLID  # ...solid through the explosion...
    walker = spawn_mobj(game_map, phys, index, 860 << 16, -3500 << 16, 0,
                        MT_INDEX["PLAYER"])
    spot = (900 << 16, -3500 << 16)
    assert not phys.check_position(walker, *spot).ok  # stub blocks
    assert sweep_dead(mobjs, index, 0)  # ...but S_NULL sweeps it
    assert mobjs == []
    assert barrel not in barrel.sector.thinglist
    assert phys.check_position(walker, *spot).ok  # pad walkable again


@requires_wad
def test_player_death_clears_solid(setup):
    game_map, phys, index, ctx = setup
    _, player, ctx, _ = make_duel(setup)
    player.is_player = True  # like the viewer body
    assert player.flags & _MF_SOLID
    damage_mobj(player, None, None, 1000, ctx)
    assert player.health <= 0
    assert not (player.flags & _MF_SOLID)  # walkable corpse


@requires_wad
def test_hitscan_wall_spawns_puff(setup):
    game_map, phys, index, ctx = setup
    _, player, ctx, _ = make_duel(setup)
    player.angle = 0x00000000  # east, at the divider pillar (trooper west)
    before = len(ctx.mobjs)
    line_attack(player, player.angle, 2048 * 65536, 0, 0, phys, index,
                ctx.mobjs, None, ctx)
    puffs = [mo for mo in ctx.mobjs[before:]
             if mo.type == MT_INDEX["PUFF"]]
    assert len(puffs) == 1


@requires_wad
def test_imp_fires_back(setup):
    # Full loop: wake on sight, chase, then hurt the player by missile
    # or melee rush (rolls decide which; missiles are covered alone in
    # test_missile_flies_and_explodes).
    from pydoom.m_random import clear_random
    clear_random()  # deterministic rolls
    game_map, phys, index, ctx = setup
    troop = spawn_mobj(game_map, phys, index, 1056 << 16, -3400 << 16, 0,
                       MT_INDEX["TROOP"])
    player = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16, 0,
                        MT_INDEX["PLAYER"])
    ctx.players = [player]
    mobjs = [troop, player]
    ctx.mobjs = mobjs
    for _ in range(400):
        for mo in list(mobjs):
            if mo is player:
                continue
            think_mobj(mo, phys, ctx)
            for extra in list(ctx.mobjs):
                if extra not in mobjs:
                    mobjs.append(extra)
        for mo in list(mobjs):
            if mo not in (troop, player) and mo.dead:
                mobjs.remove(mo)
        if player.health < 100:
            break
    assert player.health < 100  # enemy fire or melee landed


@requires_wad
def test_melee_action_hurts_in_range(setup):
    from pydoom.ai import ACTIONS
    game_map, phys, index, ctx = setup
    troop, player, ctx, _ = make_duel(setup, dist_units=30)
    troop.target = player
    trop_hp = player.health
    ACTIONS["A_SargAttack"](troop, ctx)  # demon bite works on any body
    assert player.health < trop_hp


@requires_wad
def test_blue_room_zombie_retaliates(setup):
    """E1M1 blue-room zombiemen must shoot back (user report).

    Full viewer-style gamesim: player stands in the corridor doorway,
    the (2272, -2352) zombie is wounded once, then 1500 tics run with
    doors ticking. It must enter its missile state and land hits.
    """
    from pydoom.doors import World
    from pydoom.textures import TextureManager
    game_map, phys, index, ctx = setup
    wad = WadFile(WAD_PATH)
    world = World(game_map, TextureManager(wad))
    ctx.world = world
    ctx.sector_index = {id(s): i for i, s in enumerate(game_map.sectors)}
    mobjs = spawn_map(game_map, phys, index)
    player = spawn_mobj(game_map, phys, index, 1867 << 16, -2409 << 16, 0,
                        MT_INDEX["PLAYER"])
    refresh_sector(player, phys)
    player.z = player.floorz
    mobjs.append(player)
    ctx.players = [player]
    ctx.mobjs = mobjs
    # Viewer-style roam first: the player walks across a block boundary,
    # so its blocklink must follow (stale links made monsters miss).
    for _ in range(2):
        ok, _ = phys.try_move(player, player.x - (64 << 16), player.y)
        assert ok
    zombie = min(
        (mo for mo in mobjs if mo.type == MT_INDEX["POSSESSED"]
         and abs(mo.x - (2272 << 16)) < 2 ** 17),
        key=lambda mo: abs(mo.y - (-2352 << 16)))
    damage_mobj(zombie, player, player, 5, ctx)
    assert zombie.health > 0  # wounded, not killed
    fired = False
    for _ in range(1500):
        sub = phys.subsector_at(player.x, player.y)
        world.blocker = (
            ((sub.sector,), player.z, player.height)
            if sub.sector is not None else None)
        world.tick()
        for mo in list(mobjs):
            if mo is player:
                continue
            think_mobj(mo, phys, ctx)
        if zombie.state == zombie.missilestate:
            fired = True
        if player.health < 100:
            break
    assert fired, "zombie never entered its missile state"
    assert player.health < 100, "zombie never landed a hit"


@requires_wad
def test_hitscan_needs_fresh_blocklink(setup):
    """A roamed body with a stale blocklink is invisible to hitscans.

    Reproduces the E1M1 blue-room report (wounded zombies never land a
    shot on a player who walked across the map): with the victim filed
    far off-trace, aim finds nothing; re-filed, the same shots land.
    """
    from pydoom.angles import point_to_angle2
    from pydoom.combat import aim_line_attack
    game_map, phys, index, ctx = setup
    zombie = spawn_mobj(game_map, phys, index, 2272 << 16, -2352 << 16, 0,
                        MT_INDEX["POSSESSED"])
    zombie.z = zombie.floorz
    player = spawn_mobj(game_map, phys, index, 1867 << 16, -2409 << 16, 0,
                        MT_INDEX["PLAYER"])
    player.z = player.floorz
    ctx.players = [player]
    ctx.mobjs = [zombie, player]
    zombie.angle = point_to_angle2(zombie.x, zombie.y, player.x, player.y)
    zombie.target = player
    # Stale: body here, filed 1000+ units away (pre-relink viewer roam).
    index.unlink(player)
    index.blocks.setdefault(index._key(1056 << 16, -3520 << 16), [])
    index.blocks[index._key(1056 << 16, -3520 << 16)].append(player)
    _, tgt = aim_line_attack(zombie, zombie.angle, 2048 << 16, phys, index,
                             ctx.mobjs, None)
    assert tgt is None
    before = player.health
    for _ in range(10):
        from pydoom.combat import a_posattack
        a_posattack(zombie, ctx)
    assert player.health == before  # every pellet misses the ghost
    # Fresh: re-filed under the real block, the same shots land.
    index.relink(player)
    _, tgt = aim_line_attack(zombie, zombie.angle, 2048 << 16, phys, index,
                             ctx.mobjs, None)
    assert tgt is player
    for _ in range(10):
        from pydoom.combat import a_posattack
        a_posattack(zombie, ctx)
    assert player.health < before


@requires_wad
def test_baby_halves_player_damage(setup):
    game_map, phys, index, ctx = setup
    _, player, ctx, _ = make_duel(setup)
    player.is_player = True
    ctx.skill = "baby"
    damage_mobj(player, None, None, 30, ctx)
    assert player.health == 100 - 15


@requires_wad
def test_kill_tallies_countkill(setup):
    from pydoom.player import PlayerState
    game_map, phys, index, ctx = setup
    troop, player, ctx, _ = make_duel(setup)
    ps = PlayerState()
    ctx.player_state = ps
    ctx.mobjs = [troop, player]
    damage_mobj(troop, None, None, 1000, ctx)
    assert ps.killcount == 1


@requires_wad
def test_player_corpse_thinks_screams_and_settles(setup, monkeypatch):
    """Player death (vanilla P_KillMobj path): the corpse counts its
    death states down (A_PlayerScream on DIE2), slides to a stop, and
    consumes no RNG doing it."""
    from pydoom import audio as audio_mod
    from pydoom.info import STATE_INDEX
    from pydoom.m_random import get_state
    game_map, phys, index, ctx = setup
    _, player, ctx, _ = make_duel(setup)
    player.is_player = True
    player.momx = 8 * 65536  # NOTE: dying mid-stride (slide-out)
    sounds = []
    monkeypatch.setattr(audio_mod, "play",
                        lambda n, *a: sounds.append(n) or False)
    kill_mobj(None, player, ctx)
    assert player.state == STATE_INDEX["S_PLAY_DIE1"]
    rng0 = get_state()
    for _ in range(12):  # DIE1 (10 tics) -> DIE2 entry screams
        think_mobj(player, phys, ctx)
    assert "pldeth" in sounds  # NOTE: vanilla A_PlayerScream on DIE2
    assert player.state == STATE_INDEX["S_PLAY_DIE2"]
    assert get_state() == rng0  # corpse animation draws nothing
    for _ in range(400):  # slide settles via friction, never NaN
        think_mobj(player, phys, ctx)
    assert (player.momx, player.momy) == (0, 0)
    # NOTE: vanilla idles corpses on DIE7 (tics -1), never S_NULL.
    assert player.state == STATE_INDEX["S_PLAY_DIE7"]
    assert player.tics == -1
    assert player.flags & _MF_CORPSE  # kept for crush/render
