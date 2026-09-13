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
            WP_MISSILE: 20, WP_PLASMA: 23, WP_BFG: 60, WP_CHAINSAW: 8,
            WP_SSG: 44}
# NOTE: held-trigger cycles (A_ReFire runs at the refire state's
# ENTRY, skipping its tail: pistol 4+6+4, plasma 3, BFG 20+10+10,
# fist 4+4+5+4, shotgun up to SGUN8; chaingun/missile/saw tails are
# 0-tic so tap and held match, except the saw bites twice a cycle).
HELD_COOLDOWN = {WP_FIST: 17, WP_PISTOL: 14, WP_SHOTGUN: 37,
                 WP_CHAINGUN: 4, WP_MISSILE: 20, WP_PLASMA: 3,
                 WP_BFG: 40, WP_CHAINSAW: 8, WP_SSG: 44}
# NOTE: cooldown value at which a held trigger re-pulls mid-cycle
# (the refire-entry tic: FULL - SHORT). A fresh pull lands on 0 and
# runs the whole tail; a pull here chains the short cycle instead.
REFIRE_AT = {w: COOLDOWN[w] - HELD_COOLDOWN[w] for w in COOLDOWN}


def chained_pull(cd_now: int, atkheld: bool, weapon: int) -> bool:
    """True when a held trigger re-pulls on this tic (vanilla A_ReFire).

    A fresh cycle re-pulls at REFIRE_AT (its refire-state entry);
    a chained cycle is already short, so its refire sits at 0.
    A fresh pull lands on 0 with atkheld False and runs the full tail.
    """
    if cd_now == 0 and atkheld:
        return True
    refire_at = 0 if atkheld else REFIRE_AT.get(weapon, 0)
    return refire_at > 0 and cd_now == refire_at
# NOTE: muzzle-flash lengths from the FLASH states (lights skipped).
FLASH_TICS = {WP_FIST: 0, WP_PISTOL: 7, WP_SHOTGUN: 7, WP_CHAINGUN: 5,
              WP_MISSILE: 7, WP_PLASMA: 4, WP_BFG: 17, WP_CHAINSAW: 0,
              WP_SSG: 7}
# NOTE: A_Light1/2 levels per flash (shotgun/BFG step up mid-flash).
FLASH_LIGHT = {WP_FIST: 0, WP_PISTOL: 1, WP_SHOTGUN: 1, WP_CHAINGUN: 1,
               WP_MISSILE: 1, WP_PLASMA: 1, WP_BFG: 1, WP_CHAINSAW: 0,
               WP_SSG: 1}
FLASH_LIGHT_STEP = {WP_SHOTGUN: (4, 2), WP_BFG: (11, 2),
                    }  # NOTE: (elapsed tics, level) once past the split
# NOTE: body frames across each cycle (one char per tic), from the
# attack states: the kick reads because it lasts, like vanilla.
ATTACK_BODY = {
    WP_FIST: "BBBBCCCCDDDDDCCCCBBBBB",
    # NOTE: pistol order is the vanilla state run (A wind-up, B fire,
    # C settle, B recover), so the kick lands on the bang, not the pull.
    WP_PISTOL: "AAAABBBBBBCCCCBBBBB",
    WP_SHOTGUN: ("AAAAAAAAAABBBBBCCCCCDDDDCCCCCBBBBBAAAAAAAAAA"),
    WP_CHAINGUN: "AABB",
    WP_MISSILE: "B" * 20,
    WP_PLASMA: "AAA" + "B" * 20,
    WP_BFG: "A" * 20 + "B" * 40,
    WP_CHAINSAW: "AAAABBBB",  # NOTE: two bites a cycle (SAW1+SAW2)
    WP_SSG: "A" * 44,
}
# NOTE: held-trigger timelines (refire-entry tails cut, like above).
HELD_BODY = {
    WP_FIST: "BBBBCCCCDDDDDCCCC",
    WP_PISTOL: "AAAABBBBBBCCCC",
    WP_SHOTGUN: "AAAAAAAAAABBBBBCCCCCDDDDCCCCCBBBBBAAA",
    WP_CHAINGUN: "AABB",
    WP_MISSILE: "B" * 20,
    WP_PLASMA: "AAA",
    WP_BFG: "A" * 20 + "B" * 20,
    WP_CHAINSAW: "AAAABBBB",
    WP_SSG: "A" * 44,
}


