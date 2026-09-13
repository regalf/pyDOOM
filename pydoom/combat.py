"""Combat: damage, missiles, hitscan (p_inter damage part + p_mobj
missiles + p_map hitscan + E1 attack actions).

Covers P_DamageMobj/P_KillMobj (no armor/powers), P_SpawnMissile/
P_CheckMissileSpawn/P_ExplodeMissile, P_RadiusAttack (A_Explode),
P_AimLineAttack/P_LineAttack (+Aim/Shoot traversers), P_SpawnPuff/
P_SpawnBlood, P_BulletSlope/P_GunShot, and the E1 monster attack
actions (Pos/SPos/Troop/Sarg/Head/Bruis/Skull) registered into
ai.ACTIONS by register_combat_actions().

Scope (documented, never silent):

* No godmode, powers or intermission counts: health only.
  Item drops on death spawn always (vanilla clip/shotgun table).
* No sounds. Muzzle flash/extralight and weapon psprites arrive with
  p_pspr; the viewer fires hitscan directly with its own cooldown.
* P_ShootSpecialLine never fires (shootable switches don't trigger).
* Missiles always explode on contact, even against sky ceilings (the
  sky hack needs the ceiling line, untracked here).
* Player death is reported through health only; respawning is the
  viewer's job.
"""

from __future__ import annotations

from pydoom import tables
from pydoom.player import PW_INVULN, CF_GODMODE, WP_CHAINSAW
from pydoom.angles import point_to_angle2
from pydoom.fixed import FRACBITS, FRACUNIT, c_div, fixed_div, fixed_mul
from pydoom.info import MF_FLAGS, MOBJ_TYPES, MT_INDEX, MT_NAMES
from pydoom.mapdata import ML_TWOSIDED
from pydoom.m_random import p_random

__all__ = [
    "MISSILERANGE",
    "SKULLSPEED",
    "BASETHRESHOLD",
    "MELEERANGE",
    "damage_mobj",
    "kill_mobj",
    "spawn_missile",
    "check_missile_spawn",
    "explode_missile",
    "radius_attack",
    "aim_line_attack",
    "line_attack",
    "bullet_slope",
    "gunshot",
    "fire_pistol",
    "spawn_puff",
    "spawn_blood",
    "skull_slam",
    "register_combat_actions",
]

MISSILERANGE = 32 * 64 * FRACUNIT  # p_local.h
SKULLSPEED = 20 * FRACUNIT  # p_enemy.c local define
BASETHRESHOLD = 100  # p_local.h
MELEERANGE = 64 * FRACUNIT

_U32 = 0xFFFFFFFF

_MF_SOLID = MF_FLAGS["MF_SOLID"]
_MF_SHOOTABLE = MF_FLAGS["MF_SHOOTABLE"]
_MF_NOBLOOD = MF_FLAGS["MF_NOBLOOD"]
_MF_NOCLIP = MF_FLAGS["MF_NOCLIP"]
_MF_SKULLFLY = MF_FLAGS["MF_SKULLFLY"]
_MF_MISSILE = MF_FLAGS["MF_MISSILE"]
_MF_NOGRAVITY = MF_FLAGS["MF_NOGRAVITY"]
_MF_FLOAT = MF_FLAGS["MF_FLOAT"]
_MF_INFLOAT = MF_FLAGS["MF_INFLOAT"]
_MF_CORPSE = MF_FLAGS["MF_CORPSE"]
_MF_COUNTKILL = MF_FLAGS["MF_COUNTKILL"]
_MF_DROPOFF = MF_FLAGS["MF_DROPOFF"]
_MF_JUSTHIT = MF_FLAGS["MF_JUSTHIT"]
_MF_SPAWNCEILING = MF_FLAGS["MF_SPAWNCEILING"]


# MOBJ_TYPES tuple indices (see gen_info.py).
_I_SPAWNHEALTH = 2
_I_SEESTATE = 7
_I_PAINSTATE = 11
_I_DEATHSTATE = 12
_I_XDEATHSTATE = 13
_I_PAINCHANCE = 15
_I_MASS = 16
_I_DAMAGE = 17
_I_SPEED = 10


def _info(mo):
    return MOBJ_TYPES[mo.type]


