"""Player thinking (p_user.c): thrust, view height, death and rebirth.

Fixed-point throughout (16.16 ints, fine tables for thrust); no pygame.
The viewer owns the loop: per tic it feeds the built Ticcmd to
move_player (momentum into momx/momy), steps the body with
mobjs.xy_movement (friction, slide, line crossing), then renders the
viewz calc_height returns. Death runs the PST_DEAD -> PST_REBORN flow
(DeathThink waits for BT_USE, rebirth resets the inventory).

Vanilla pieces owned elsewhere and NOT duplicated here: P_MovePsprites
(the viewer ticks weapons every tic), power/palette counters
(PlayerState.tick), and P_PlayerInSpecialSector (the viewer calls it
after movement, like P_PlayerThink does after P_MovePlayer).
"""
from __future__ import annotations

from pydoom.angles import point_to_angle2
from pydoom.fixed import ANG90, ANG180, FRACUNIT, fixed_mul
from pydoom.info import MF_FLAGS, STATE_INDEX
from pydoom.player import CF_NOMOMENTUM

_GRAVITY = FRACUNIT  # p_local.h GRAVITY
_MF_NOGRAVITY = MF_FLAGS["MF_NOGRAVITY"]
from pydoom.tables import (
    ANGLETOFINESHIFT,
    FINEANGLES,
    FINEMASK,
    finecosine,
    finesine,
)

# NOTE: d_player.h playerstate_t.
PST_LIVE, PST_DEAD, PST_REBORN = 0, 1, 2

# NOTE: p_user.c: 16 pixels of view bob, eye height, death POV height.
MAXBOB = 0x100000
VIEWHEIGHT = 41 * FRACUNIT
DEATHVIEWHEIGHT = 6 * FRACUNIT
# NOTE: P_DeathThink turns toward the killer in ANG90/18 steps.
ANG5 = ANG90 // 18
_U32 = 0x100000000


def thrust(mo, angle: int, move: int) -> None:
    """P_Thrust: push mom along a BAM angle (move is 16.16 fixed)."""
    idx = ((angle & (_U32 - 1)) >> ANGLETOFINESHIFT) & FINEMASK
    mo.momx += fixed_mul(move, finecosine(idx))
    mo.momy += fixed_mul(move, finesine[idx])


def move_player(mo, cmd, set_state=None) -> bool:
    """P_MovePlayer: angle first, then momentum (no control airborne).

    Returns onground (z <= floorz) for the caller's P_CalcHeight.
    """
    mo.angle = (mo.angle + (cmd.angleturn << 16)) & (_U32 - 1)
    onground = mo.z <= mo.floorz
    if cmd.forwardmove and onground:
        thrust(mo, mo.angle, cmd.forwardmove * 2048)
    if cmd.sidemove and onground:
        # NOTE: angled strafe is just thrust at angle-ANG90.
        thrust(mo, (mo.angle - ANG90) & (_U32 - 1), cmd.sidemove * 2048)
    # NOTE: MF_JUSTATTACKED (chainsaw lunge) skipped: our A_Saw never
    # sets the flag, so the cmd override would never fire.
    if ((cmd.forwardmove or cmd.sidemove)
            and mo.state == STATE_INDEX["S_PLAY"] and set_state is not None):
        set_state(mo, STATE_INDEX["S_PLAY_RUN1"])
    return onground


