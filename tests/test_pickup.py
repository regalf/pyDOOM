"""Tests for pickup.py/player.py: touch, give, drops, keys, armor."""

import os

import pytest

from pydoom.ai import AIContext
from pydoom.combat import damage_mobj, kill_mobj, register_combat_actions
from pydoom.info import MF_FLAGS, MT_INDEX
from pydoom.mapdata import Map
from pydoom.mobjs import ThingIndex, refresh_sector, spawn_mobj, think_mobj
from pydoom.physics import Physics
from pydoom.pickup import collect_touched, touch_special_thing
from pydoom.player import (
    AM_CELL,
    AM_CLIP,
    AM_MISL,
    AM_SHELL,
    KEY_BLUE,
    KEY_RED,
    MAXHEALTH,
    PW_INVULN,
    PlayerState,
    WP_CHAINGUN,
    WP_SHOTGUN,
)
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

_MF_DROPPED = MF_FLAGS["MF_DROPPED"]


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


def make_player(setup, x=1056, y=-3616):
    game_map, phys, index, ctx = setup
    player = spawn_mobj(game_map, phys, index, x << 16, y << 16, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    refresh_sector(player, phys)
    player.z = player.floorz
    ps = PlayerState()
    ctx.players = [player]
    ctx.player_state = ps
    return player, ps, ctx, index


def make_item(setup, doomednum, x=1056, y=-3616):
    from pydoom.info import type_record
    game_map, phys, index, ctx = setup
    rec = type_record(doomednum)
    assert rec is not None, f"unknown doomednum {doomednum}"
    item = spawn_mobj(game_map, phys, index, x << 16, y << 16, -1,
                      rec["mt"])
    item.doomednum = doomednum
    return item


@requires_wad
def test_clip_pickup(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    item = make_item(setup, 2007)
    picked, msg = touch_special_thing(item, player, ps, ctx)
    assert picked and msg == "PICKED UP THE CLIP."
    assert ps.ammo[AM_CLIP] == 60


@requires_wad
def test_full_ammo_refuses(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    ps.ammo[AM_CLIP] = ps.maxammo[AM_CLIP]
    item = make_item(setup, 2007)
    picked, msg = touch_special_thing(item, player, ps, ctx)
    assert not picked and msg is None
    assert not item.dead


@requires_wad
def test_dropped_clip_gives_half(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    item = make_item(setup, 2007)
    item.flags |= _MF_DROPPED
    picked, _ = touch_special_thing(item, player, ps, ctx)
    assert picked
    assert ps.ammo[AM_CLIP] == 55


@requires_wad
def test_health_rules(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    player.health = 95
    picked, _ = touch_special_thing(make_item(setup, 2011), player, ps, ctx)
    assert picked and player.health == 100  # capped at MAXHEALTH
    picked, _ = touch_special_thing(make_item(setup, 2011), player, ps, ctx)
    assert not picked  # refused at MAXHEALTH
    player.health = 80
    picked, msg = touch_special_thing(make_item(setup, 2012), player, ps, ctx)
    assert picked and msg == "PICKED UP A MEDI-KIT."
    player.health = 10
    picked, msg = touch_special_thing(make_item(setup, 2012), player, ps, ctx)
    # NOTE: NEED wording needs post-heal health < 25 (dead men pick
    # nothing), so the living always read MEDI-KIT.
    assert picked and msg == "PICKED UP A MEDI-KIT."
    player.health = 150
    picked, _ = touch_special_thing(make_item(setup, 2014), player, ps, ctx)
    assert picked and player.health == 151  # bonus works past 100
    player.health = 200
    picked, _ = touch_special_thing(make_item(setup, 2014), player, ps, ctx)
    assert not picked


@requires_wad
def test_armor_rules(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    # NOTE: vanilla always takes the helmet (seeds green from scratch).
    picked, _ = touch_special_thing(make_item(setup, 2015), player, ps, ctx)
    assert picked and (ps.armortype, ps.armorpoints) == (1, 1)
    picked, msg = touch_special_thing(make_item(setup, 2018), player, ps, ctx)
    assert picked and (ps.armortype, ps.armorpoints) == (1, 100)
    assert msg == "PICKED UP THE ARMOR."
    picked, _ = touch_special_thing(make_item(setup, 2018), player, ps, ctx)
    assert not picked  # same suit never replaces
    picked, _ = touch_special_thing(make_item(setup, 2015), player, ps, ctx)
    assert picked and ps.armorpoints == 101
    picked, _ = touch_special_thing(make_item(setup, 2019), player, ps, ctx)
    assert picked and (ps.armortype, ps.armorpoints) == (2, 200)
    picked, _ = touch_special_thing(make_item(setup, 2018), player, ps, ctx)
    assert not picked  # green never downgrades blue


@requires_wad
def test_backpack_doubles_and_feeds(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    picked, msg = touch_special_thing(make_item(setup, 8), player, ps, ctx)
    assert picked and ps.backpack
    assert ps.maxammo == [400, 100, 100, 600]
    assert ps.ammo[AM_CLIP] == 60  # 50 + 10 welcome clips
    assert msg == "PICKED UP A BACKPACK FULL OF AMMO!"


@requires_wad
def test_weapon_pickup_arms_pending(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    picked, msg = touch_special_thing(make_item(setup, 2001), player, ps, ctx)
    assert picked and msg == "YOU GOT THE SHOTGUN!"
    assert ps.weapons & (1 << WP_SHOTGUN)
    assert ps.pendingweapon == WP_SHOTGUN
    assert ps.ammo[AM_SHELL] == 8  # found guns carry two clips
    picked, _ = touch_special_thing(make_item(setup, 2001), player, ps, ctx)
    assert picked and ps.ammo[AM_SHELL] == 16  # owned guns convert


@requires_wad
def test_soul_always_taken_mega_shareware_refused(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    picked, _ = touch_special_thing(make_item(setup, 2013), player, ps, ctx)
    assert picked and player.health == 200
    # NOTE: soulsphere always lands, even at 200 (wasted).
    picked, _ = touch_special_thing(make_item(setup, 2013), player, ps, ctx)
    assert picked and player.health == 200
    # NOTE: megasphere is commercial-only; shareware leaves it alone.
    picked, msg = touch_special_thing(make_item(setup, 83), player, ps, ctx)
    assert not picked and msg is None


@requires_wad
def test_key_pickup_sets_bit(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    picked, msg = touch_special_thing(make_item(setup, 5), player, ps, ctx)
    assert picked and ps.keys & KEY_BLUE
    assert msg == "PICKED UP A BLUE KEYCARD."
    picked, msg = touch_special_thing(make_item(setup, 5), player, ps, ctx)
    assert picked and msg is None  # dupes taken silently


@requires_wad
def test_locked_door_needs_key():
    from pydoom.doors import World
    from pydoom.textures import TextureManager
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M2")
    phys = Physics(game_map)
    world = World(game_map, TextureManager(wad))
    line = game_map.lines[527]
    assert line.special == 28  # red manual door
    msg = world.use_special_line(line, 0, True, 0)
    assert msg == "You need a red key to open this door"
    assert not world.thinkers  # stays shut
    msg = world.use_special_line(line, 0, True, KEY_RED)
    assert msg is None  # opens silently
    assert len(world.thinkers) == 1


@requires_wad
def test_kill_drops_half_clip(setup):
    game_map, phys, index, ctx = setup
    mobjs = []
    ctx.mobjs = mobjs
    zombie = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16, 0,
                        MT_INDEX["POSSESSED"])
    mobjs.append(zombie)
    kill_mobj(None, zombie, ctx)
    drops = [mo for mo in mobjs if mo.type == MT_INDEX["CLIP"]]
    assert len(drops) == 1
    assert drops[0].flags & _MF_DROPPED
    player, ps, ctx, _ = make_player(setup)
    picked, _ = touch_special_thing(drops[0], player, ps, ctx)
    assert picked and ps.ammo[AM_CLIP] == 55


@requires_wad
def test_armor_absorbs_damage(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    ps.armortype, ps.armorpoints = 1, 100  # green saves 1/3
    damage_mobj(player, None, None, 30, ctx)
    assert player.health == 80 and ps.armorpoints == 90
    ps.armortype, ps.armorpoints = 2, 5  # thin blue breaks
    damage_mobj(player, None, None, 30, ctx)
    assert ps.armortype == 0 and ps.armorpoints == 0
    assert player.health == 80 - 25


@requires_wad
def test_invuln_ignores_damage(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    picked, _ = touch_special_thing(make_item(setup, 2022), player, ps, ctx)
    assert picked and ps.powers.get(PW_INVULN)
    damage_mobj(player, None, None, 50, ctx)
    assert player.health == 100


@requires_wad
def test_collect_sweep_picks_reachable_only(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, index2 = make_player(setup)
    ctx.mobjs = [player]
    near = make_item(setup, 2007)
    ctx.mobjs.append(near)
    far = make_item(setup, 2007, x=2000, y=-2000)
    ctx.mobjs.append(far)
    msg = collect_touched(phys, player, ps, ctx)
    assert msg == "PICKED UP THE CLIP."
    assert ps.ammo[AM_CLIP] == 60
    assert near.dead and near not in ctx.mobjs
    assert not far.dead and far in ctx.mobjs


@requires_wad
def test_berserk_arms_fists(setup):
    from pydoom.player import PW_STRENGTH, WP_FIST
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    assert ps.readyweapon != WP_FIST
    picked, msg = touch_special_thing(make_item(setup, 2023), player, ps,
                                      ctx)
    assert picked and msg == "BERSERK!"
    assert ps.powers.get(PW_STRENGTH)
    assert ps.pendingweapon == WP_FIST  # vanilla switches you to fists


@requires_wad
def test_dry_ammo_pickup_arms_matching_gun(setup):
    from pydoom.player import WP_CHAINGUN, WP_FIST, WP_PISTOL
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    ps.weapons |= 1 << WP_CHAINGUN
    ps.readyweapon = ps.pendingweapon = WP_FIST
    ps.ammo[AM_CLIP] = 0
    picked, _ = touch_special_thing(make_item(setup, 2007), player, ps, ctx)
    assert picked and ps.ammo[AM_CLIP] == 10
    assert ps.pendingweapon == WP_CHAINGUN  # not user selectable
    assert ps.readyweapon == WP_FIST  # raises over the next ticks


@requires_wad
def test_power_dupes_refresh(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    picked, _ = touch_special_thing(make_item(setup, 2022), player, ps, ctx)
    assert picked
    first = ps.powers.get(PW_INVULN)
    ps.powers[PW_INVULN] = 5  # nearly expired
    picked, _ = touch_special_thing(make_item(setup, 2022), player, ps, ctx)
    assert picked and ps.powers.get(PW_INVULN) == first  # refreshed


@requires_wad
def test_invuln_blocks_small_not_telefrag(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    picked, _ = touch_special_thing(make_item(setup, 2022), player, ps, ctx)
    assert picked
    damage_mobj(player, None, None, 50, ctx)
    assert player.health == 100  # shrugged off
    damage_mobj(player, None, None, 10000, ctx)
    assert player.health <= 0  # NOTE: >= 1000 goes through (telefrag)


@requires_wad
def test_hell_hack_caps_sector11_kill(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    player.sector.special = 11  # E1M8 burn-out rules anywhere
    player.health = 15
    damage_mobj(player, None, None, 20, ctx)
    assert player.health == 1  # NOTE: capped at health-1, exit decides
    player.sector.special = 0


@requires_wad
def test_spent_armor_clears_type(setup):
    game_map, phys, index, ctx = setup
    player, ps, ctx, _ = make_player(setup)
    ps.armortype, ps.armorpoints = 2, 0  # fumes: type set, nothing banked
    damage_mobj(player, None, None, 30, ctx)
    assert player.health == 70  # nothing absorbed...
    assert ps.armortype == 0  # ...but vanilla still clears the type