def damage_mobj(target, inflictor, source, damage: int, ctx=None) -> None:
    """P_DamageMobj with player armor/invulnerability/godmode."""
    from pydoom.mobjs import set_mobj_state

    if not (target.flags & _MF_SHOOTABLE):
        return  # shouldn't happen...
    if target.health <= 0:
        return
    if target.flags & _MF_SKULLFLY:
        target.momx = target.momy = target.momz = 0
    if getattr(target, "is_player", False) and ctx is not None \
            and getattr(ctx, "skill", "normal") == "baby":
        damage >>= 1  # NOTE: trainer mode halves what you take
    ps = (getattr(ctx, "player_state", None) if ctx is not None else None)
    if getattr(target, "is_player", False) and ps is not None:
        if target.sector is not None and target.sector.special == 11 \
                and damage >= target.health:
            damage = target.health - 1  # end-of-game hell hack
        # NOTE: below 1000, god/invuln ignore damage (telefrag goes thru).
        if damage < 1000 and (ps.powers.get(PW_INVULN)
                              or ps.cheats & CF_GODMODE):
            return
        if ps.armortype:
            # NOTE: green saves 1/3, blue 1/2 (even on fumes: type clears).
            saved = (damage // 3 if ps.armortype == 1 else damage // 2)
            if ps.armorpoints <= saved:
                saved = ps.armorpoints
                ps.armortype = 0
            ps.armorpoints -= saved
            damage -= saved
        # NOTE: red flash tracks post-armor damage, capped at 100.
        ps.damagecount = min(100, ps.damagecount + damage)
    chainsawing = (ps is not None and ps.readyweapon == WP_CHAINSAW)
    if (inflictor is not None and not (target.flags & MF_FLAGS["MF_NOCLIP"])
            and (source is None or not getattr(source, "is_player", False)
                 or not chainsawing)):
        # NOTE: close-combat saws don't shove victims out of reach.
        ang = point_to_angle2(inflictor.x, inflictor.y, target.x, target.y)
        mass = _info(target)[_I_MASS]
        thrust = damage * (FRACUNIT >> 3) * 100 // mass if mass else 0
        if (damage < 40 and damage > target.health
                and target.z - inflictor.z > 64 * FRACUNIT
                and (p_random() & 1)):
            ang = (ang + 0x80000000) & _U32
            thrust *= 4
        fa = (ang & _U32) >> 19
        target.momx += fixed_mul(thrust, tables.finecosine(fa))
        target.momy += fixed_mul(thrust, tables.finesine[fa])
    target.health -= damage
    if getattr(target, "is_player", False):
        target.attacker = source  # NOTE: face-turn tracking (no AI use)
    if target.health <= 0:
        kill_mobj(source, target, ctx)
        return
    painchance = _info(target)[_I_PAINCHANCE]
    if p_random() < painchance and not (target.flags & _MF_SKULLFLY):
        target.flags |= _MF_JUSTHIT  # fight back!
        set_mobj_state(target, _info(target)[_I_PAINSTATE], ctx)
    target.reactiontime = 0  # we're awake now...
    if ((not target.threshold or target.type == MT_INDEX["VILE"])
            and source is not None and source is not target
            and source.type != MT_INDEX["VILE"]):
        target.target = source
        target.threshold = BASETHRESHOLD
        if (target.state == target.spawnstate
                and target.seestate != 0):  # S_NULL
            set_mobj_state(target, target.seestate, ctx)


def kill_mobj(source, target, ctx=None) -> None:
    """P_KillMobj with clip/shotgun drops and the intermission tally."""
    from pydoom.mobjs import set_mobj_state

    if target.flags & _MF_COUNTKILL and ctx is not None:
        # NOTE: every COUNTKILL death tallies, infights included.
        ps = getattr(ctx, "player_state", None)
        if ps is not None:
            ps.killcount += 1

    target.flags &= ~(_MF_SHOOTABLE | _MF_FLOAT | _MF_SKULLFLY)
    if target.type != MT_INDEX["SKULL"]:
        target.flags &= ~MF_FLAGS["MF_NOGRAVITY"]
    target.flags |= _MF_CORPSE | _MF_DROPOFF
    target.height >>= 2
    if target.is_player:
        target.flags &= ~_MF_SOLID  # player corpse is walkable
    rec = _info(target)
    if target.health < -rec[_I_SPAWNHEALTH] and rec[_I_XDEATHSTATE]:
        set_mobj_state(target, rec[_I_XDEATHSTATE], ctx)  # gibbed
    else:
        set_mobj_state(target, rec[_I_DEATHSTATE], ctx)
    target.tics -= p_random() & 3
    if target.tics < 1:
        target.tics = 1
    # NOTE: death cries ride the DIE2/XDIE2 states (A_Scream family).
    if ctx is not None:
        # NOTE: vanilla clip/shotgun/chaingun drops, always spawned
        # (dropped = half ammo); no ctx-missing escape hatch.
        drop = {MT_INDEX["POSSESSED"]: MT_INDEX["CLIP"],
                MT_INDEX["SHOTGUY"]: MT_INDEX["SHOTGUN"],
                MT_INDEX["WOLFSS"]: MT_INDEX["CLIP"],
                MT_INDEX["CHAINGUY"]: MT_INDEX["CHAINGUN"]}
        if target.type in drop:
            from pydoom.mobjs import spawn_mobj
            phys = ctx.physics
            th = spawn_mobj(None, phys, phys.things, target.x, target.y,
                            -1, drop[target.type])
            th.flags |= MF_FLAGS["MF_DROPPED"]
            ctx.mobjs.append(th)


def spawn_missile(source, dest, mt: int, physics, index, mobjs) -> object:
    """P_SpawnMissile: aimed projectile, linked and listed."""
    from pydoom.mobjs import spawn_mobj
    from pydoom.info import MOBJ_TYPES

    th = spawn_mobj(None, physics, index,
                    source.x, source.y, source.z + 32 * FRACUNIT, mt)
    th.target = source  # where it came from
    an = point_to_angle2(source.x, source.y, dest.x, dest.y)
    if dest.flags & MF_FLAGS["MF_SHADOW"]:
        an = (an + ((p_random() - p_random()) << 20)) & _U32
    th.angle = an
    speed = MOBJ_TYPES[mt][_I_SPEED]
    fa = (an & _U32) >> 19
    th.momx = fixed_mul(speed, tables.finecosine(fa))
    th.momy = fixed_mul(speed, tables.finesine[fa])
    from pydoom.physics import aprox_distance
    dist = aprox_distance(dest.x - source.x, dest.y - source.y) // speed
    if dist < 1:
        dist = 1
    th.momz = (dest.z - source.z) // dist
    check_missile_spawn(th, physics)
    mobjs.append(th)
    return th


def check_missile_spawn(th, physics) -> None:
    """P_CheckMissileSpawn: nudge forward, explode if stuck."""
    th.tics -= p_random() & 3
    if th.tics < 1:
        th.tics = 1
    th.x += th.momx >> 1
    th.y += th.momy >> 1
    th.z += th.momz >> 1
    ok, _crossed = physics.try_move(th, th.x, th.y)
    if not ok:
        explode_missile(th, None)


def explode_missile(mo, ctx=None) -> None:
    """P_ExplodeMissile: stop, death state, deathsound (A_Explode booms
    separately for rockets/barrels)."""
    from pydoom import audio
    from pydoom.mobjs import set_mobj_state
    mo.momx = mo.momy = mo.momz = 0
    set_mobj_state(mo, _info(mo)[_I_DEATHSTATE], ctx)
    # NOTE: vanilla P_ExplodeMissile jitters the death tics (every
    # rocket impact draws once); without it the stream under-consumes.
    mo.tics -= p_random() & 3
    if mo.tics < 1:
        mo.tics = 1
    mo.flags &= ~MF_FLAGS["MF_MISSILE"]  # NOTE: spent shells go inert
    voice = audio.MISSILE_DEATHS.get(MT_NAMES[mo.type])
    if voice is not None:
        audio.play(voice, mo.x, mo.y, mo)


def radius_attack(spot, source, damage: int, ctx) -> None:
    """P_RadiusAttack: splash damage around spot (A_Explode)."""
    from pydoom import ai as _ai
    from pydoom.mapdata import MAPBLOCKSHIFT, MAXRADIUS
    physics, index = ctx.physics, ctx.physics.things
    # NOTE: the C expression overflows 32 bits (undefined behavior that
    # x86 wraps); replicate the wrap or the block range explodes to
    # billions of blocks and hangs. Wrapped, damage=128 covers ±1 block.
    dist = ((damage + MAXRADIUS) << FRACBITS) & 0xFFFFFFFF
    if dist >= 0x80000000:
        dist -= 0x100000000
    bm = physics.map.blockmap
    yh = (spot.y + dist - bm.orgy) >> MAPBLOCKSHIFT
    yl = (spot.y - dist - bm.orgy) >> MAPBLOCKSHIFT
    xh = (spot.x + dist - bm.orgx) >> MAPBLOCKSHIFT
    xl = (spot.x - dist - bm.orgx) >> MAPBLOCKSHIFT
    for bx in range(xl, xh + 1):
        for by in range(yl, yh + 1):
            if index is None:
                continue
            for thing in list(index.iter_block(bx, by)):
                if not (thing.flags & _MF_SHOOTABLE):
                    continue
                if thing.type in (MT_INDEX["CYBORG"], MT_INDEX["SPIDER"]):
                    continue  # immune to concussion
                dx = abs(thing.x - spot.x)
                dy = abs(thing.y - spot.y)
                dist = (dx if dx > dy else dy) - thing.radius
                dist >>= FRACBITS
                if dist < 0:
                    dist = 0
                if dist >= damage:
                    continue  # out of range
                if _ai.check_sight(thing, spot, ctx):
                    damage_mobj(thing, spot, source, damage - dist, ctx)


def spawn_puff(x: int, y: int, z: int, physics, index, mobjs,
               melee: bool = False):
    """P_SpawnPuff: bullet puff decor (S_PUFF3 when melee)."""
    from pydoom.info import MT_INDEX, STATE_INDEX
    from pydoom.mobjs import set_mobj_state, spawn_mobj
    z += ((p_random() - p_random()) << 10)
    th = spawn_mobj(None, physics, index, x, y, z, MT_INDEX["PUFF"])
    th.momz = FRACUNIT
    th.tics -= p_random() & 3
    if th.tics < 1:
        th.tics = 1
    if melee:
        set_mobj_state(th, STATE_INDEX["S_PUFF3"])
    mobjs.append(th)
    return th


def spawn_blood(x: int, y: int, z: int, damage: int, physics, index,
                mobjs):
    """P_SpawnBlood: blood sprites, frame by damage."""
    from pydoom.info import MT_INDEX, STATE_INDEX
    from pydoom.mobjs import set_mobj_state, spawn_mobj
    z += ((p_random() - p_random()) << 10)
    th = spawn_mobj(None, physics, index, x, y, z, MT_INDEX["BLOOD"])
    th.momz = FRACUNIT * 2
    th.tics -= p_random() & 3
    if th.tics < 1:
        th.tics = 1
    if 9 <= damage <= 12:
        set_mobj_state(th, STATE_INDEX["S_BLOOD2"])
    elif damage < 9:
        set_mobj_state(th, STATE_INDEX["S_BLOOD3"])
    mobjs.append(th)
    return th


class _Shot:
    """Per-shot hitscan state (replaces the C globals)."""

    def __init__(self, shooter, angle: int, distance: int, slope: int,
                 damage: int, physics, index, mobjs, skyflat):
        self.shooter = shooter
        self.angle = angle
        self.distance = distance
        self.slope = slope
        self.damage = damage
        self.physics = physics
        self.index = index
        self.mobjs = mobjs
        self.skyflat = skyflat
        self.shootz = (shooter.z + (shooter.height >> 1) + 8 * FRACUNIT)
        self.attackrange = distance
        self.topslope = 100 * FRACUNIT // 160
        self.bottomslope = -(100 * FRACUNIT // 160)
        self.aimslope = slope
        self.linetarget = None


def _aim_traverse(shot: _Shot, intercept) -> bool:
    _frac, line, thing = intercept
    if line is not None:
        if not (line.flags & ML_TWOSIDED):
            return False
        opentop, openbottom, _r, _l = shot.physics.line_opening(line)
        if openbottom >= opentop:
            return False
        dist = fixed_mul(shot.attackrange, _frac)
        if line.frontsector.floorheight != line.backsector.floorheight:
            slope = fixed_div(openbottom - shot.shootz, dist)
            if slope > shot.bottomslope:
                shot.bottomslope = slope
        if line.frontsector.ceilingheight != line.backsector.ceilingheight:
            slope = fixed_div(opentop - shot.shootz, dist)
            if slope < shot.topslope:
                shot.topslope = slope
        return shot.topslope > shot.bottomslope
    th = thing
    assert th is not None
    if th is shot.shooter:
        return True
    if not (th.flags & _MF_SHOOTABLE):
        return True
    dist = fixed_mul(shot.attackrange, _frac)
    thingtopslope = fixed_div(th.z + th.height - shot.shootz, dist)
    if thingtopslope < shot.bottomslope:
        return True
    thingbottomslope = fixed_div(th.z - shot.shootz, dist)
    if thingbottomslope > shot.topslope:
        return True
    shot.aimslope = c_div(min(thingtopslope, shot.topslope)
                          + max(thingbottomslope, shot.bottomslope), 2)
    shot.linetarget = th
    return False


def _shoot_traverse(shot: _Shot, intercept) -> bool:
    frac, line, thing = intercept
    if line is not None:
        # NOTE: P_ShootSpecialLine never fires (see docstring).
        if not (line.flags & ML_TWOSIDED):
            _hit_line(shot, line, frac)
            return False
        opentop, openbottom, _r, _l = shot.physics.line_opening(line)
        dist = fixed_mul(shot.attackrange, frac)
        if line.frontsector.floorheight != line.backsector.floorheight:
            if fixed_div(openbottom - shot.shootz, dist) > shot.aimslope:
                _hit_line(shot, line, frac)
                return False
        if line.frontsector.ceilingheight != line.backsector.ceilingheight:
            if fixed_div(opentop - shot.shootz, dist) < shot.aimslope:
                _hit_line(shot, line, frac)
                return False
        return True
    th = thing
    assert th is not None
    if th is shot.shooter:
        return True
    if not (th.flags & _MF_SHOOTABLE):
        return True
    dist = fixed_mul(shot.attackrange, frac)
    thingtopslope = fixed_div(th.z + th.height - shot.shootz, dist)
    if thingtopslope < shot.aimslope:
        return True
    thingbottomslope = fixed_div(th.z - shot.shootz, dist)
    if thingbottomslope > shot.aimslope:
        return True
    frac2 = frac - fixed_div(10 * FRACUNIT, shot.attackrange)
    x, y, z = _trace_point(shot, frac2)
    # NOTE: vanilla records linetarget (A_Punch turns into the hit) and
    # punches don't spark (S_PUFF3 when attackrange is melee).
    shot.linetarget = th
    if th.flags & MF_FLAGS["MF_NOBLOOD"]:
        spawn_puff(x, y, z, shot.physics, shot.index, shot.mobjs,
                   melee=(shot.attackrange == MELEERANGE))
    else:
        spawn_blood(x, y, z, shot.damage, shot.physics, shot.index,
                    shot.mobjs)
    if shot.damage:
        damage_mobj(th, shot.shooter, shot.shooter, shot.damage,
                    getattr(shot, "ctx", None))
    return False


def _trace_point(shot: _Shot, frac: int):
    trace = shot.physics._trace
    x = trace[0] + fixed_mul(trace[2], frac)
    y = trace[1] + fixed_mul(trace[3], frac)
    z = (shot.shootz + fixed_mul(shot.aimslope,
                                 fixed_mul(frac, shot.attackrange)))
    return x, y, z


def _hit_line(shot: _Shot, line, frac: int) -> None:
    if line.special in (24, 46, 47):
        # NOTE: P_ShootSpecialLine impact specials (guns pop switches).
        world = getattr(getattr(shot, "ctx", None), "world", None)
        if world is not None:
            world.shoot_special_line(
                line, bool(getattr(shot.shooter, "is_player", False)))
    frac2 = frac - fixed_div(4 * FRACUNIT, shot.attackrange)
    x, y, z = _trace_point(shot, frac2)
    if shot.skyflat is not None and (
            line.frontsector.ceilingpic == shot.skyflat):
        if z > line.frontsector.ceilingheight:
            return  # don't shoot the sky!
        if (line.backsector is not None
                and line.backsector.ceilingpic == shot.skyflat):
            return  # sky hack wall
    spawn_puff(x, y, z, shot.physics, shot.index, shot.mobjs)


def aim_line_attack(shooter, angle: int, distance: int, physics, index,
                    mobjs, skyflat) -> tuple[int, object]:
    """P_AimLineAttack: slope (and target) for hitscan auto-aim."""
    fa = (angle & _U32) >> 19
    x2 = shooter.x + (distance >> FRACBITS) * tables.finecosine(fa)
    y2 = shooter.y + (distance >> FRACBITS) * tables.finesine[fa]
    shot = _Shot(shooter, angle, distance, 0, 0, physics, index, mobjs,
                 skyflat)
    physics.path_traverse(shooter.x, shooter.y, x2, y2, 3,
                          lambda inter: _aim_traverse(shot, inter))
    if shot.linetarget is not None:
        return shot.aimslope, shot.linetarget
    return 0, None


def line_attack(shooter, angle: int, distance: int, slope: int,
                damage: int, physics, index, mobjs, skyflat,
                ctx=None):
    """P_LineAttack: hitscan with puffs/blood/damage. Returns target."""
    fa = (angle & _U32) >> 19
    x2 = shooter.x + (distance >> FRACBITS) * tables.finecosine(fa)
    y2 = shooter.y + (distance >> FRACBITS) * tables.finesine[fa]
    shot = _Shot(shooter, angle, distance, slope, damage, physics, index,
                 mobjs, skyflat)
    shot.ctx = ctx
    physics.path_traverse(shooter.x, shooter.y, x2, y2, 3,
                          lambda inter: _shoot_traverse(shot, inter))
    return shot.linetarget


def bullet_slope(shooter, physics, index, mobjs, skyflat) -> int:
    """P_BulletSlope: auto-aim slope with side retries."""
    an = shooter.angle
    slope, target = aim_line_attack(shooter, an, MISSILERANGE, physics,
                                    index, mobjs, skyflat)
    if target is None:
        an = (an + (1 << 26)) & _U32
        slope, target = aim_line_attack(shooter, an, MISSILERANGE,
                                        physics, index, mobjs, skyflat)
        if target is None:
            an = (an - (2 << 26)) & _U32
            slope, target = aim_line_attack(shooter, an, MISSILERANGE,
                                            physics, index, mobjs,
                                            skyflat)
    return slope


def gunshot(shooter, accurate: bool, slope: int, physics, index, mobjs,
            skyflat, ctx=None):
    """P_GunShot: one 5d3 pellet, optional spread."""
    damage = 5 * ((p_random() % 3) + 1)
    angle = shooter.angle
    if not accurate:
        angle = (angle + ((p_random() - p_random()) << 18)) & _U32
    line_attack(shooter, angle, MISSILERANGE, slope, damage, physics,
                index, mobjs, skyflat, ctx)


def fire_pistol(shooter, physics, index, mobjs, skyflat, accurate: bool,
                ctx=None) -> None:
    """Viewer pistol shot (A_FirePistol minus psprites/ammo)."""
    slope = bullet_slope(shooter, physics, index, mobjs, skyflat)
    gunshot(shooter, accurate, slope, physics, index, mobjs, skyflat, ctx)


def skull_slam(tmthing, thing, ctx=None) -> bool:
    """Lost-soul impact helper for the physics damage hook."""
    from pydoom.info import MOBJ_TYPES
    damage = ((p_random() % 8) + 1) * MOBJ_TYPES[tmthing.type][_I_DAMAGE]
    damage_mobj(thing, tmthing, tmthing, damage, ctx)
    tmthing.flags &= ~_MF_SKULLFLY
    tmthing.momx = tmthing.momy = tmthing.momz = 0
    from pydoom.mobjs import set_mobj_state
    set_mobj_state(tmthing, MOBJ_TYPES[tmthing.type][1], ctx)
    return False  # stop moving


def missile_hit(mover, thing, ctx=None) -> bool:
    """PIT_CheckThing missile damage (species filtered by physics)."""
    if not (thing.flags & _MF_SHOOTABLE):
        return not (thing.flags & _MF_SOLID)
    damage = ((p_random() % 8) + 1) * MOBJ_TYPES[mover.type][_I_DAMAGE]
    damage_mobj(thing, mover, getattr(mover, "target", None), damage, ctx)
    return False  # don't traverse any more


def things_hit(mover, thing, ctx=None) -> bool:
    """Physics damage-hook dispatcher (skulls and missiles)."""
    if mover.flags & _MF_SKULLFLY:
        return skull_slam(mover, thing, ctx)
    return missile_hit(mover, thing, ctx)


def _face(actor) -> None:
    """A_FaceTarget: drop the ambush, snap, spray spectres (2 draws)."""
    from pydoom.angles import point_to_angle2
    if actor.target is None:
        return
    actor.flags &= ~MF_FLAGS["MF_AMBUSH"]
    actor.angle = point_to_angle2(actor.x, actor.y,
                                  actor.target.x, actor.target.y)
    if actor.target.flags & MF_FLAGS["MF_SHADOW"]:
        # NOTE: A_FaceTarget sprays spectres (vanilla <<21); the two
        # draws keep the P_Random stream aligned on infights.
        actor.angle = (actor.angle
                       + ((p_random() - p_random()) << 21)) & _U32


def _a_posattack(actor, ctx, pellets: int, sound: str) -> None:
    if actor.target is None:
        return
    _face(actor)
    from pydoom import audio
    audio.play(sound, actor.x, actor.y, actor)
    for _ in range(pellets):
        slope, _t = aim_line_attack(actor, actor.angle, MISSILERANGE,
                                    ctx.physics, ctx.physics.things,
                                    ctx.mobjs, ctx.skyflatnum)
        # NOTE: vanilla draws spread before damage (A_PosAttack); the
        # player gunshot below draws damage first, like A_FireShotgun.
        angle = (actor.angle + ((p_random() - p_random()) << 20)) & _U32
        damage = ((p_random() % 5) + 1) * 3
        line_attack(actor, angle, MISSILERANGE, slope, damage,
                    ctx.physics, ctx.physics.things, ctx.mobjs,
                    ctx.skyflatnum, ctx)


def a_posattack(actor, ctx) -> None:
    _a_posattack(actor, ctx, 1, "pistol")


def a_sposattack(actor, ctx) -> None:
    _a_posattack(actor, ctx, 3, "shotgn")


def a_troopattack(actor, ctx) -> None:
    if actor.target is None:
        return
    _face(actor)
    from pydoom import audio
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        audio.play("claw", actor.x, actor.y, actor)
        damage_mobj(actor.target, actor, actor, (p_random() % 8 + 1) * 3,
                    ctx)
        return
    audio.play("firsht", actor.x, actor.y, actor)
    spawn_missile(actor, actor.target, MT_INDEX["TROOPSHOT"], ctx.physics,
                  ctx.physics.things, ctx.mobjs)


def a_sargattack(actor, ctx) -> None:
    if actor.target is None:
        return
    _face(actor)
    from pydoom import audio
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        audio.play("claw", actor.x, actor.y, actor)
        damage_mobj(actor.target, actor, actor, (p_random() % 10 + 1) * 4,
                    ctx)


def a_headattack(actor, ctx) -> None:
    if actor.target is None:
        return
    _face(actor)
    from pydoom import audio
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        audio.play("claw", actor.x, actor.y, actor)
        damage_mobj(actor.target, actor, actor, (p_random() % 6 + 1) * 10,
                    ctx)
        return
    audio.play("firsht", actor.x, actor.y, actor)
    spawn_missile(actor, actor.target, MT_INDEX["HEADSHOT"], ctx.physics,
                  ctx.physics.things, ctx.mobjs)


def a_bruisattack(actor, ctx) -> None:
    if actor.target is None:
        return
    from pydoom import audio
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        audio.play("claw", actor.x, actor.y, actor)
        damage_mobj(actor.target, actor, actor, (p_random() % 8 + 1) * 10,
                    ctx)
        return
    audio.play("firsht", actor.x, actor.y, actor)
    spawn_missile(actor, actor.target, MT_INDEX["BRUISERSHOT"],
                  ctx.physics, ctx.physics.things, ctx.mobjs)


def a_skullattack(actor, ctx) -> None:
    if actor.target is None:
        return
    from pydoom.physics import aprox_distance
    dest = actor.target
    actor.flags |= _MF_SKULLFLY
    _face(actor)
    fa = (actor.angle & _U32) >> 19
    actor.momx = fixed_mul(SKULLSPEED, tables.finecosine(fa))
    actor.momy = fixed_mul(SKULLSPEED, tables.finesine[fa])
    dist = aprox_distance(dest.x - actor.x, dest.y - actor.y) // SKULLSPEED
    if dist < 1:
        dist = 1
    # NOTE: C truncating division (negative when the target is below).
    actor.momz = c_div(dest.z + (dest.height >> 1) - actor.z, dist)


def a_explode(actor, ctx) -> None:
    radius_attack(actor, actor.target, 128, ctx)


def _affliction_cry(actor, idx: int, player_sound: str) -> None:
    """Shared pain/death lookup (vanilla A_Pain/A_Scream placement)."""
    from pydoom import audio
    if getattr(actor, "is_player", False):
        audio.play(player_sound, actor.x, actor.y, actor)
    else:
        entry = audio.MONSTERS.get(MT_NAMES[actor.type])
        if entry is not None and entry[idx] is not None:
            audio.play(entry[idx], actor.x, actor.y, actor)


def a_pain(actor, ctx) -> None:
    # NOTE: multi-pellet re-hits reset the soundless first pain frame,
    # so one blast cries once instead of stacking (vanilla debounce).
    _affliction_cry(actor, 1, "plpain")


def _death_cry(actor, name: str) -> None:
    _affliction_cry(actor, 2, name)


def a_scream(actor, ctx) -> None:
    # NOTE: A_Scream cycles podth/bgdth variants (vanilla draws
    # P_Random here, so the death cry keeps the stream aligned).
    from pydoom import audio
    if getattr(actor, "is_player", False):
        audio.play("pldeth", actor.x, actor.y, actor)  # pdiehi: commercial
        return
    entry = audio.MONSTERS.get(MT_NAMES[actor.type])
    sound = entry[2] if entry is not None else None
    if sound in ("podth1", "podth2", "podth3"):
        sound = f"podth{p_random() % 3 + 1}"
    elif sound in ("bgdth1", "bgdth2"):
        sound = f"bgdth{p_random() % 2 + 1}"
    if sound is not None:
        audio.play(sound, actor.x, actor.y, actor)


def a_xscream(actor, ctx) -> None:
    from pydoom import audio
    audio.play("slop", actor.x, actor.y, actor)


def a_playerscream(actor, ctx) -> None:
    from pydoom import audio
    audio.play("pldeth", actor.x, actor.y, actor)  # NOTE: see a_scream


def a_fall(actor, ctx) -> None:
    actor.flags &= ~MF_FLAGS["MF_SOLID"]  # corpses never block


def a_bossdeath(actor, ctx) -> None:
    """A_BossDeath: episode boss effects (p_enemy.c).

    E1M8 barons drop the tag-666 floors (the exit chain continues on
    foot); E2M8 cyber / E3M8 spider exit the level outright
    (G_ExitLevel, no floor); E4 arms arrive with Ultimate.
    """
    world = getattr(ctx, "world", None)
    game_map = getattr(world, "map", None)
    marker = getattr(game_map, "marker", "")
    key = (marker, actor.type)
    if key == ("E1M8", MT_INDEX["BRUISER"]):
        effect = "floor666"
    elif key in (("E2M8", MT_INDEX["CYBORG"]),
                 ("E3M8", MT_INDEX["SPIDER"])):
        effect = "exit"
    else:
        # NOTE: E4M6 (cyber door) / E4M8 (spider floor) land with retail.
        return
    players = getattr(ctx, "players", None) or []
    if not any(getattr(p, "health", 0) > 0 for p in players):
        return  # no one left alive, so do not end game
    for mo in getattr(ctx, "mobjs", None) or []:
        if mo is not actor and not mo.dead and mo.type == actor.type \
                and mo.health > 0:
            return  # other boss not dead
    if effect == "floor666":
        world.lower_floors_by_tag(666)
    else:
        world.exit_kind = "normal"


def a_keendie(actor, ctx) -> None:
    """A_KeenDie (p_enemy.c): when every keen is dead, the tag-666
    doors swing open (E4M2; harmless elsewhere, keens never spawn)."""
    from pydoom.doors import DoorType
    a_fall(actor, ctx)
    for mo in getattr(ctx, "mobjs", None) or []:
        if mo is not actor and mo.type == actor.type and mo.health > 0:
            return  # other Keen not dead
    world = getattr(ctx, "world", None)
    if world is not None:
        world.open_doors_by_tag(666, DoorType.OPEN)


COMBAT_ACTIONS = {
    "A_PosAttack": a_posattack,
    "A_SPosAttack": a_sposattack,
    "A_TroopAttack": a_troopattack,
    "A_SargAttack": a_sargattack,
    "A_HeadAttack": a_headattack,
    "A_BruisAttack": a_bruisattack,
    "A_SkullAttack": a_skullattack,
    "A_Explode": a_explode,
    "A_Scream": a_scream,
    "A_XScream": a_xscream,
    "A_Pain": a_pain,
    "A_PlayerScream": a_playerscream,
    "A_Fall": a_fall,
    "A_BossDeath": a_bossdeath,
    "A_KeenDie": a_keendie,
}


def register_combat_actions() -> None:
    """Plug combat actions into ai.ACTIONS (called by viewer/tests)."""
    from pydoom import ai as _ai
    _ai.ACTIONS.update(COMBAT_ACTIONS)
