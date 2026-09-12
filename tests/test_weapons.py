"""Tests for weapons.py: slots, switching, CheckAmmo, per-weapon fire."""

import os

import pytest

from pydoom import weapons
from pydoom.ai import AIContext
from pydoom.combat import register_combat_actions
from pydoom.info import MT_INDEX
from pydoom.mapdata import Map
from pydoom.mobjs import ThingIndex, spawn_mobj
from pydoom.physics import Physics
from pydoom.player import (
    AM_CELL,
    AM_CLIP,
    AM_MISL,
    AM_SHELL,
    PW_STRENGTH,
    PlayerState,
    WP_BFG,
    WP_CHAINGUN,
    WP_CHAINSAW,
    WP_FIST,
    WP_MISSILE,
    WP_PISTOL,
    WP_PLASMA,
    WP_SHOTGUN,
)
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@pytest.fixture()
def setup():
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


def make_range(setup, dist_units=100):
    """Player shooter facing a trooper across open hangar floor."""
    game_map, phys, index, ctx = setup
    player = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    player.z = player.floorz
    troop = spawn_mobj(game_map, phys, index,
                       (900 + dist_units) << 16, -3500 << 16, 0,
                       MT_INDEX["TROOP"])
    troop.z = troop.floorz
    player.angle = 0  # face east toward the trooper
    player.target = troop
    ps = PlayerState()
    ctx.players = [player]
    ctx.player_state = ps
    return player, troop, ps, ctx, index


def ready_weapon(ps, weapon):
    ps.weapons |= 1 << weapon
    ps.readyweapon = weapon
    ps.pendingweapon = weapon
    ps.switchtics = 0


def fire_all(ps, player, phys, index, mobjs, skyflat, accurate, ctx):
    """Trigger pull + run out the windup (vanilla shot timing)."""
    queue = []
    cd, _flash = weapons.fire(ps, player, phys, index, mobjs, skyflat,
                              accurate, ctx, queue)
    while queue:
        weapons.tick_pending(ps, player, phys, index, mobjs, skyflat,
                             ctx, queue)
    return cd


@requires_wad
def test_request_gating_and_raise(setup):
    game_map, phys, index, ctx = setup
    ps = PlayerState()
    assert not weapons.request_weapon(ps, "3")  # shotgun not owned
    assert ps.pendingweapon == WP_PISTOL
    ps.weapons |= 1 << WP_SHOTGUN
    assert weapons.request_weapon(ps, "3")
    assert ps.pendingweapon == WP_SHOTGUN
    assert ps.readyweapon == WP_PISTOL  # still lowering
    for _ in range(weapons.SWITCH_TICS):
        weapons.tick_weapon(ps)
    assert ps.readyweapon == WP_SHOTGUN


@requires_wad
def test_key1_prefers_chainsaw(setup):
    ps = PlayerState()
    assert weapons.request_weapon(ps, "1")
    assert ps.pendingweapon == WP_FIST  # no saw yet
    ps.weapons |= 1 << WP_CHAINSAW
    assert weapons.request_weapon(ps, "1")
    assert ps.pendingweapon == WP_CHAINSAW


@requires_wad
def test_fire_during_switch_holds(setup):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup)
    ps.weapons |= 1 << WP_SHOTGUN
    ps.ammo[AM_SHELL] = 8
    weapons.request_weapon(ps, "3")
    cd, _flash = weapons.fire(ps, player, phys, index, ctx.mobjs, None,
                              True, ctx, [])
    assert cd == -1
    assert ps.ammo[AM_SHELL] == 8  # nothing spent mid-switch


@requires_wad
def test_shotgun_fires_seven_spread_pellets(setup, monkeypatch):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup)
    ready_weapon(ps, WP_SHOTGUN)
    ps.ammo[AM_SHELL] = 8
    calls = []
    monkeypatch.setattr(
        weapons, "gunshot",
        lambda shooter, accurate, *a: calls.append(accurate))
    cd = fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
    assert cd == weapons.COOLDOWN[WP_SHOTGUN]
    assert ps.ammo[AM_SHELL] == 7
    assert calls == [False] * 7  # shotguns always spread


@requires_wad
def test_dry_pistol_falls_back_to_fist(setup):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup, dist_units=30)
    ps.ammo[AM_CLIP] = 0
    cd, _flash = weapons.fire(ps, player, phys, index, ctx.mobjs, None,
                              True, ctx, [])
    assert cd == -1
    assert ps.pendingweapon == WP_FIST  # P_CheckAmmo fallback
    for _ in range(weapons.SWITCH_TICS):
        weapons.tick_weapon(ps)
    hp = troop.health
    cd = fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
    assert cd == weapons.COOLDOWN[WP_FIST]
    assert troop.health < hp  # fists need no ammo


@requires_wad
def test_berserk_fists_hit_tenfold(setup):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup, dist_units=30)
    ready_weapon(ps, WP_FIST)
    fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
    plain = 60 - troop.health
    assert 2 <= plain <= 20
    troop.health = 1000  # survive the berserk punch for measuring
    ps.powers[PW_STRENGTH] = -1
    fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
    assert 1000 - troop.health >= 20


@requires_wad
def test_rocket_and_plasma_spawn_missiles(setup):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup)
    for weapon, ammo, mt in ((WP_MISSILE, AM_MISL, "ROCKET"),
                             (WP_PLASMA, AM_CELL, "PLASMA")):
        ready_weapon(ps, weapon)
        ps.ammo[ammo] = 10
        n0 = len(ctx.mobjs)
        cd = fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
        assert cd == weapons.COOLDOWN[weapon]
        assert ps.ammo[ammo] == 9
        kinds = [mo.type for mo in ctx.mobjs[n0:]]
        assert MT_INDEX[mt] in kinds


