"""Map objects: spawn, thing links, thinker-lite (p_mobj.c movement part).

Covers P_SpawnMobj/P_SpawnMapThing (skill filter, ambush, ceiling
placement), P_SetMobjState (no action functions yet), P_MobjThinker
(momentum/friction/gravity/state countdown only), P_XYMovement (with the
player slide/stop branches, driven by ctx.player_state) and
P_ZMovement (with floater target-tracking; no skull slams, missiles
or crush damage), plus
sector thinglists and blockmap links (P_SetThingPosition /
P_UnsetThingPosition).

Scope (documented, never silent):

* No AI or weapon actions: state actions are never called, so monsters
  stand in their spawn frames (statues) and items sit still. Missiles
  explode on contact (death anim + radius via A_Explode when ctx has
  combat actions); skull slams need the physics damage hook.
* No players, deathmatch or multiplayer: thing types 1-4 and 11 are
  skipped, multiplayer-only (options bit 4) and MF_NOTDMATCH types are
  skipped, MF_AMBUSH is set from options bit 3 (deaf, for later AI).
* No pickups or damage: special (pickup) things are walkable and
  reported as touched; blocking, touching and removal follow
  PIT_CheckThing, minus P_TouchSpecialThing/P_DamageMobj.
* Skill filtering defaults to selonormal ("normal" bit); pass another
  skill name to spawn a different population.
* Movers and mobjs share duck-typed bodies, so physics.try_move and
  slide_move work on both.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydoom.fixed import FRACUNIT, c_div, fixed_mul
from pydoom.info import MF_FLAGS, MT_INDEX, STATES, STATE_INDEX, type_record
from pydoom.mapdata import MAPBLOCKSHIFT, Map
from pydoom.m_random import p_random
from pydoom.player import CF_NOMOMENTUM

__all__ = [
    "SKILL_BITS",
    "STOPSPEED",
    "FRICTION",
    "GRAVITY",
    "MAXMOVE",
    "FLOATSPEED",
    "Mobj",
    "ThingIndex",
    "spawn_map",
    "spawn_mobj",
    "set_mobj_state",
    "think_mobj",
    "xy_movement",
    "refresh_sector",
    "level_totals",
]

# Skill option bits (P_SpawnMapThing): baby->1, nightmare->4, else medium.
SKILL_BITS = {"baby": 1, "easy": 1, "normal": 2, "hard": 4, "nightmare": 4}

STOPSPEED = 0x1000  # p_mobj.c
FRICTION = 0xE800
GRAVITY = FRACUNIT  # p_local.h
MAXMOVE = 30 * FRACUNIT  # p_local.h
FLOATSPEED = 4 * FRACUNIT  # p_local.h

_MF_SOLID = MF_FLAGS["MF_SOLID"]
_MF_SHOOTABLE = MF_FLAGS["MF_SHOOTABLE"]
_MF_SPECIAL = MF_FLAGS["MF_SPECIAL"]
_MF_SPAWNCEILING = MF_FLAGS["MF_SPAWNCEILING"]
_MF_NOGRAVITY = MF_FLAGS["MF_NOGRAVITY"]
_MF_AMBUSH = MF_FLAGS["MF_AMBUSH"]
_MF_NOTDMATCH = MF_FLAGS["MF_NOTDMATCH"]
_MF_MISSILE = MF_FLAGS["MF_MISSILE"]
_MF_NOCLIP = MF_FLAGS["MF_NOCLIP"]
_MF_SKULLFLY = MF_FLAGS["MF_SKULLFLY"]
_MF_FLOAT = MF_FLAGS["MF_FLOAT"]
_MF_INFLOAT = MF_FLAGS["MF_INFLOAT"]
_MF_CORPSE = MF_FLAGS["MF_CORPSE"]
_MF_DROPPED = MF_FLAGS["MF_DROPPED"]
_MF_COUNTKILL = MF_FLAGS["MF_COUNTKILL"]
_MF_COUNTITEM = MF_FLAGS["MF_COUNTITEM"]


@dataclass(eq=False)
class Mobj:
    """Live map object (player-less subset of mobj_t)."""

    x: int = 0
    y: int = 0
    z: int = 0
    momx: int = 0
    momy: int = 0
    momz: int = 0
    angle: int = 0  # BAM
    sprite: int = 0
    frame: int = 0
    tics: int = -1
    state: int = 0  # index into info.STATES
    type: int = 0  # MT_ index
    doomednum: int = -1
    radius: int = 0
    height: int = 0
    health: int = 0
    flags: int = 0
    reactiontime: int = 0
    threshold: int = 0
    movecount: int = 0
    movedir: int = 0
    lastlook: int = 0  # P_LookForPlayers rotation (p_random at spawn)
    speed: int = 0  # chase step multiplier (mobjinfo)
    spawnstate: int = 0
    seestate: int = 0
    meleestate: int = 0
    missilestate: int = 0
    target: "Mobj | None" = field(default=None, repr=False)
    attacker: "Mobj | None" = field(default=None, repr=False)  # face-turn
    spawnpoint: object = field(default=None, repr=False)  # map thing
    floorz: int = 0
    ceilingz: int = 0
    sector: object = field(default=None, repr=False)
    is_player: bool = False
    dead: bool = False


class ThingIndex:
    """Blockmap thing links (blocklinks lite): pos -> mobjs in block."""

    def __init__(self, game_map: Map) -> None:
        self.map = game_map
        self.blocks: dict[tuple[int, int], list[Mobj]] = {}

    def _key(self, x: int, y: int) -> tuple[int, int] | None:
        bm = self.map.blockmap
        bx = (x - bm.orgx) >> MAPBLOCKSHIFT
        by = (y - bm.orgy) >> MAPBLOCKSHIFT
        if 0 <= bx < bm.width and 0 <= by < bm.height:
            return (bx, by)
        return None  # thing is off the map

    def link(self, mo: Mobj) -> None:
        key = self._key(mo.x, mo.y)
        if key is not None:
            self.blocks.setdefault(key, []).append(mo)

    def unlink(self, mo: Mobj) -> None:
        # Scan for the mobj (positions change; single-linked in C).
        for key, cell in list(self.blocks.items()):
            if mo in cell:
                cell.remove(mo)
                if not cell:
                    del self.blocks[key]
                return

    def move(self, mo: Mobj, x: int, y: int) -> None:
        self.unlink(mo)
        mo.x, mo.y = x, y
        self.link(mo)

    def relink(self, mo: Mobj) -> bool:
        """P_SetThingPosition for a placed thing: re-file it under its
        current block. No-op when never linked (camera bodies stay out
        of the index); off-map things drop out like link() does."""
        for key, cell in list(self.blocks.items()):
            if mo in cell:
                newkey = self._key(mo.x, mo.y)
                if newkey == key:
                    return True
                cell.remove(mo)
                if not cell:
                    del self.blocks[key]
                if newkey is not None:
                    self.blocks.setdefault(newkey, []).append(mo)
                return True
        return False

    def iter_block(self, bx: int, by: int):
        return iter(self.blocks.get((bx, by), ()))


def spawn_mobj(game_map, physics, index: ThingIndex, x: int, y: int, z: int,
               mt: int) -> Mobj:
    """P_SpawnMobj: blank mobj in spawn state (no state actions yet)."""
    from pydoom.info import MOBJ_TYPES
    assert 0 <= mt < len(MOBJ_TYPES), f"bad mobjtype {mt}"
    (_doomednum, spawnstate, spawnhealth, reactiontime, radius, height,
     flags, seestate, meleestate, missilestate, speed, _pain, _death,
     _xdeath, _raise, _painchance, _mass, _dmg) = MOBJ_TYPES[mt]
    mo = Mobj(x=x, y=y, type=mt, radius=radius, height=height,
              health=spawnhealth, flags=flags, reactiontime=reactiontime,
              speed=speed, spawnstate=spawnstate, seestate=seestate,
              meleestate=meleestate, missilestate=missilestate,
              lastlook=p_random() & 3)
    sprite, frame, tics, _next, _action = STATES[spawnstate]
    mo.state, mo.tics, mo.sprite, mo.frame = spawnstate, tics, sprite, frame
    sub = physics.subsector_at(x, y)
    assert sub.sector is not None
    mo.sector = sub.sector
    mo.sector.thinglist.append(mo)
    mo.floorz = sub.sector.floorheight
    mo.ceilingz = sub.sector.ceilingheight
    if z == -1:  # ONFLOORZ
        mo.z = mo.floorz
    elif z == -2:  # ONCEILINGZ
        mo.z = mo.ceilingz - height
    else:
        mo.z = z
    index.link(mo)
    return mo


# NOTE: p_setup.c P_LoadThings skips these Doom2-only doomednums when
# the game is not commercial (shareware/registered alike).
_NON_SHAREWARE_TYPES = frozenset({68, 64, 88, 89, 69, 67, 71, 65, 66, 84})


def spawn_map(game_map: Map, physics, index: ThingIndex,
              skill: str = "normal", nomonsters: bool = False,
              player_hook=None) -> list[Mobj]:
    """P_SpawnMapThing over all map things, in lump order.

    The console player (type 1) spawns inline at its loop position, like
    vanilla's P_SpawnPlayer call inside the THINGS loop: its lastlook
    draw lands at the right point of the RNG stream. player_hook(mo,
    thing) receives the player body (None hook keeps the old behavior
    of skipping players, for unit tests).
    """
    bit = SKILL_BITS[skill]
    mobjs: list[Mobj] = []
    for thing in game_map.things:
        if thing.type == 11:
            continue  # deathmatch starts recorded elsewhere in vanilla
        if thing.type <= 4:
            # NOTE: vanilla records starts 1-4 and spawns the console
            # player inline (non-deathmatch); other starts wait.
            if thing.type == 1 and player_hook is not None:
                mo = spawn_mobj(game_map, physics, index,
                                thing.x << 16, thing.y << 16, -1,
                                MT_INDEX["PLAYER"])
                mo.angle = 0x20000000 * (thing.angle // 45)
                mobjs.append(mo)
                player_hook(mo, thing)
            continue  # players spawn separately (no player mobj here)
        if thing.type in _NON_SHAREWARE_TYPES:
            # NOTE: vanilla P_LoadThings breaks the whole loop here
            # (!commercial + Doom2-only type), it does not skip one.
            break
        if thing.options & 16:
            continue  # multiplayer-only
        if not (thing.options & bit):
            continue  # wrong skill level
        rec = type_record(thing.type)
        if nomonsters and rec is not None \
                and (rec["flags"] & _MF_COUNTKILL
                     or rec["mt"] == MT_INDEX["SKULL"]):
            # NOTE: -nomonsters skips kill-counted types plus lost souls
            # (vanilla also skips MT_SKULL, which is not COUNTKILL).
            continue
        if rec is None:
            raise ValueError(
                f"P_SpawnMapThing: unknown type {thing.type} "
                f"at ({thing.x}, {thing.y})")
        # NOTE: MF_NOTDMATCH skips only in deathmatch (vanilla); this
        # engine is single-player, so keys always spawn.
        mo = spawn_mobj(game_map, physics, index,
                        thing.x << 16, thing.y << 16,
                        -2 if rec["flags"] & _MF_SPAWNCEILING else -1,
                        rec["mt"])
        mo.doomednum = thing.type
        mo.angle = 0x20000000 * (thing.angle // 45)  # ANG45 * steps
        mo.spawnpoint = thing
        if skill == "nightmare":
            mo.reactiontime = 0  # NOTE: nightmare spawns pre-alerted
        if thing.options & 8:
            mo.flags |= _MF_AMBUSH
        if mo.tics > 0:
            mo.tics = 1 + (p_random() % mo.tics)
        mobjs.append(mo)
    return mobjs


def set_mobj_state(mo: Mobj, state: int, ctx=None) -> bool:
    """P_SetMobjState without action calls. False = removed (S_NULL).

    With a context, entering a state runs its action via
    ctx.call_action (AI arrives separately; unknown actions are no-ops).
    """
    while True:
        if state == 0:  # S_NULL
            mo.dead = True
            return False
        sprite, frame, tics, _next, action = STATES[state]
        mo.state, mo.tics, mo.sprite, mo.frame = state, tics, sprite, frame
        # NOTE: st->action(mobj) calls arrive with AI/weapons.
        if ctx is not None and action is not None:
            ctx.call_action(mo, action)
            if mo.dead:
                return False
        state = _next
        if mo.tics:
            return True


def level_totals(mobjs, sectors) -> tuple[int, int, int]:
    """Intermission denominators from what actually spawned (skill and
    deathmatch filters already applied): COUNTKILL mobjs, COUNTITEM
    mobjs, and special-9 sectors."""
    kills = sum(1 for mo in mobjs if mo.flags & _MF_COUNTKILL)
    items = sum(1 for mo in mobjs if mo.flags & _MF_COUNTITEM)
    secrets = sum(1 for sec in sectors if sec.special == 9)
    return kills, items, secrets


def think_mobj(mo: Mobj, physics, ctx=None) -> list:
    """P_MobjThinker lite: momentum, gravity, state countdown.

    Returns crossed special lines (for the caller to execute; statues
    never move so this stays empty until AI lands).
    """
    crossed: list = []
    if mo.momx or mo.momy or (mo.flags & _MF_SKULLFLY):
        ox, oy = mo.x, mo.y
        crossed.extend(xy_movement(mo, physics, ctx))
        if mo.dead:
            return crossed
        if (mo.x, mo.y) != (ox, oy):
            # Re-link like P_SetThingPosition after a move.
            refresh_sector(mo, physics)
    sec = mo.sector
    if sec is not None and sec.specialdata is not None:
        # NOTE: riding a lift: vanilla refreshes floorz on every move
        # (P_TryMove), so bodies track the platform instead of hovering.
        # Stationary bodies never move, hence this explicit re-glue.
        mo.floorz = sec.floorheight
        mo.ceilingz = sec.ceilingheight
    if mo.z != mo.floorz or mo.momz:
        _z_movement(mo, ctx)
        if mo.dead:
            return crossed
    if mo.tics != -1:
        mo.tics -= 1
        if not mo.tics:
            _sprite, _frame, _tics, nextstate, _action = STATES[mo.state]
            set_mobj_state(mo, nextstate, ctx)
    elif (ctx is not None and (getattr(ctx, "skill", "normal") == "nightmare"
                                or getattr(ctx, "respawn", False))
            and mo.spawnpoint is not None
            and (mo.flags & _MF_COUNTKILL)):
        # NOTE: S_NULL corpses qualify (tics -1, movecount aged); live
        # monsters never reach this branch (their states tick down).
        _maybe_respawn(mo, physics, ctx)
    return crossed


def _maybe_respawn(mo: Mobj, physics, ctx) -> None:
    """Nightmare corpse respawn (P_MobjThinker else-branch): after ~12s
    of death, on a 32-tic boundary and a ~2% roll, the monster returns
    at its spawnpoint in teleport fog (blocked spots wait)."""
    mo.movecount += 1  # NOTE: doubles as the corpse-age counter
    if mo.movecount < 12 * 35:
        return
    world = getattr(ctx, "world", None)
    if world is None or (world.time & 31):
        return
    if p_random() > 4:
        return
    from pydoom import audio
    from pydoom.info import MT_INDEX
    sp = mo.spawnpoint
    x, y = sp.x << 16, sp.y << 16
    res = physics.check_position(mo, x, y)
    if not res.ok:
        return  # NOTE: something camps the spot; retry later
    index = physics.things
    fog = spawn_mobj(None, physics, index, mo.x, mo.y,
                     mo.sector.floorheight if mo.sector is not None
                     else mo.floorz, MT_INDEX["TFOG"])
    fog.momz = 65536
    audio.play("telept", mo.x, mo.y, fog)
    if index is not None:
        index.unlink(mo)
    if mo.sector is not None:
        try:
            mo.sector.thinglist.remove(mo)
        except ValueError:
            pass
    mobjs = getattr(ctx, "mobjs", None)
    if mobjs is not None:
        if mo in mobjs:
            mobjs.remove(mo)
        dest = spawn_mobj(None, physics, index, x, y, -1, mo.type)
        dest.spawnpoint = sp
        dest.angle = 0x20000000 * (sp.angle // 45)
        if sp.options & 8:
            dest.flags |= _MF_AMBUSH
        dest.reactiontime = 18
        dest.movecount = 0
        mobjs.append(dest)
        fog2 = spawn_mobj(None, physics, index, x, y, dest.floorz,
                          MT_INDEX["TFOG"])
        fog2.momz = 65536
        audio.play("telept", x, y, fog2)
        mobjs.append(fog)
        mobjs.append(fog2)
    mo.dead = True


def refresh_sector(mo: Mobj, physics) -> None:
    """Re-link a moved mobj (sector thinglist + blocklinks)."""
    if physics.things is not None:
        physics.things.unlink(mo)
        physics.things.link(mo)
    sub = physics.subsector_at(mo.x, mo.y)
    if sub.sector is not None and sub.sector is not mo.sector:
        if mo.sector is not None:
            try:
                mo.sector.thinglist.remove(mo)
            except ValueError:
                pass
        mo.sector = sub.sector
        mo.sector.thinglist.append(mo)


def xy_movement(mo: Mobj, physics, ctx=None) -> list:
    """P_XYMovement: stepped slide-move, then friction or full stop.

    Players slide on blocked moves (P_SlideMove) instead of stopping,
    honor CF_NOMOMENTUM, and only stop below STOPSPEED with no move
    input held (walking frames return to S_PLAY); the last Ticcmd and
    cheats ride on ctx.player_state, like player->cmd/player->cheats.
    """
    crossed: list = []
    if not mo.momx and not mo.momy:
        if mo.flags & _MF_SKULLFLY:
            # NOTE: the skull slammed into something (p_mobj.c
            # P_XYMovement): stop, drop back to spawn, and chase
            # again from there instead of lunging in place.
            from pydoom.info import MOBJ_TYPES
            mo.flags &= ~_MF_SKULLFLY
            mo.momz = 0
            set_mobj_state(mo, MOBJ_TYPES[mo.type][1], ctx)
        return crossed
    if mo.momx > MAXMOVE:
        mo.momx = MAXMOVE
    elif mo.momx < -MAXMOVE:
        mo.momx = -MAXMOVE
    if mo.momy > MAXMOVE:
        mo.momy = MAXMOVE
    elif mo.momy < -MAXMOVE:
        mo.momy = -MAXMOVE
    xmove, ymove = mo.momx, mo.momy
    is_player = mo.is_player and not (mo.flags & _MF_MISSILE)
    while xmove or ymove:
        if xmove > MAXMOVE // 2 or ymove > MAXMOVE // 2:
            # NOTE: C halves with truncating division (xmove/2), not
            # floor: odd negatives step -188945, not -188946 (oracolo:
            # 1 subunit drift from tic 103 of E1M4TRIK otherwise).
            ptryx, ptryy = mo.x + c_div(xmove, 2), mo.y + c_div(ymove, 2)
            xmove >>= 1
            ymove >>= 1
        else:
            ptryx, ptryy = mo.x + xmove, mo.y + ymove
            xmove = ymove = 0
        ok, got = physics.try_move(mo, ptryx, ptryy)
        crossed.extend(got)
        if not ok:
            if is_player:
                physics.slide_move(mo, crossed)
            elif mo.flags & _MF_MISSILE and not (mo.flags & _MF_NOCLIP):
                from pydoom.combat import explode_missile
                explode_missile(mo, ctx)
            else:
                mo.momx = mo.momy = 0  # blocked
    if is_player:
        ps = getattr(ctx, "player_state", None)
        if ps is not None and (ps.cheats & CF_NOMOMENTUM):
            # NOTE: debug no-sliding option: momentum dies here.
            mo.momx = mo.momy = 0
            return crossed
    if mo.flags & (_MF_MISSILE | _MF_SKULLFLY):
        return crossed  # no friction for missiles ever
    if mo.z > mo.floorz:
        return crossed  # no friction when airborne
    cmd_fwd = cmd_side = 0
    if is_player:
        ps = getattr(ctx, "player_state", None)
        cmd = getattr(ps, "cmd", None) if ps is not None else None
        if cmd is not None:
            cmd_fwd, cmd_side = cmd.forwardmove, cmd.sidemove
    if ((mo.momx > -STOPSPEED and mo.momx < STOPSPEED
         and mo.momy > -STOPSPEED and mo.momy < STOPSPEED)
            and (not is_player or (cmd_fwd == 0 and cmd_side == 0))):
        if is_player and (STATE_INDEX["S_PLAY_RUN1"] <= mo.state
                          <= STATE_INDEX["S_PLAY_RUN1"] + 3):
            # NOTE: walking frames settle back to S_PLAY (ctx-free:
            # run frames carry no actions, so nothing else can fire).
            set_mobj_state(mo, STATE_INDEX["S_PLAY"])
        mo.momx = mo.momy = 0
    else:
        # NOTE: the MF_CORPSE halfway-off-step exception needs the
        # current subsector floor; statues never move, AI will refine.
        mo.momx = fixed_mul(mo.momx, FRICTION)
        mo.momy = fixed_mul(mo.momy, FRICTION)
    return crossed


def _z_movement(mo: Mobj, ctx=None) -> None:
    """P_ZMovement without skull slams or missiles (see docstring)."""
    mo.z += mo.momz
    if (mo.flags & _MF_FLOAT) and mo.target is not None:
        # NOTE: floaters ease toward the target's mid-height when close
        # (vanilla dist-gated approach, skipped while skull-charging or
        # mid float-adjust); NOGRAVITY keeps them aloft otherwise.
        if not (mo.flags & (_MF_SKULLFLY | _MF_INFLOAT)):
            from pydoom.physics import aprox_distance
            dist = aprox_distance(mo.x - mo.target.x, mo.y - mo.target.y)
            delta = (mo.target.z + (mo.target.height >> 1)) - mo.z
            if delta < 0 and dist < -(delta * 3):
                mo.z -= FLOATSPEED
            elif delta > 0 and dist < (delta * 3):
                mo.z += FLOATSPEED
    if mo.z <= mo.floorz:
        if mo.momz < 0:
            mo.momz = 0
        mo.z = mo.floorz
        if mo.flags & _MF_MISSILE:
            from pydoom.combat import explode_missile
            explode_missile(mo, ctx)  # approximate P_ExplodeMissile
            return
    elif not (mo.flags & _MF_NOGRAVITY):
        if mo.momz == 0:
            mo.momz = -GRAVITY * 2
        else:
            mo.momz -= GRAVITY
    if mo.z + mo.height > mo.ceilingz:
        if mo.momz > 0:
            mo.momz = 0
        mo.z = mo.ceilingz - mo.height
        if mo.flags & _MF_MISSILE:
            from pydoom.combat import explode_missile
            explode_missile(mo, ctx)  # approximate P_ExplodeMissile
