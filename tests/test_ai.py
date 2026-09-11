"""Tests for ai.py: sight, waking, chasing, freeze, sound flood."""

import math
import os

import pytest

from pydoom.ai import (
    AIContext,
    check_sight,
    divline_side,
    new_chase_dir,
    noise_alert,
)
from pydoom.info import MT_INDEX
from pydoom.mapdata import Map
from pydoom.mobjs import (
    ThingIndex,
    set_mobj_state,
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


@pytest.fixture()
def setup():
    # Function-scoped: spawned bodies are solid and would perturb
    # later movement tests if shared.
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    ctx = AIContext(
        physics=phys,
        sector_index={id(s): i for i, s in enumerate(game_map.sectors)},
    )
    return game_map, phys, index, ctx


def make_player(setup, x=1056, y=-3616):
    _, phys, index, ctx = setup
    pmo = spawn_mobj(None, phys, index, x << 16, y << 16, 0,
                     MT_INDEX["PLAYER"])
    ctx.players = [pmo]
    return pmo


@requires_wad
def test_sight_open_room(setup):
    game_map, phys, index, ctx = setup
    pmo = make_player(setup)
    mo = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                    MT_INDEX["TROOP"])
    assert check_sight(mo, pmo, ctx) is True


@requires_wad
def test_sight_blocked_by_wall(setup):
    game_map, phys, index, ctx = setup
    pmo = make_player(setup)
    mo = spawn_mobj(game_map, phys, index, 1056 << 16, -3750 << 16, 0,
                    MT_INDEX["TROOP"])
    assert check_sight(mo, pmo, ctx) is False


@requires_wad
def test_reject_table_blocks(setup):
    game_map, phys, index, ctx = setup
    # Find any sector pair the REJECT table calls invisible.
    n = len(game_map.sectors)
    pair = None
    for a in range(n):
        for b in range(n):
            pnum = a * n + b
            if game_map.reject[pnum >> 3] & (1 << (pnum & 7)):
                pair = (a, b)
                break
        if pair:
            break
    assert pair is not None
    a, b = pair
    sta = spawn_mobj(game_map, phys, index, 0, 0, 0, MT_INDEX["TROOP"])
    stb = spawn_mobj(game_map, phys, index, 0, 0, 0, MT_INDEX["TROOP"])
    sta.sector, stb.sector = game_map.sectors[a], game_map.sectors[b]
    assert check_sight(sta, stb, ctx) is False


@requires_wad
def test_look_wakes_and_chase_approaches(setup):
    game_map, phys, index, ctx = setup
    pmo = make_player(setup)
    mo = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                    MT_INDEX["TROOP"])
    for _ in range(30):  # spawn frames loop A_Look until sighted
        think_mobj(mo, phys, ctx)
    assert mo.target is pmo
    assert mo.state != mo.spawnstate
    d0 = math.hypot(mo.x - pmo.x, mo.y - pmo.y)
    for _ in range(120):
        think_mobj(mo, phys, ctx)
    d1 = math.hypot(mo.x - pmo.x, mo.y - pmo.y)
    assert d1 < d0  # closed in
    assert d1 < 200 * 65536  # melee holding range, never through player


@requires_wad
def test_frozen_ai_stays_statue(setup):
    game_map, phys, index, ctx = setup
    ctx.ai_frozen = True
    try:
        pmo = make_player(setup)
        mo = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                        MT_INDEX["TROOP"])
        x0, y0 = mo.x, mo.y
        for _ in range(60):
            think_mobj(mo, phys, ctx)
        assert mo.target is None
        assert (mo.x, mo.y) == (x0, y0)
    finally:
        ctx.ai_frozen = False


@requires_wad
def test_noise_alert_floods_region(setup):
    game_map, phys, index, ctx = setup
    pmo = make_player(setup)
    emitter = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                         MT_INDEX["TROOP"])
    noise_alert(pmo, emitter, ctx)
    flooded = [s for s in game_map.sectors if s.soundtarget is pmo]
    assert len(flooded) > 1  # reached beyond the emitter sector
    assert emitter.sector.soundtarget is pmo


@requires_wad
def test_new_chase_dir_steps_closer(setup):
    game_map, phys, index, ctx = setup
    pmo = make_player(setup)
    mo = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                    MT_INDEX["TROOP"])
    mo.target = pmo
    d0 = abs(mo.x - pmo.x) + abs(mo.y - pmo.y)
    new_chase_dir(mo, ctx)
    d1 = abs(mo.x - pmo.x) + abs(mo.y - pmo.y)
    assert d1 <= d0