@requires_wad
def test_bfg_needs_forty_cells(setup):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup)
    ready_weapon(ps, WP_BFG)
    ps.ammo[AM_CELL] = 39
    cd, _flash = weapons.fire(ps, player, phys, index, ctx.mobjs, None,
                              True, ctx, [])
    assert cd == -1  # dry: auto-switch, no shot
    assert ps.ammo[AM_CELL] == 39
    ps.ammo[AM_CELL] = 40
    assert weapons.request_weapon(ps, "7")  # re-raise the BFG
    for _ in range(weapons.SWITCH_TICS):
        weapons.tick_weapon(ps)
    n0 = len(ctx.mobjs)
    cd = fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
    assert cd == weapons.COOLDOWN[WP_BFG]
    assert ps.ammo[AM_CELL] == 0
    assert MT_INDEX["BFG"] in [mo.type for mo in ctx.mobjs[n0:]]


@requires_wad
def test_bfg_spray_hurts_downrange(setup):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup, dist_units=100)
    ready_weapon(ps, WP_BFG)
    ps.ammo[AM_CELL] = 300
    fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
    assert troop.health < 60  # ball flies, spray lands at once


@requires_wad
def test_chaingun_honors_accurate_flag(setup, monkeypatch):
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup)
    ready_weapon(ps, WP_CHAINGUN)
    ps.ammo[AM_CLIP] = 50
    calls = []
    monkeypatch.setattr(
        weapons, "gunshot",
        lambda shooter, accurate, *a: calls.append(accurate))
    weapons.fire(ps, player, phys, index, ctx.mobjs, None, True, ctx, [])
    weapons.fire(ps, player, phys, index, ctx.mobjs, None, False, ctx, [])
    assert calls == [True, False]  # aimed first, sprayed on refire


def test_attack_timelines_match_cooldowns():
    """Body-frame timelines must span exactly one attack cycle."""
    from pydoom.player import WP_CHAINGUN
    for weapon, cd in weapons.COOLDOWN.items():
        assert len(weapons.ATTACK_BODY[weapon]) == cd, weapon
        if weapon == WP_CHAINGUN:
            continue  # NOTE: vanilla flash (5) bridges its 4-tic cycle
        assert weapons.FLASH_TICS[weapon] <= cd, weapon


@requires_wad
def test_weapons_fire_pistol_hits(setup):
    """Pistol through weapons.fire must damage (no blank branches)."""
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup)
    ctx.mobjs = [player, troop]
    hp = troop.health
    for _ in range(6):
        cd = fire_all(ps, player, phys, index, ctx.mobjs, None, True, ctx)
        assert cd >= 0
    assert troop.health < hp


def test_flash_light_levels():
    """A_Light1 everywhere, step-up mid-flash for shotgun/BFG."""
    for weapon, level in weapons.FLASH_LIGHT.items():
        assert level in (0, 1, 2), weapon
    assert weapons.FLASH_LIGHT_STEP[2 - 2 + 2] == (4, 2)  # shotgun
    from pydoom.player import WP_BFG
    assert weapons.FLASH_LIGHT_STEP[WP_BFG] == (11, 2)


def test_attack_timelines_match_vanilla_frames():
    """Body timelines run the vanilla state frames in order."""
    from pydoom.player import WP_PISTOL
    assert weapons.ATTACK_BODY[WP_PISTOL] == \
        "AAAA" + "BBBBBB" + "CCCC" + "BBBBB"  # S_PISTOL1..4
    assert weapons.attack_timeline(WP_CHAINGUN, 0) == "AAAA"
    assert weapons.attack_timeline(WP_CHAINGUN, 1) == "BBBB"
    # NOTE: saw bites twice a cycle (SAW1+SAW2), no alternation.
    assert weapons.attack_timeline(WP_CHAINSAW, 0) == "AAAABBBB"
    assert weapons.attack_timeline(WP_CHAINSAW, 1) == "AAAABBBB"
    for weapon, cd in weapons.HELD_COOLDOWN.items():
        assert len(weapons.attack_timeline(
            weapon, 0, held=True)) == cd, weapon


@requires_wad
def test_held_fire_runs_refire_entry_cycles(setup):
    """Held trigger skips the refire-state tail (vanilla A_ReFire)."""
    from pydoom.player import AM_CELL, WP_BFG, WP_PLASMA
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup)
    ps.ammo = [400, 400, 400, 400]
    player.angle = 0  # face east toward the trooper
    for weapon, cd in ((WP_FIST, 17), (WP_PISTOL, 14),
                       (WP_SHOTGUN, 37), (WP_PLASMA, 3),
                       (WP_BFG, 40)):
        ready_weapon(ps, weapon)
        got, _ = weapons.fire(ps, player, phys, index, ctx.mobjs,
                              None, True, ctx, [], held=True)
        assert got == cd, weapon


@requires_wad
def test_held_saw_bites_twice_a_cycle(setup):
    """Saw cycle is 8 tics with bites at +0/+4 (SAW1+SAW2)."""
    game_map, phys, index, ctx = setup
    player, troop, ps, ctx, _ = make_range(setup, dist_units=30)
    ready_weapon(ps, WP_CHAINSAW)
    player.angle = 0  # face east toward the trooper
    queue = []
    cd, _ = weapons.fire(ps, player, phys, index, ctx.mobjs, None,
                         True, ctx, queue, held=True)
    assert cd == 8
    assert len(queue) == 1  # NOTE: second bite rides the windup
    hp_after_first = troop.health
    for _ in range(5):
        weapons.tick_pending(ps, player, phys, index, ctx.mobjs, None,
                             ctx, queue)
    assert not queue and troop.health < hp_after_first
