"""Tests for p_user.py: vanilla thrust, view height, death and rebirth."""

import os
from types import SimpleNamespace

import pytest

from pydoom import p_user
from pydoom.fixed import ANG90, FRACUNIT, fixed_mul
from pydoom.info import STATE_INDEX
from pydoom.player import PlayerState
from pydoom.tables import ANGLETOFINESHIFT, FINEMASK, finecosine, finesine
from pydoom.ticcmd import Ticcmd

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

_U32 = 0x100000000


def make_mo(**kw):
    fields = dict(x=0, y=0, z=0, momx=0, momy=0, momz=0, angle=0,
                  state=STATE_INDEX["S_PLAY"], floorz=0, flags=0,
                  ceilingz=128 * FRACUNIT, attacker=None)
    fields.update(kw)
    return SimpleNamespace(**fields)


def test_thrust_uses_fine_tables():
    mo = make_mo()
    p_user.thrust(mo, 0, 50 * 2048)  # facing east, full run forward
    idx = (0 >> ANGLETOFINESHIFT) & FINEMASK
    assert mo.momx == fixed_mul(50 * 2048, finecosine(idx))
    assert mo.momy == fixed_mul(50 * 2048, finesine[idx])


def test_move_player_angle_then_momentum():
    mo = make_mo()
    cmd = Ticcmd(forwardmove=50, sidemove=0, angleturn=640, buttons=0)
    onground = p_user.move_player(mo, cmd, set_state=None)
    assert onground is True
    assert mo.angle == (640 << 16) & (_U32 - 1)
    assert mo.momx != 0  # thrust applied
    # NOTE: strafe thrusts at angle-ANG90 (angled strafe for free).
    mo2 = make_mo()
    p_user.move_player(mo2, Ticcmd(0, 40, 0, 0), set_state=None)
    assert mo2.momx != 0 or mo2.momy != 0


def test_move_player_no_control_airborne():
    mo = make_mo(z=64 * FRACUNIT, floorz=0)
    cmd = Ticcmd(forwardmove=50, sidemove=40, angleturn=0, buttons=0)
    onground = p_user.move_player(mo, cmd, set_state=None)
    assert onground is False
    assert (mo.momx, mo.momy) == (0, 0)  # momentum preserved, untouched
    assert mo.angle == 0  # ...but turning always applies


def test_move_player_run_frames():
    from pydoom.mobjs import set_mobj_state
    mo = make_mo()
    p_user.move_player(mo, Ticcmd(10, 0, 0, 0), set_state=set_mobj_state)
    assert mo.state == STATE_INDEX["S_PLAY_RUN1"]


def test_calc_height_bob_clamped():
    ps = PlayerState()
    mo = make_mo(momx=30 * FRACUNIT, momy=30 * FRACUNIT)  # huge momentum
    viewz = p_user.calc_height(ps, mo, 0, True)
    assert ps.bob == p_user.MAXBOB  # clamped to 16 pixels
    assert viewz <= mo.ceilingz - 4 * FRACUNIT


def test_calc_height_standing_still():
    ps = PlayerState()
    mo = make_mo()
    viewz = p_user.calc_height(ps, mo, 7, True)
    assert ps.bob == 0
    assert viewz == mo.z + ps.viewheight  # no bob term at rest


def test_calc_height_airborne_skips_walk():
    ps = PlayerState()
    ps.viewheight -= 3 * FRACUNIT  # dipped (stairs/landing)
    mo = make_mo()
    viewz = p_user.calc_height(ps, mo, 7, False)
    assert ps.viewheight == p_user.VIEWHEIGHT - 3 * FRACUNIT  # untouched
    assert viewz == mo.z + ps.viewheight


def test_calc_height_viewheight_walks_home():
    ps = PlayerState()
    ps.viewheight = p_user.VIEWHEIGHT - FRACUNIT
    ps.deltaviewheight = FRACUNIT // 4
    mo = make_mo()
    p_user.calc_height(ps, mo, 7, True)
    assert ps.viewheight > p_user.VIEWHEIGHT - FRACUNIT
    # NOTE: eventually settles exactly (no drift past VIEWHEIGHT).
    for _ in range(40):
        p_user.calc_height(ps, mo, 7, True)
    assert ps.viewheight == p_user.VIEWHEIGHT
    assert ps.deltaviewheight == 0


def test_z_step_adjust_dips_view():
    ps = PlayerState()
    mo = make_mo(z=0, floorz=8 * FRACUNIT)  # floor rose under the player
    p_user.z_step_adjust(ps, mo)
    assert ps.viewheight == p_user.VIEWHEIGHT - 8 * FRACUNIT
    assert ps.deltaviewheight == (8 * FRACUNIT) >> 3