def test_divline_side_on_quirk():
    # Vanilla compares x to node->y (not node->x) for horizontal lines.
    assert divline_side(7, 999, (0, 7, 10, 0)) == 2
    assert divline_side(0, 0, (0, 0, 0, 10)) == 2  # vertical, x == lx
    assert divline_side(-1, 0, (0, 0, 0, 10)) == 1
    assert divline_side(1, 0, (0, 0, 0, 10)) == 0


@requires_wad
def test_sight_matches_bruteforce_geometry(setup):
    # Independent float ray-caster over linedefs; REJECT-blocked pairs
    # are skipped (vanilla honors REJECT, pure geometry does not).
    import random
    game_map, phys, index, ctx = setup
    n = len(game_map.sectors)

    def brute(ax, ay, az1, bx, by, bz1, bz2):
        loslope, hislope = -1e18, 1e18
        import math
        dist_total = math.hypot(bx - ax, by - ay)
        if dist_total == 0:
            return True
        hits = []
        for li in game_map.lines:
            assert li.v1 is not None and li.v2 is not None
            x1, y1 = li.v1.x / 65536, li.v1.y / 65536
            x2, y2 = li.v2.x / 65536, li.v2.y / 65536
            den = (bx - ax) * (y2 - y1) - (by - ay) * (x2 - x1)
            if abs(den) < 1e-9:
                continue
            t = ((x1 - ax) * (y2 - y1) - (y1 - ay) * (x2 - x1)) / den
            u = ((x1 - ax) * (by - ay) - (y1 - ay) * (bx - ax)) / den
            if 0.001 < t < 0.999 and 0.001 < u < 0.999:
                hits.append((t, li))
        hits.sort()
        for t, li in hits:
            if li.backsector is None:
                return False
            f, b = li.frontsector, li.backsector
            assert f is not None and b is not None
            if (f.floorheight == b.floorheight
                    and f.ceilingheight == b.ceilingheight):
                continue
            ot = min(f.ceilingheight, b.ceilingheight) / 65536
            ob = max(f.floorheight, b.floorheight) / 65536
            if ob >= ot:
                return False
            d = dist_total * t
            if f.floorheight != b.floorheight:
                loslope = max(loslope, (ob - az1) / d)
            if f.ceilingheight != b.ceilingheight:
                hislope = min(hislope, (ot - az1) / d)
            if hislope <= loslope:
                return False
        d = dist_total
        return not (hislope * d + az1 < bz1 - 1e-6
                    or loslope * d + az1 > bz2 + 1e-6)

    rng = random.Random(3)
    checked = 0
    for _ in range(120):
        ax = rng.uniform(-700, 3700)
        ay = rng.uniform(-4800, -2100)
        bx = rng.uniform(-700, 3700)
        by = rng.uniform(-4800, -2100)
        sa = phys.subsector_at(int(ax * 65536), int(ay * 65536)).sector
        sb = phys.subsector_at(int(bx * 65536), int(by * 65536)).sector
        if sa is None or sb is None:
            continue
        s1 = game_map.sectors.index(sa)
        s2 = game_map.sectors.index(sb)
        pnum = s1 * n + s2
        if game_map.reject[pnum >> 3] & (1 << (pnum & 7)):
            continue  # REJECT: vanilla says no sight, geometry may differ
        a = spawn_mobj(game_map, phys, index, int(ax * 65536),
                       int(ay * 65536), 0, MT_INDEX["TROOP"])
        a.z = sa.floorheight  # grounded, like a real standing body
        b = spawn_mobj(game_map, phys, index, int(bx * 65536),
                       int(by * 65536), 0, MT_INDEX["TROOP"])
        b.z = sb.floorheight
        want = brute(ax, ay, sa.floorheight / 65536 + 42,
                     bx, by, sb.floorheight / 65536,
                     sb.floorheight / 65536 + 56)
        assert check_sight(a, b, ctx) == want, ((ax, ay), (bx, by))
        checked += 1
    assert checked > 50


