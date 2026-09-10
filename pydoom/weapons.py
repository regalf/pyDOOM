"""Player weapons (p_pspr.c fire logic, no sprites/sounds): slots 1-7,
raise/lower switching, P_CheckAmmo fallback and per-weapon fire.

Effects reuse combat hitscans/missiles, so ballistics stay identical
to monster attacks. SSG data is carried (Doom 2 maps) but never fires
here: the bundled IWAD is Doom 1.
"""

from __future__ import annotations

from pydoom.combat import (
    aim_line_attack,
    bullet_slope,
    gunshot,
    line_attack,
)
from pydoom.info import MT_INDEX
from pydoom.m_random import p_random
from pydoom.player import (
    AM_CELL,
    AM_CLIP,
    AM_MISL,
    AM_SHELL,
    PW_STRENGTH,
    WP_BFG,
    WP_CHAINGUN,
    WP_CHAINSAW,
    WP_FIST,
    WP_MISSILE,
    WP_PISTOL,
    WP_PLASMA,
    WP_SHOTGUN,
    WP_SSG,
)

# NOTE: viewer-tuned attack cycles were 2-8x vanilla; these are the
# real state durations (p_pspr.c), so held-fire rates match Doom.
SWITCH_TICS = 10
COOLDOWN = {WP_FIST: 22, WP_PISTOL: 19, WP_SHOTGUN: 44, WP_CHAINGUN: 4,
            WP_MISSILE: 20, WP_PLASMA: 23, WP_BFG: 60, WP_CHAINSAW: 4,
            WP_SSG: 44}
# NOTE: muzzle-flash lengths from the FLASH states (lights skipped).
FLASH_TICS = {WP_FIST: 0, WP_PISTOL: 7, WP_SHOTGUN: 7, WP_CHAINGUN: 5,
              WP_MISSILE: 7, WP_PLASMA: 4, WP_BFG: 17, WP_CHAINSAW: 0,
              WP_SSG: 7}
# NOTE: body frames across each cycle (one char per tic), from the
# attack states: the kick reads because it lasts, like vanilla.
ATTACK_BODY = {
    WP_FIST: "BBBBCCCCDDDDDCCCCBBBBB",
    # NOTE: the pistol timeline starts at the peak: vanilla fires (and
    # flashes) on S_PISTOL2/B, the A wind-up settles the cycle instead.
    WP_PISTOL: "BBBBBBCCCCBBBBBAAAA",
    WP_SHOTGUN: ("AAAAAAAAAABBBBBCCCCCDDDDCCCCCBBBBBAAAAAAAAAA"),
    WP_CHAINGUN: "AABB",
    WP_MISSILE: "B" * 20,
    WP_PLASMA: "AAA" + "B" * 20,
    WP_BFG: "A" * 20 + "B" * 40,
    WP_CHAINSAW: "AABB",
    WP_SSG: "A" * 44,
}
# (ammo type, rounds per shot); ammo < 0 means unarmed.
COST = {WP_FIST: (-1, 0), WP_PISTOL: (AM_CLIP, 1), WP_SHOTGUN: (AM_SHELL, 1),
        WP_CHAINGUN: (AM_CLIP, 1), WP_MISSILE: (AM_MISL, 1),
        WP_PLASMA: (AM_CELL, 1), WP_BFG: (AM_CELL, 40),
        WP_CHAINSAW: (-1, 0), WP_SSG: (AM_SHELL, 2)}
# Number-key preference lists (vanilla key order 1..7).
KEYMAP = {"1": (WP_CHAINSAW, WP_FIST), "2": (WP_PISTOL,),
          "3": (WP_SSG, WP_SHOTGUN), "4": (WP_CHAINGUN,),
          "5": (WP_MISSILE,), "6": (WP_PLASMA,), "7": (WP_BFG,)}
# NOTE: HUD sprites (body, flash); plasma/BFG lumps only exist in the
# registered WAD, the renderer skips missing ones silently. CSAW is the
# floor pickup; the held saw is SAWG (all shareware-present).
PSPRITES = {WP_FIST: ("PUNG", None), WP_PISTOL: ("PISG", "PISF"),
            WP_SHOTGUN: ("SHTG", "SHTF"), WP_CHAINGUN: ("CHGG", "CHGF"),
            WP_MISSILE: ("MISG", "MISF"), WP_PLASMA: ("PLSG", "PLSF"),
            WP_BFG: ("BFGG", "BFGF"), WP_CHAINSAW: ("SAWG", None),
            WP_SSG: ("SHT2", "SHTF")}


