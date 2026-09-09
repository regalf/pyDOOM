"""Combat: damage, missiles, hitscan (p_inter damage part + p_mobj
missiles + p_map hitscan + E1 attack actions).

Covers P_DamageMobj/P_KillMobj (no armor/powers), P_SpawnMissile/
P_CheckMissileSpawn/P_ExplodeMissile, P_RadiusAttack (A_Explode),
P_AimLineAttack/P_LineAttack (+Aim/Shoot traversers), P_SpawnPuff/
P_SpawnBlood, P_BulletSlope/P_GunShot, and the E1 monster attack
actions (Pos/SPos/Troop/Sarg/Head/Bruis/Skull) registered into
ai.ACTIONS by register_combat_actions().

Scope (documented, never silent):

* No armor, godmode, powers or intermission counts: health only.
  Item drops on death are skipped (no pickups yet).
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
from pydoom.player import PW_INVULN, WP_CHAINSAW
from pydoom.angles import point_to_angle2
from pydoom.fixed import FRACBITS, FRACUNIT, fixed_div, fixed_mul
from pydoom.info import MF_FLAGS, MOBJ_TYPES, MT_INDEX, STATE_INDEX
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
    """P_DamageMobj with player armor/invulnerability, sans cheats."""
    from pydoom.mobjs import set_mobj_state

    if not (target.flags & _MF_SHOOTABLE):
        return  # shouldn't happen...
    if target.health <= 0:
        return
    if target.flags & _MF_SKULLFLY:
        target.momx = target.momy = target.momz = 0
    ps = (getattr(ctx, "player_state", None) if ctx is not None else None)
    if getattr(target, "is_player", False) and ps is not None:
        if target.sector is not None and target.sector.special == 11 \
                and damage >= target.health:
            damage = target.health - 1  # end-of-game hell hack
        # NOTE: below 1000, god/invuln ignore damage (telefrag goes thru).
        if damage < 1000 and ps.powers.get(PW_INVULN):
            return
        if ps.armortype:
            # NOTE: green saves 1/3, blue 1/2 (even on fumes: type clears).
            saved = (damage // 3 if ps.armortype == 1 else damage // 2)
            if ps.armorpoints <= saved:
                saved = ps.armorpoints
                ps.armortype = 0
            ps.armorpoints -= saved
            damage -= saved
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
    """P_KillMobj with clip/shotgun drops, sans counts/player-flow."""
    from pydoom.mobjs import set_mobj_state

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
    if ctx is not None:
        # NOTE: vanilla clip/shotgun/chaingun drops (dropped = half ammo).
        drop = {MT_INDEX["POSSESSED"]: MT_INDEX["CLIP"],
                MT_INDEX["SHOTGUY"]: MT_INDEX["SHOTGUN"],
                MT_INDEX["WOLFSS"]: MT_INDEX["CLIP"],
                MT_INDEX["CHAINGUY"]: MT_INDEX["CHAINGUN"]}
        if target.type in drop:
            from pydoom.mobjs import spawn_mobj
            phys = getattr(ctx, "physics", None)
            index = getattr(phys, "things", None) if phys is not None else None
            mobjs = getattr(ctx, "mobjs", None)
            if phys is not None and index is not None and mobjs is not None:
                th = spawn_mobj(None, phys, index, target.x, target.y, -1,
                                drop[target.type])
                th.flags |= MF_FLAGS["MF_DROPPED"]
                mobjs.append(th)


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
    """P_ExplodeMissile: stop and play the death state (A_Explode booms)."""
    from pydoom.mobjs import set_mobj_state
    mo.momx = mo.momy = mo.momz = 0
    set_mobj_state(mo, _info(mo)[_I_DEATHSTATE], ctx)


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
    shot.aimslope = (min(thingtopslope, shot.topslope)
                     + max(thingbottomslope, shot.bottomslope)) // 2
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
    if th.flags & MF_FLAGS["MF_NOBLOOD"]:
        spawn_puff(x, y, z, shot.physics, shot.index, shot.mobjs)
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
    return None  # damage/puffs applied inline (linetarget is aim-only)


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
    from pydoom.angles import point_to_angle2
    if actor.target is not None:
        actor.angle = point_to_angle2(actor.x, actor.y,
                                      actor.target.x, actor.target.y)


def _a_posattack(actor, ctx, pellets: int) -> None:
    if actor.target is None:
        return
    _face(actor)
    for _ in range(pellets):
        slope, _t = aim_line_attack(actor, actor.angle, MISSILERANGE,
                                    ctx.physics, ctx.physics.things,
                                    ctx.mobjs, ctx.skyflatnum)
        damage = ((p_random() % 5) + 1) * 3
        angle = (actor.angle + ((p_random() - p_random()) << 20)) & _U32
        line_attack(actor, angle, MISSILERANGE, slope, damage,
                    ctx.physics, ctx.physics.things, ctx.mobjs,
                    ctx.skyflatnum, ctx)


def a_posattack(actor, ctx) -> None:
    _a_posattack(actor, ctx, 1)


def a_sposattack(actor, ctx) -> None:
    _a_posattack(actor, ctx, 3)


def a_troopattack(actor, ctx) -> None:
    if actor.target is None:
        return
    _face(actor)
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        damage_mobj(actor.target, actor, actor, (p_random() % 8 + 1) * 3,
                    ctx)
        return
    spawn_missile(actor, actor.target, MT_INDEX["TROOPSHOT"], ctx.physics,
                  ctx.physics.things, ctx.mobjs)


def a_sargattack(actor, ctx) -> None:
    if actor.target is None:
        return
    _face(actor)
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        damage_mobj(actor.target, actor, actor, (p_random() % 10 + 1) * 4,
                    ctx)


def a_headattack(actor, ctx) -> None:
    if actor.target is None:
        return
    _face(actor)
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        damage_mobj(actor.target, actor, actor, (p_random() % 6 + 1) * 10,
                    ctx)
        return
    spawn_missile(actor, actor.target, MT_INDEX["HEADSHOT"], ctx.physics,
                  ctx.physics.things, ctx.mobjs)


def a_bruisattack(actor, ctx) -> None:
    if actor.target is None:
        return
    from pydoom.ai import check_melee_range
    if check_melee_range(actor, ctx):
        damage_mobj(actor.target, actor, actor, (p_random() % 8 + 1) * 10,
                    ctx)
        return
    spawn_missile(actor, actor.target, MT_INDEX["BRUISERSHOT"],
                  ctx.physics, ctx.physics.things, ctx.mobjs)


def a_skullattack(actor, ctx) -> None:
    if actor.target is None:
        return
    dest = actor.target
    actor.flags |= _MF_SKULLFLY
    _face(actor)
    fa = (actor.angle & _U32) >> 19
    actor.momx = fixed_mul(SKULLSPEED, tables.finecosine(fa))
    actor.momy = fixed_mul(SKULLSPEED, tables.finesine[fa])
    dist = aprox_distance(dest.x - actor.x, dest.y - actor.y) // SKULLSPEED
    if dist < 1:
        dist = 1
    actor.momz = (dest.z + (dest.height >> 1) - actor.z) // dist


def a_explode(actor, ctx) -> None:
    radius_attack(actor, actor.target, 128, ctx)


def a_bossdeath(actor, ctx) -> None:
    """A_BossDeath: episode boss effects (p_enemy.c).

    E1M8 barons drop the tag-666 floors (the exit chain continues on
    foot); E2M8/E3M8 route out, re-mapped when those episodes land.
    """
    world = getattr(ctx, "world", None)
    game_map = getattr(world, "map", None)
    marker = getattr(game_map, "marker", "")
    if not marker.startswith("E1M8") or actor.type != MT_INDEX["BRUISER"]:
        # NOTE: E2 (cyber) / E3 (spider) arms arrive with their maps.
        return
    players = getattr(ctx, "players", None) or []
    if not any(getattr(p, "health", 0) > 0 for p in players):
        return  # no one left alive, so do not end game
    for mo in getattr(ctx, "mobjs", None) or []:
        if mo is not actor and not mo.dead and mo.type == actor.type \
                and mo.health > 0:
            return  # other boss not dead
    world.lower_floors_by_tag(666)


COMBAT_ACTIONS = {
    "A_PosAttack": a_posattack,
    "A_SPosAttack": a_sposattack,
    "A_TroopAttack": a_troopattack,
    "A_SargAttack": a_sargattack,
    "A_HeadAttack": a_headattack,
    "A_BruisAttack": a_bruisattack,
    "A_SkullAttack": a_skullattack,
    "A_Explode": a_explode,
    "A_BossDeath": a_bossdeath,
}


def register_combat_actions() -> None:
    """Plug combat actions into ai.ACTIONS (called by viewer/tests)."""
    from pydoom import ai as _ai
    _ai.ACTIONS.update(COMBAT_ACTIONS)
