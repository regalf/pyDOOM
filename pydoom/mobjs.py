"""Map objects: spawn, thing links, thinker-lite (p_mobj.c movement part).

Covers P_SpawnMobj/P_SpawnMapThing (skill filter, ambush, ceiling
placement), P_SetMobjState (no action functions yet), P_MobjThinker
(momentum/friction/gravity/state countdown only), P_XYMovement and
P_ZMovement (no missiles, skulls, floaters or crush damage), plus
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

from pydoom.fixed import FRACUNIT, fixed_mul
from pydoom.info import MF_FLAGS, STATES, type_record
from pydoom.mapdata import MAPBLOCKSHIFT, Map
from pydoom.m_random import p_random

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
    "refresh_sector",
]

# Skill option bits (P_SpawnMapThing): baby->1, nightmare->4, else medium.
SKILL_BITS = {"baby": 1, "easy": 1, "normal": 2, "hard": 4, "nightmare": 4}

STOPSPEED = 0x1000  # p_mobj.c
FRICTION = 0xE800
GRAVITY = FRACUNIT  # p_local.h
MAXMOVE = 30 * FRACUNIT  # p_local.h
FLOATSPEED = 4 * FRACUNIT  # p_local.h (floaters arrive with AI)

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
_MF_CORPSE = MF_FLAGS["MF_CORPSE"]
_MF_DROPPED = MF_FLAGS["MF_DROPPED"]


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


def spawn_map(game_map: Map, physics, index: ThingIndex,
              skill: str = "normal") -> list[Mobj]:
    """P_SpawnMapThing over all map things (players skipped, see docstring)."""
    bit = SKILL_BITS[skill]
    mobjs: list[Mobj] = []
    for thing in game_map.things:
        if thing.type == 11:
            continue  # deathmatch starts recorded elsewhere in vanilla
        if thing.type <= 4:
            continue  # players spawn separately (no player mobj here)
        if thing.options & 16:
            continue  # multiplayer-only
        if not (thing.options & bit):
            continue  # wrong skill level
        rec = type_record(thing.type)
        if rec is None:
            raise ValueError(
                f"P_SpawnMapThing: unknown type {thing.type} "
                f"at ({thing.x}, {thing.y})")
        if rec["flags"] & _MF_NOTDMATCH:
            continue  # deathmatch-only in a single-player view
        mo = spawn_mobj(game_map, physics, index,
                        thing.x << 16, thing.y << 16,
                        -2 if rec["flags"] & _MF_SPAWNCEILING else -1,
                        rec["mt"])
        mo.doomednum = thing.type
        mo.angle = 0x20000000 * (thing.angle // 45)  # ANG45 * steps
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


def think_mobj(mo: Mobj, physics, ctx=None) -> list:
    """P_MobjThinker lite: momentum, gravity, state countdown.

    Returns crossed special lines (for the caller to execute; statues
    never move so this stays empty until AI lands).
    """
    crossed: list = []
    if mo.momx or mo.momy or (mo.flags & _MF_SKULLFLY):
        ox, oy = mo.x, mo.y
        crossed.extend(_xy_movement(mo, physics, ctx))
        if mo.dead:
            return crossed
        if (mo.x, mo.y) != (ox, oy):
            # Re-link like P_SetThingPosition after a move.
            refresh_sector(mo, physics)
    if mo.z != mo.floorz or mo.momz:
        _z_movement(mo, ctx)
        if mo.dead:
            return crossed
    if mo.tics != -1:
        mo.tics -= 1
        if not mo.tics:
            _sprite, _frame, _tics, nextstate, _action = STATES[mo.state]
            set_mobj_state(mo, nextstate, ctx)
    return crossed


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


def _xy_movement(mo: Mobj, physics, ctx=None) -> list:
    """P_XYMovement without players/skulls (see docstring)."""
    crossed: list = []
    if not mo.momx and not mo.momy:
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
    while xmove or ymove:
        if xmove > MAXMOVE // 2 or ymove > MAXMOVE // 2:
            ptryx, ptryy = mo.x + xmove // 2, mo.y + ymove // 2
            xmove >>= 1
            ymove >>= 1
        else:
            ptryx, ptryy = mo.x + xmove, mo.y + ymove
            xmove = ymove = 0
        ok, got = physics.try_move(mo, ptryx, ptryy)
        crossed.extend(got)
        if not ok:
            if mo.flags & _MF_MISSILE and not (mo.flags & _MF_NOCLIP):
                from pydoom.combat import explode_missile
                explode_missile(mo, ctx)
            else:
                mo.momx = mo.momy = 0  # blocked (players would slide)
    if mo.flags & (_MF_MISSILE | _MF_SKULLFLY):
        return crossed  # no friction for missiles ever
    if mo.z > mo.floorz:
        return crossed  # no friction when airborne
    if ((mo.momx > -STOPSPEED and mo.momx < STOPSPEED
         and mo.momy > -STOPSPEED and mo.momy < STOPSPEED)):
        mo.momx = mo.momy = 0
    else:
        # NOTE: the MF_CORPSE halfway-off-step exception needs the
        # current subsector floor; statues never move, AI will refine.
        mo.momx = fixed_mul(mo.momx, FRICTION)
        mo.momy = fixed_mul(mo.momy, FRICTION)
    return crossed


def _z_movement(mo: Mobj, ctx=None) -> None:
    """P_ZMovement without floaters, skulls or missiles (see docstring)."""
    mo.z += mo.momz
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