def calc_height(ps, mo, leveltime: int, onground: bool) -> int:
    """P_CalcHeight: bob from momentum, viewheight walk, ceiling clamp.

    Returns viewz (fixed); ps.bob/viewheight/deltaviewheight updated.
    """
    ps.bob = (fixed_mul(mo.momx, mo.momx)
              + fixed_mul(mo.momy, mo.momy)) >> 2
    if ps.bob > MAXBOB:
        ps.bob = MAXBOB
    if (ps.cheats & CF_NOMOMENTUM) or not onground:
        viewz = mo.z + VIEWHEIGHT
        if viewz > mo.ceilingz - 4 * FRACUNIT:
            viewz = mo.ceilingz - 4 * FRACUNIT
        # NOTE: vanilla then overwrites with z + viewheight (kept exact).
        return mo.z + ps.viewheight
    angle = (FINEANGLES // 20 * leveltime) & FINEMASK
    bobterm = fixed_mul(ps.bob // 2, finesine[angle])
    if ps.playerstate == PST_LIVE:
        ps.viewheight += ps.deltaviewheight
        if ps.viewheight > VIEWHEIGHT:
            ps.viewheight = VIEWHEIGHT
            ps.deltaviewheight = 0
        if ps.viewheight < VIEWHEIGHT // 2:
            ps.viewheight = VIEWHEIGHT // 2
            if ps.deltaviewheight <= 0:
                ps.deltaviewheight = 1
        if ps.deltaviewheight:
            ps.deltaviewheight += FRACUNIT // 4
            if not ps.deltaviewheight:
                ps.deltaviewheight = 1
    viewz = mo.z + ps.viewheight + bobterm
    if viewz > mo.ceilingz - 4 * FRACUNIT:
        viewz = mo.ceilingz - 4 * FRACUNIT
    return viewz


def z_step_adjust(ps, mo) -> None:
    """P_ZMovement smooth step-up: stairs dip the view, then it walks
    back (the deltaviewheight set here is consumed by calc_height)."""
    if mo.z < mo.floorz:
        ps.viewheight -= mo.floorz - mo.z
        ps.deltaviewheight = (VIEWHEIGHT - ps.viewheight) >> 3


def z_movement(mo, ps) -> bool:
    """P_ZMovement player path: accelerating falls, hard-landing view
    dip, ceiling clip. Returns True when the landing earns sfx_oof
    (momz < -8*GRAVITY, vanilla threshold). Skull/missile/floater
    branches never apply to players."""
    mo.z += mo.momz
    oof = False
    if mo.z <= mo.floorz:
        if mo.momz < 0:
            if mo.momz < -_GRAVITY * 8:
                ps.deltaviewheight = mo.momz >> 3
                oof = True
            mo.momz = 0
        mo.z = mo.floorz
    elif not (mo.flags & _MF_NOGRAVITY):
        if mo.momz == 0:
            mo.momz = -_GRAVITY * 2
        else:
            mo.momz -= _GRAVITY
    if mo.z + mo.height > mo.ceilingz:
        if mo.momz > 0:
            mo.momz = 0
        mo.z = mo.ceilingz - mo.height
    return oof


def death_think(ps, mo, leveltime: int, use_pressed: bool) -> int:
    """P_DeathThink: fall to face level, turn to the killer, wait USE.

    MovePsprites and damagecount already tick in the viewer loop (like
    P_PlayerThink's tail), so this only does the death-specific part.
    Sets PST_REBORN when the use button arrives; returns viewz.
    """
    if ps.viewheight > DEATHVIEWHEIGHT:
        ps.viewheight -= FRACUNIT
    if ps.viewheight < DEATHVIEWHEIGHT:
        ps.viewheight = DEATHVIEWHEIGHT
    ps.deltaviewheight = 0
    onground = mo.z <= mo.floorz
    viewz = calc_height(ps, mo, leveltime, onground)
    attacker = mo.attacker
    if attacker is not None and attacker is not mo:
        angle = point_to_angle2(mo.x, mo.y, attacker.x, attacker.y)
        delta = (angle - mo.angle) & (_U32 - 1)
        if delta < ANG5 or delta > (_U32 - ANG5):
            mo.angle = angle
        elif delta < ANG180:
            mo.angle = (mo.angle + ANG5) & (_U32 - 1)
        else:
            mo.angle = (mo.angle - ANG5) & (_U32 - 1)
    if use_pressed:
        ps.playerstate = PST_REBORN
    return viewz