def has_ammo_for(ps, weapon: int) -> bool:
    """Enough loaded for one shot (fists/saws always ready)."""
    ammo, cost = COST[weapon]
    if ammo < 0:
        return True
    return ps.ammo[ammo] >= cost


def request_weapon(ps, key: str) -> bool:
    """Number-key select: pending = first owned of the key's list."""
    for weapon in KEYMAP.get(key, ()):
        if ps.weapons & (1 << weapon):
            if ps.pendingweapon != weapon:
                ps.pendingweapon = weapon
                ps.switchtics = SWITCH_TICS
            return True
    return False  # not owned: vanilla keeps the current gun


def tick_weapon(ps) -> None:
    """Advance a pending raise; arrival arms the ready slot."""
    if ps.pendingweapon != ps.readyweapon:
        if ps.switchtics > 0:
            ps.switchtics -= 1
        if ps.switchtics <= 0:
            ps.readyweapon = ps.pendingweapon


def check_ammo(ps) -> bool:
    """P_CheckAmmo: dry guns pick the fallback instead of clicking."""
    if has_ammo_for(ps, ps.readyweapon):
        return True
    from pydoom.player import GAMEMODE
    commercial = GAMEMODE == "commercial"
    order = []
    if commercial:  # NOTE: shareware E1 never owns these anyway.
        order.append(WP_PLASMA)
    order += [WP_CHAINGUN, WP_SHOTGUN]
    if commercial:
        order.append(WP_SSG)
    order += [WP_PISTOL, WP_CHAINSAW, WP_MISSILE]
    if commercial:
        order.append(WP_BFG)
    order.append(WP_FIST)
    for weapon in order:
        if weapon != ps.readyweapon and (ps.weapons & (1 << weapon)) \
                and has_ammo_for(ps, weapon):
            if ps.pendingweapon != weapon:
                ps.pendingweapon = weapon
                ps.switchtics = SWITCH_TICS
            return False
    # NOTE: pistol needs no ownership check (always carried); the fist
    # fallback below is unreachable in practice but kept for safety.
    if ps.pendingweapon != WP_FIST:
        ps.pendingweapon = WP_FIST
        ps.switchtics = SWITCH_TICS
    return False


def _melee(ps, shooter, physics, index, mobjs, skyflat, ctx,
           saw: bool) -> None:
    """A_Punch/A_Saw: spread-aimed short hitscan (berserk fists x10)."""
    from pydoom.ai import MELEERANGE
    from pydoom.angles import point_to_angle2
    attack_range = MELEERANGE + (65536 if saw else 0)
    angle = (shooter.angle + ((p_random() - p_random()) << 18)) & 0xFFFFFFFF
    slope, _t = aim_line_attack(shooter, angle, attack_range,
                                physics, index, mobjs, skyflat)
    damage = ((p_random() % 10) + 1) * 2
    if not saw and ps.powers.get(PW_STRENGTH):
        damage *= 10
    line_attack(shooter, angle, attack_range, slope, damage,
                physics, index, mobjs, skyflat, ctx)
    target = getattr(shooter, "target", None)
    if target is not None and getattr(target, "health", 0) > 0:
        shooter.angle = point_to_angle2(shooter.x, shooter.y,
                                        target.x, target.y)


BFG_SPRAY_RANGE = 16 * 64 * 65536  # p_pspr.c, not MISSILERANGE


def spawn_player_missile(shooter, mt: int, physics, index, mobjs):
    """P_SpawnPlayerMissile: aimed retries, z+32, slope in momz."""
    from pydoom import tables
    from pydoom.combat import check_missile_spawn
    from pydoom.fixed import FRACUNIT
    from pydoom.info import MOBJ_TYPES
    from pydoom.mobjs import spawn_mobj
    an = shooter.angle
    _slope, target = aim_line_attack(shooter, an, BFG_SPRAY_RANGE,
                                     physics, index, mobjs, None)
    if target is None:
        an = (an + (1 << 26)) & 0xFFFFFFFF
        _slope, target = aim_line_attack(shooter, an, BFG_SPRAY_RANGE,
                                         physics, index, mobjs, None)
        if target is None:
            an = (an - (2 << 26)) & 0xFFFFFFFF
            _slope, target = aim_line_attack(shooter, an, BFG_SPRAY_RANGE,
                                             physics, index, mobjs, None)
    if target is None:
        an = shooter.angle
        slope = 0
    else:
        slope = _slope
    th = spawn_mobj(None, physics, index, shooter.x, shooter.y,
                    shooter.z + 32 * FRACUNIT, mt)
    th.target = shooter
    th.angle = an
    speed = MOBJ_TYPES[mt][10]
    fa = (an & 0xFFFFFFFF) >> 19
    th.momx = speed * tables.finecosine(fa) // FRACUNIT
    th.momy = speed * tables.finesine[fa] // FRACUNIT
    th.momz = speed * slope // FRACUNIT
    check_missile_spawn(th, physics)
    mobjs.append(th)
    return th