def attack_timeline(weapon: int, flip: int = 0,
                    held: bool = False) -> str:
    """Body frames for one attack cycle (render picks by elapsed tic).

    Chaingun pulls alternate whole AAAA/BBBB blocks per pull: each
    vanilla 4-tic pull shows a single frame (S_CHAIN1/S_CHAIN2), so
    the base "AABB" entries only pin the length.
    """
    table = HELD_BODY if held else ATTACK_BODY
    if weapon == WP_CHAINGUN:
        return "BBBB" if flip else "AAAA"
    return table[weapon]


def idle_frame(weapon: int, tics: int) -> str:
    """Ready-state body frame (vanilla psprite ready states).

    Only the saw animates at rest: S_SAW/S_SAWB alternate C/D every
    4 tics (blade up, bobbing). Every other ready state (S_PISTOL,
    S_SGUN, S_CHAIN, S_MISSILE, S_PLASMA, S_BFG, S_PUNCH) holds A.
    """
    if weapon == WP_CHAINSAW:
        return "D" if (tics // 4) & 1 else "C"
    return "A"
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
    from pydoom.info import MF_FLAGS
    attack_range = MELEERANGE + (65536 if saw else 0)
    # NOTE: vanilla draws damage first, then the SubRandom spread
    # (A_Punch/A_Saw): same count, but the values must land in order.
    damage = ((p_random() % 10) + 1) * 2
    if not saw and ps.powers.get(PW_STRENGTH):
        damage *= 10
    angle = (shooter.angle + ((p_random() - p_random()) << 18)) & 0xFFFFFFFF
    slope, _t = aim_line_attack(shooter, angle, attack_range,
                                physics, index, mobjs, skyflat)
    hit = line_attack(shooter, angle, attack_range, slope, damage,
                      physics, index, mobjs, skyflat, ctx)
    if hit is not None:
        # NOTE: vanilla A_Punch/A_Saw turn into the struck target.
        from pydoom.angles import point_to_angle2
        shooter.angle = point_to_angle2(shooter.x, shooter.y,
                                        hit.x, hit.y) & 0xFFFFFFFF
        if saw:
            # NOTE: the forward lunge (P_PlayerThink overrides the next
            # cmd) only lands on a hit: A_Saw returns early on a miss
            # (sawful), with no turn and no JUSTATTACKED.
            shooter.flags |= MF_FLAGS["MF_JUSTATTACKED"]
    # NOTE: vanilla never re-aims the shooter here (manual chainsaw
    # tracking); mo.target may mirror vanilla state but stays unread.


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


# NOTE: P_FireWeapon->fire-action windup in tics (vanilla psprite
# states): the shot lands W tics after the trigger pull, while noise
# alerts at the pull. Chaingun/plasma/saw fire on entry (+0). SSG is
# Doom-2-only (S_DSGUN1 runs 3): carried so stray ownership (old saves)
# can't KeyError the trigger, but it never fires in Doom 1 maps.
WINDUP = {WP_FIST: 4, WP_PISTOL: 4, WP_SHOTGUN: 3, WP_CHAINGUN: 0,
          WP_MISSILE: 8, WP_PLASMA: 0, WP_BFG: 30, WP_CHAINSAW: 0,
          WP_SSG: 3}
# NOTE: muzzle-flash delay (A_GunFlash on S_MISSILE1 fires at the pull,
# BFG flash on S_BFG2 at +20); every other flash rides its shot.
FLASH_DELAY = {WP_MISSILE: 0, WP_BFG: 20}  # default: WINDUP[weapon]


def fire(ps, shooter, physics, index, mobjs, skyflat, accurate: bool,
         ctx=None, queue=None, held: bool = False) -> tuple:
    """Trigger pull (P_FireWeapon). Returns (cooldown, flash_now).

    Cooldown gates the next pull (-1 holds: still switching, or just
    auto-switched off a dry gun). held chains the short refire-entry
    cycle (vanilla A_ReFire skips the tail); taps run the full one.
    Shots with windup land in tick_pending(); flash_now asks the
    viewer for this tic's flash.
    """
    if ps.pendingweapon != ps.readyweapon:
        return -1, False
    if not has_ammo_for(ps, ps.readyweapon):
        check_ammo(ps)
        return -1, False
    weapon = ps.readyweapon
    # NOTE: P_FireWeapon always alerts on a successful trigger pull
    # (every weapon, fists/saw included): the shot wakes the flood
    # region before any projectile/hitscan lands.
    if ctx is not None:
        from pydoom.ai import noise_alert
        noise_alert(shooter, shooter, ctx)
    from pydoom import audio
    if weapon == WP_BFG:
        # NOTE: A_BFGsound runs on S_BFG1 (the pull), 20 tics before
        # the flash and 30 before the shot, on every pull held or not.
        audio.play("bfg", shooter.x, shooter.y, shooter)
    cd = HELD_COOLDOWN[weapon] if held else COOLDOWN[weapon]
    windup = WINDUP[weapon]
    if weapon == WP_CHAINSAW and queue is not None:
        # NOTE: two bites a cycle (SAW1 now, SAW2 at +4).
        _shoot(ps, weapon, shooter, physics, index, mobjs, skyflat,
               accurate, ctx)
        queue.append({"weapon": weapon, "shot": 4, "flash": 0,
                      "accurate": accurate})
        return cd, True
    if windup <= 0 or queue is None:
        _shoot(ps, weapon, shooter, physics, index, mobjs, skyflat,
               accurate, ctx)
        return cd, True
    queue.append({"weapon": weapon, "shot": windup,
                  "flash": FLASH_DELAY.get(weapon, windup),
                  "accurate": accurate})
    return cd, FLASH_DELAY.get(weapon, windup) <= 0


def tick_pending(ps, shooter, physics, index, mobjs, skyflat, ctx,
                 queue) -> bool:
    """Advance scheduled shots; True when a muzzle flash shows. Shots
    need a live body on the same weapon (switching/death drops them,
    like lowering the gun mid-windup)."""
    flashed = False
    for pend in list(queue):
        pend["shot"] -= 1
        pend["flash"] -= 1
        if pend["flash"] == 0:
            flashed = True
        if pend["shot"] <= 0:
            queue.remove(pend)
            if shooter.health > 0 and ps.readyweapon == pend["weapon"] \
                    and ps.pendingweapon == ps.readyweapon:
                _shoot(ps, pend["weapon"], shooter, physics, index,
                       mobjs, skyflat, pend["accurate"], ctx)
    return flashed


def _shoot(ps, weapon, shooter, physics, index, mobjs, skyflat,
           accurate: bool, ctx=None) -> None:
    """The fire action itself (ammo, projectile/hitscan, shot sound)."""
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
        # NOTE: A_FirePlasma barks every shot, tap or 3-tic chain alike
        # (silent under shareware: no DSPLASMA lump, like the sprites).
        # NOTE: vanilla picks flashstate+(P_Random()&1) before spawning
        # (A_FirePlasma): the draw counts even though the flash sprite
        # rides our own psprite clock.
        audio.play("plasma", shooter.x, shooter.y, shooter)
        _flash = p_random() & 1
        spawn_player_missile(shooter, MT_INDEX["PLASMA"], physics, index,
                             mobjs)
    elif weapon == WP_BFG:
        # NOTE: shareware has no BFG lump either.
        # NOTE: A_FireBFG only launches; A_BFGSpray runs on the ball.
        # DIVERGENCE (commercial-only, no E1 impact): the spray below
        # fires at launch, vanilla sprays at ball impact, so the 40x15
        # P_Random draws land earlier in the stream.
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