@requires_wad
def test_sight_heals_after_stale_sector(setup):
    """REJECT uses cached sectors: a roamed body stuck in its spawn
    sector reads blind, one legal step re-links it (blue-room bug)."""
    game_map, phys, index, ctx = setup
    zombie = spawn_mobj(game_map, phys, index, 2272 << 16, -2352 << 16, 0,
                        MT_INDEX["POSSESSED"])
    zombie.z = zombie.floorz
    player = spawn_mobj(game_map, phys, index, 1867 << 16, -2409 << 16, 0,
                        MT_INDEX["PLAYER"])
    player.z = player.floorz
    assert check_sight(zombie, player, ctx)
    # Pre-fix viewer roam: body at the doorway, sector of map start.
    start_sec = phys.subsector_at(1056 << 16, -3616 << 16).sector
    player.sector.thinglist.remove(player)
    player.sector = start_sec
    start_sec.thinglist.append(player)
    assert not check_sight(zombie, player, ctx)  # REJECT false negative
    ok, _ = phys.try_move(player, player.x - (8 << 16), player.y)
    assert ok
    assert check_sight(zombie, player, ctx)


def _floater_mo(**kw):
    from types import SimpleNamespace  # noqa: F401 (used below)
    from pydoom.fixed import FRACUNIT
    from pydoom.info import MF_FLAGS
    from pydoom.mobjs import Mobj
    fields = dict(x=0, y=0, z=0, momx=0, momy=0, momz=0, angle=0,
                  flags=(MF_FLAGS["MF_SOLID"] | MF_FLAGS["MF_SHOOTABLE"]
                         | MF_FLAGS["MF_FLOAT"] | MF_FLAGS["MF_NOGRAVITY"]),
                  height=56 * FRACUNIT, radius=16 * FRACUNIT,
                  floorz=0, ceilingz=200 * FRACUNIT, movedir=2, speed=8,
                  target=None)
    fields.update(kw)
    return Mobj(**fields)


def test_floater_climbs_toward_blocked_floor():
    """P_Move floatok: a blocked floater steps z toward the target cell
    floor instead of turning (cacos rise over steps)."""
    from types import SimpleNamespace
    from pydoom.ai import DI_NODIR, move_actor
    from pydoom.fixed import FRACUNIT
    from pydoom.info import MF_FLAGS
    from pydoom.mobjs import FLOATSPEED
    res = SimpleNamespace(ok=True, floorz=48 * FRACUNIT,
                          ceilingz=200 * FRACUNIT, spechit=[])
    phys = SimpleNamespace(try_move=lambda mo, x, y: (False, []),
                           check_position=lambda mo, x, y: res)
    world = SimpleNamespace(use_special_line=lambda *a: False)
    ctx = SimpleNamespace(physics=phys, world=world)
    mo = _floater_mo()
    assert move_actor(mo, ctx) is True
    assert mo.z == FLOATSPEED
    assert mo.flags & MF_FLAGS["MF_INFLOAT"]
    # NOTE: ground bodies still give up and open doors instead.
    from pydoom.mobjs import Mobj
    grunt = Mobj(x=0, y=0, z=0, flags=MF_FLAGS["MF_SOLID"],
                 height=56 * FRACUNIT, movedir=2, speed=8)
    assert move_actor(grunt, ctx) is False
    assert grunt.movedir == DI_NODIR


def test_floater_eases_toward_target_height():
    """P_ZMovement floaters: z chases the target mid-height when close,
    holds while skull-charging, ignores distant targets."""
    from types import SimpleNamespace
    from pydoom.fixed import FRACUNIT
    from pydoom.info import MF_FLAGS
    from pydoom.mobjs import FLOATSPEED, _z_movement
    target = SimpleNamespace(x=100 << 16, y=0, z=50 * FRACUNIT,
                             height=56 * FRACUNIT)
    mo = _floater_mo(target=target)
    _z_movement(mo)
    assert mo.z == FLOATSPEED  # 100 mu away < 3x the 78 mu gap
    far = SimpleNamespace(x=500 << 16, y=0, z=50 * FRACUNIT,
                          height=56 * FRACUNIT)
    mo2 = _floater_mo(target=far)
    _z_movement(mo2)
    assert mo2.z == 0  # 500 mu away: out of the approach cone
    mo3 = _floater_mo(target=target)
    mo3.flags |= MF_FLAGS["MF_SKULLFLY"]
    _z_movement(mo3)
    assert mo3.z == 0  # charging skulls never ease