def test_death_think_falls_and_waits_for_use():
    ps = PlayerState()
    ps.playerstate = p_user.PST_DEAD
    mo = make_mo()
    viewz = p_user.death_think(ps, mo, 0, use_pressed=False)
    assert ps.viewheight == p_user.VIEWHEIGHT - FRACUNIT
    assert ps.playerstate == p_user.PST_DEAD  # still dead, no USE yet
    assert viewz == mo.z + ps.viewheight  # bob term is zero at rest
    for _ in range(200):
        p_user.death_think(ps, mo, 0, use_pressed=False)
    assert ps.viewheight == p_user.DEATHVIEWHEIGHT  # face-plant floor
    p_user.death_think(ps, mo, 0, use_pressed=True)
    assert ps.playerstate == p_user.PST_REBORN


def test_death_think_turns_to_killer():
    ps = PlayerState()
    ps.playerstate = p_user.PST_DEAD
    killer = make_mo(x=100 * FRACUNIT, y=0)
    mo = make_mo(angle=ANG90, attacker=killer)  # facing north, shot east
    first = mo.angle
    p_user.death_think(ps, mo, 0, use_pressed=False)
    assert mo.angle != first
    # NOTE: converges in ANG5 steps (turns clockwise here).
    for _ in range(40):
        p_user.death_think(ps, mo, 0, use_pressed=False)
    from pydoom.angles import point_to_angle2
    assert mo.angle == point_to_angle2(mo.x, mo.y, killer.x, killer.y)