def _bfg_spray(ball, shooter, physics, index, mobjs, skyflat, ctx) -> None:
    """A_BFGSpray on the fresh ball: 40 rays off its angle, each needing
    its own aim lock (blind rays fizzle); EXTRABFG puffs mark the hits."""
    from pydoom.fixed import ANG90
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import spawn_mobj
    base = ball.angle
    for i in range(40):
        an = (base - ANG90 // 2 + (ANG90 // 40) * i) & 0xFFFFFFFF
        _slope, target = aim_line_attack(shooter, an, BFG_SPRAY_RANGE,
                                         physics, index, mobjs, skyflat)
        if target is None:
            continue  # NOTE: vanilla skips lockless rays entirely
        puff = spawn_mobj(None, physics, index, target.x, target.y,
                          target.z + (target.height >> 2),
                          MT_INDEX["EXTRABFG"])
        mobjs.append(puff)
        damage = 0
        for _ in range(15):
            damage += (p_random() & 7) + 1
        from pydoom.combat import damage_mobj
        damage_mobj(target, shooter, shooter, damage, ctx)


def fire(ps, shooter, physics, index, mobjs, skyflat, accurate: bool,
         ctx=None) -> int:
    """Fire the ready weapon. Returns cooldown tics, or -1 to hold
    (still switching, or just auto-switched off a dry gun)."""
    if ps.pendingweapon != ps.readyweapon:
        return -1
    if not has_ammo_for(ps, ps.readyweapon):
        check_ammo(ps)
        return -1
    weapon = ps.readyweapon
    ammo, cost = COST[weapon]
    if ammo >= 0:
        ps.ammo[ammo] -= cost
    from pydoom import audio
    if weapon == WP_FIST:
        # NOTE: vanilla fists swing silent.
        _melee(ps, shooter, physics, index, mobjs, skyflat, ctx, False)
    elif weapon == WP_CHAINSAW:
        target = getattr(shooter, "target", None)
        before = getattr(target, "health", None)
        _melee(ps, shooter, physics, index, mobjs, skyflat, ctx, True)
        after = getattr(target, "health", None)
        audio.play("sawhit" if after is not None and before is not None
                   and after < before else "sawful",
                   shooter.x, shooter.y, shooter)
    elif weapon == WP_PISTOL:
        audio.play("pistol", shooter.x, shooter.y, shooter)
        slope = bullet_slope(shooter, physics, index, mobjs, skyflat)
        gunshot(shooter, accurate, slope, physics, index, mobjs,
                skyflat, ctx)
    elif weapon == WP_SHOTGUN:
        audio.play("shotgn", shooter.x, shooter.y, shooter)
        slope = bullet_slope(shooter, physics, index, mobjs, skyflat)
        for _ in range(7):  # NOTE: A_FireShotgun never auto-aims straight
            gunshot(shooter, False, slope, physics, index, mobjs,
                    skyflat, ctx)
    elif weapon == WP_MISSILE:
        audio.play("rlaunc", shooter.x, shooter.y, shooter)
        spawn_player_missile(shooter, MT_INDEX["ROCKET"], physics, index,
                             mobjs)
    elif weapon == WP_PLASMA:
        # NOTE: shareware has no plasma lump; feel free to hear nothing.
        spawn_player_missile(shooter, MT_INDEX["PLASMA"], physics, index,
                             mobjs)
    elif weapon == WP_BFG:
        # NOTE: shareware has no BFG lump either.
        # NOTE: A_FireBFG only launches; A_BFGSpray runs on the ball.
        ball = spawn_player_missile(shooter, MT_INDEX["BFG"], physics,
                                    index, mobjs)
        _bfg_spray(ball, shooter, physics, index, mobjs, skyflat, ctx)
    else:  # pistol, chaingun (SSG never fires in Doom 1 maps)
        # NOTE: vanilla chainguns bark through the pistol lump.
        audio.play("pistol", shooter.x, shooter.y, shooter)
        slope = bullet_slope(shooter, physics, index, mobjs, skyflat)
        gunshot(shooter, accurate, slope, physics, index, mobjs,
                skyflat, ctx)
    return COOLDOWN[weapon]