def test_xy_stop_needs_empty_cmd():
    from pydoom.mobjs import FRICTION, STOPSPEED, xy_movement

    class Phys:
        def try_move(self, mo, x, y):
            mo.x, mo.y = x, y
            return True, []

    phys, ctx = Phys(), SimpleNamespace(player_state=PlayerState())
    # NOTE: tiny momentum + held input: friction, never a full stop.
    mo = make_mo(momx=STOPSPEED // 2, momy=0)
    mo.is_player = True
    ctx.player_state.cmd = Ticcmd(forwardmove=25, sidemove=0,
                                  angleturn=0, buttons=0)
    xy_movement(mo, phys, ctx)
    assert mo.momx == fixed_mul(STOPSPEED // 2, FRICTION)
    # NOTE: same momentum, no input: full stop + back to S_PLAY.
    mo = make_mo(momx=STOPSPEED // 2, momy=0,
                 state=STATE_INDEX["S_PLAY_RUN1"] + 1)
    mo.is_player = True
    ctx.player_state.cmd = Ticcmd()
    xy_movement(mo, phys, ctx)
    assert (mo.momx, mo.momy) == (0, 0)
    assert mo.state == STATE_INDEX["S_PLAY"]


def test_xy_player_slides_instead_of_stopping():
    from pydoom.mobjs import xy_movement

    slid = []

    class Phys:
        def try_move(self, mo, x, y):
            return False, []

        def slide_move(self, mo, crossed):
            slid.append((mo, crossed))

    mo = make_mo(momx=FRACUNIT, momy=0)
    mo.is_player = True
    xy_movement(mo, Phys(), SimpleNamespace(player_state=PlayerState()))
    assert len(slid) == 1  # blocked player slides (not zeroed)...
    from pydoom.mobjs import FRICTION
    assert mo.momx == fixed_mul(FRACUNIT, FRICTION)  # ...then friction


class OpenPhys:
    """Stub collision (open field): the fixed-point thrust/friction math
    runs exactly as in game, only walls are missing."""

    def try_move(self, mo, x, y):
        mo.x, mo.y = x, y
        return True, []

    def slide_move(self, mo, crossed):
        pass


def run_tics(mo, ps, phys, cmd, n):
    """move_player + xy_movement, like the viewer loop (no rendering)."""
    from pydoom.mobjs import set_mobj_state, xy_movement
    steps = []
    for _ in range(n):
        ps.cmd = cmd
        p_user.move_player(mo, cmd, set_mobj_state)
        ox, oy = mo.x, mo.y
        xy_movement(mo, phys, SimpleNamespace(player_state=ps))
        steps.append(((mo.x - ox) / FRACUNIT, (mo.y - oy) / FRACUNIT))
    return steps


def test_run_reaches_vanilla_terminal_velocity():
    # NOTE: thrust 50*2048 against FRICTION converges to 16.67 map
    # units/tic (50*2048/(1-0xE800/0x10000)); the legacy mover ran at a
    # flat 13.0, so this band proves the momentum model, not the scale.
    ps = PlayerState()
    mo = make_mo(angle=0, floorz=0, ceilingz=128 * FRACUNIT)
    mo.is_player = True
    steps = run_tics(mo, ps, OpenPhys(),
                     Ticcmd(forwardmove=50, sidemove=0,
                            angleturn=0, buttons=0), 120)
    assert 16.4 < steps[-1][0] < 16.7
    # NOTE: finesine[0] is 25, not 0 (vanilla table dust, exact copy):
    # full-speed runs drift ~0.006 mu/tic sideways, like vanilla.
    assert abs(steps[-1][1]) < 0.01
    # NOTE: release the keys: friction bleeds to an exact stop.
    steps = run_tics(mo, ps, OpenPhys(), Ticcmd(), 200)
    assert (mo.momx, mo.momy) == (0, 0)
    assert steps[-1] == (0, 0)


@requires_wad
def test_momentum_ramp_on_real_e1m1():
    from pydoom.info import MF_FLAGS
    from pydoom.mapdata import Map
    from pydoom.mobjs import Mobj
    from pydoom.physics import Physics
    from pydoom.wad import WadFile
    game_map = Map.from_wad(WadFile(WAD_PATH), "E1M1")
    phys = Physics(game_map)
    start = next(t for t in game_map.things if t.type == 1)
    flags = (MF_FLAGS["MF_SOLID"] | MF_FLAGS["MF_SHOOTABLE"]
             | MF_FLAGS["MF_DROPOFF"])
    mo = Mobj(x=start.x << 16, y=start.y << 16, z=0,
              radius=16 * FRACUNIT, height=56 * FRACUNIT, flags=flags,
              angle=ANG90, is_player=True)  # face north: 704 mu free
    res = phys.check_position(mo, mo.x, mo.y)
    assert res.ok
    mo.floorz, mo.ceilingz, mo.z = res.floorz, res.ceilingz, res.floorz
    ps = PlayerState()
    run = Ticcmd(forwardmove=50, sidemove=0, angleturn=0, buttons=0)
    ps.cmd = run
    p_user.move_player(mo, run, None)
    # NOTE: northbound thrust lands on momy (momx keeps vanilla table
    # dust only: finecosine(512) is not exactly zero, like vanilla).
    assert abs(mo.momx) < 200
    fine_idx = (ANG90 >> ANGLETOFINESHIFT) & FINEMASK  # 2048 = due north
    assert mo.momy == fixed_mul(50 * 2048, finesine[fine_idx])
    steps = run_tics(mo, ps, phys, run, 24)
    disp = sum(s[1] for s in steps)  # northbound map units
    # NOTE: ~250 mu after 24 tics: the acceleration ramp, not the flat
    # legacy 13.0 (which would read exactly 312).
    assert 220 < disp < 290


def test_z_movement_falls_with_gravity():
    """Vanilla falls accelerate (momz 0/-2/-3...), land exactly, and
    oof past -8 (unlike the old instant -8 glue, which gifted thrust
    tics vanilla spends airborne)."""
    from pydoom.fixed import FRACUNIT
    ps = PlayerState()
    mo = make_mo(z=100 * FRACUNIT, floorz=0, ceilingz=200 * FRACUNIT,
                 momz=0, height=56 * FRACUNIT)
    zs = []
    for _ in range(6):
        oof = p_user.z_movement(mo, ps)
        zs.append(mo.z // FRACUNIT)
    assert zs == [100, 98, 95, 91, 86, 80]  # accel, not linear
    assert mo.momz == -7 * FRACUNIT  # 0, -2, -3, ... per tic
    assert oof is False  # soft so far
    while mo.z > mo.floorz:
        oof = p_user.z_movement(mo, ps)
    assert mo.z == mo.floorz == 0
    assert mo.momz == 0
    assert oof is True  # 100mu fall lands hard
    assert ps.deltaviewheight < 0  # view squats (P_ZMovement)


def test_z_movement_rise_snaps_and_clips_ceiling():
    from pydoom.fixed import FRACUNIT
    ps = PlayerState()
    mo = make_mo(z=0, floorz=40 * FRACUNIT, ceilingz=200 * FRACUNIT,
                 height=56 * FRACUNIT)
    assert p_user.z_movement(mo, ps) is False
    assert mo.z == 40 * FRACUNIT  # stairs/lifts glue, like vanilla
    mo.floorz = 0
    mo.ceilingz = 50 * FRACUNIT  # headroom under body height
    p_user.z_movement(mo, ps)
    assert mo.z == 50 * FRACUNIT - mo.height  # duck under
    assert mo.momz == -2 * FRACUNIT  # NOTE: vanilla only clears
    # upward momz on ceiling contact...
    mo.ceilingz = 200 * FRACUNIT  # headroom restored: land next tic
    assert p_user.z_movement(mo, ps) is False
    assert (mo.z, mo.momz) == (0, 0)
