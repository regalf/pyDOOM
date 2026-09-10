"""Monster AI: sight, chase, sound (p_enemy.c movement AI + p_sight.c).

Covers P_CheckSight (+DivlineSide/CrossSubsector/CrossBSPNode),
P_RecursiveSound/P_NoiseAlert, P_CheckMeleeRange, P_CheckMissileRange,
P_Move/P_TryWalk, P_NewChaseDir, A_Look and A_Chase (movement only).

Scope (documented, never silent):

* Attacks live in combat.py, which registers them into ACTIONS when
  loaded. Without that, A_Chase faces its target and holds at attack
  range instead of entering melee/missile states. JUSTATTACKED still
  paces re-aiming.
* No floaters: MF_FLOAT height adjustment in P_Move is skipped, so
  flying monsters keep their spawn height until AI flight lands.
* Sounds play through pydoom.audio (seesound on wake, activesound
  growls); without a mixer everything stays silent.
* Single-level validcount stamp is shared with physics (one counter,
  like the C global), bumped by check_sight/noise_alert.
* Player powers, netgame retargeting, zatemissy deathmatch and
  nightmare/fast parameters are fixed off (ctx carries them for later).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydoom.angles import point_to_angle2
from pydoom.fixed import ANG90, ANG180, ANG270, fixed_div
from pydoom.info import MF_FLAGS, MT_INDEX
from pydoom.mapdata import ML_SOUNDBLOCK, ML_TWOSIDED
from pydoom.m_random import p_random
from pydoom.physics import aprox_distance, intercept_vector

__all__ = [
    "MELEERANGE",
    "DI_NODIR",
    "AIContext",
    "check_sight",
    "look_for_players",
    "check_melee_range",
    "check_missile_range",
    "move_actor",
    "try_walk",
    "new_chase_dir",
    "noise_alert",
    "a_look",
    "a_chase",
]

MELEERANGE = 64 * 65536  # p_local.h (FRACUNIT implied)

# dirtype_t (p_enemy.c): E, NE, N, NW, W, SW, S, SE, NODIR.
DI_NODIR = 8
OPPOSITE = [4, 5, 6, 7, 0, 1, 2, 3, 8]
DIAGS = [3, 1, 5, 7]
XSPEED = [65536, 47000, 0, -47000, -65536, -47000, 0, 47000]
YSPEED = [0, 47000, 65536, 47000, 0, -47000, -65536, -47000]

_U32 = 0xFFFFFFFF
_SIGNBIT = 0x80000000

_MF_AMBUSH = MF_FLAGS["MF_AMBUSH"]
_MF_JUSTHIT = MF_FLAGS["MF_JUSTHIT"]
_MF_JUSTATTACKED = MF_FLAGS["MF_JUSTATTACKED"]
_MF_SHOOTABLE = MF_FLAGS["MF_SHOOTABLE"]
_MF_SHADOW = MF_FLAGS["MF_SHADOW"]


@dataclass(eq=False)
class AIContext:
    """Everything actions need: world access plus game switches."""

    physics: object = None
    world: object = None  # doors.World: use_special_line
    players: list = field(default_factory=list)
    playingame: list = field(default_factory=lambda: [True, False, False, False])
    ai_frozen: bool = False
    fast: bool = False
    skill: str = "normal"
    sector_index: dict = field(default_factory=dict)  # id(sector) -> number
    noise_target: object = None  # P_NoiseAlert flood target
    mobjs: list = field(default_factory=list)  # spawned missiles/puffs go here
    skyflatnum: object = None  # hitscan sky hack (renderer value)
    player_state: object = None  # PlayerState for armor/invuln (combat)

    def call_action(self, mo, name: str) -> None:
        if self.ai_frozen and name in ("A_Look", "A_Chase"):
            return  # frozen statues: gravity/states continue
        fn = ACTIONS.get(name)
        if fn is not None:
            fn(mo, self)


def divline_side(x: int, y: int, dl: tuple[int, int, int, int]) -> int:
    """P_DivlineSide: 0 front, 1 back, 2 on (with the x==node->y quirk)."""
    lx, ly, ldx, ldy = dl
    if ldx == 0:
        if x == lx:
            return 2
        if x <= lx:
            return int(ldy > 0)
        return int(ldy < 0)
    if ldy == 0:
        if x == ly:  # NOTE: vanilla compares x to node->y here
            return 2
        if y <= ly:
            return int(ldx < 0)
        return int(ldx > 0)
    dx, dy = x - lx, y - ly
    left = (ldy >> 16) * (dx >> 16)
    right = (dy >> 16) * (ldx >> 16)
    if right < left:
        return 0
    if left == right:
        return 2
    return 1


class _Sight:
    """P_CheckSight traversal state (slopes narrow along the trace)."""

    def __init__(self, game_map, physics, t1, t2) -> None:
        self.map = game_map
        self.physics = physics
        self.sightzstart = t1.z + t1.height - (t1.height >> 2)
        self.topslope = (t2.z + t2.height) - self.sightzstart
        self.bottomslope = t2.z - self.sightzstart
        self.strace = (t1.x, t1.y, t2.x - t1.x, t2.y - t1.y)
        self.t2x, self.t2y = t2.x, t2.y

    def cross_bsp_node(self, bspnum: int) -> bool:
        if bspnum & 0x8000:  # NF_SUBSECTOR
            num = 0 if bspnum == -1 else bspnum & ~0x8000
            return self.cross_subsector(num)
        bsp = self.map.nodes[bspnum]
        dl = (bsp.x, bsp.y, bsp.dx, bsp.dy)
        side = divline_side(self.strace[0], self.strace[1], dl)
        if side == 2:
            side = 0
        if not self.cross_bsp_node(bsp.children[side]):
            return False
        if side == divline_side(self.t2x, self.t2y, dl):
            return True
        return self.cross_bsp_node(bsp.children[side ^ 1])

    def cross_subsector(self, num: int) -> bool:
        sub = self.map.subsectors[num]
        for i in range(sub.numlines):
            seg = self.map.segs[sub.firstline + i]
            line = seg.linedef
            assert line is not None
            if line.validcount == self.physics.validcount:
                continue  # already checked other side
            line.validcount = self.physics.validcount
            assert line.v1 is not None and line.v2 is not None
            s1 = divline_side(line.v1.x, line.v1.y, self.strace)
            s2 = divline_side(line.v2.x, line.v2.y, self.strace)
            if s1 == s2:
                continue  # line isn't crossed
            dl = (line.v1.x, line.v1.y, line.dx, line.dy)
            s1 = divline_side(self.strace[0], self.strace[1], dl)
            s2 = divline_side(self.t2x, self.t2y, dl)
            if s1 == s2:
                continue
            if not (line.flags & ML_TWOSIDED):
                return False
            front, back = seg.frontsector, seg.backsector
            assert front is not None and back is not None
            if (front.floorheight == back.floorheight
                    and front.ceilingheight == back.ceilingheight):
                continue  # no wall to block sight with
            opentop = min(front.ceilingheight, back.ceilingheight)
            openbottom = max(front.floorheight, back.floorheight)
            if openbottom >= opentop:
                return False
            frac = intercept_vector(self.strace, dl)
            if front.floorheight != back.floorheight:
                slope = fixed_div(openbottom - self.sightzstart, frac)
                if slope > self.bottomslope:
                    self.bottomslope = slope
            if front.ceilingheight != back.ceilingheight:
                slope = fixed_div(opentop - self.sightzstart, frac)
                if slope < self.topslope:
                    self.topslope = slope
            if self.topslope <= self.bottomslope:
                return False
        return True


def check_sight(t1, t2, ctx: AIContext) -> bool:
    """P_CheckSight via the REJECT table then BSP slope tracing."""
    game_map = ctx.physics.map
    sectors = game_map.sectors
    s1 = ctx.sector_index.get(id(t1.sector), -1)
    s2 = ctx.sector_index.get(id(t2.sector), -1)
    if s1 >= 0 and s2 >= 0 and game_map.reject:
        pnum = s1 * len(sectors) + s2
        if game_map.reject[pnum >> 3] & (1 << (pnum & 7)):
            return False  # trivially rejected
    ctx.physics.validcount += 1
    walker = _Sight(game_map, ctx.physics, t1, t2)
    if not game_map.nodes:
        return walker.cross_subsector(0)
    return walker.cross_bsp_node(len(game_map.nodes) - 1)


def look_for_players(actor, ctx: AIContext, allaround: bool) -> bool:
    """P_LookForPlayers with lastlook rotation over playing slots."""
    lastlook = actor.lastlook
    stop = (lastlook - 1) & 3
    c = 0
    while True:
        if ctx.playingame[lastlook]:
            if c == 2 or lastlook == stop:
                return False  # done looking
            c += 1
            player = ctx.players[lastlook]
            if player.health <= 0:
                pass  # dead: keep looking
            elif not check_sight(actor, player, ctx):
                pass  # out of sight: keep looking
            elif not allaround:
                an = (point_to_angle2(actor.x, actor.y, player.x, player.y)
                      - actor.angle) & _U32
                if an > ANG90 and an < ANG270:
                    dist = aprox_distance(player.x - actor.x,
                                          player.y - actor.y)
                    if dist > MELEERANGE:
                        pass  # behind back: keep looking
                    else:
                        actor.target = player
                        return True
                else:
                    actor.target = player
                    return True
            else:
                actor.target = player
                return True
        lastlook = (lastlook + 1) & 3
        actor.lastlook = lastlook


def check_melee_range(actor, ctx: AIContext) -> bool:
    """P_CheckMeleeRange: close, and visible."""
    if actor.target is None:
        return False
    dist = aprox_distance(actor.target.x - actor.x,
                          actor.target.y - actor.y)
    if dist >= MELEERANGE - 20 * 65536 + actor.target.radius:
        return False
    return check_sight(actor, actor.target, ctx)


def check_missile_range(actor, ctx: AIContext) -> bool:
    """P_CheckMissileRange: decides ranged-attack eligibility."""
    if not check_sight(actor, actor.target, ctx):
        return False
    if actor.flags & _MF_JUSTHIT:
        actor.flags &= ~_MF_JUSTHIT
        return True
    if actor.reactiontime:
        return False
    dist = (aprox_distance(actor.x - actor.target.x,
                           actor.y - actor.target.y)
            - 64 * 65536)
    from pydoom.info import MT_INDEX
    if not actor.meleestate:
        dist -= 128 * 65536
    dist >>= 16
    if actor.type == MT_INDEX["VILE"] and dist > 14 * 64:
        return False
    if actor.type == MT_INDEX["UNDEAD"]:
        if dist < 196:
            return False
        dist >>= 1
    if actor.type in (MT_INDEX["CYBORG"], MT_INDEX["SPIDER"],
                      MT_INDEX["SKULL"]):
        dist >>= 1
    dist = min(dist, 200)
    if actor.type == MT_INDEX["CYBORG"] and dist > 160:
        dist = 160
    from pydoom.m_random import p_random
    if p_random() < dist:
        return False
    return True


def move_actor(actor, ctx: AIContext) -> bool:
    """P_Move: step along movedir (opens doors when blocked)."""
    if actor.movedir == DI_NODIR:
        return False
    if actor.movedir >= 8:
        raise ValueError("Weird actor->movedir!")
    tryx = actor.x + actor.speed * XSPEED[actor.movedir]
    tryy = actor.y + actor.speed * YSPEED[actor.movedir]
    ok, _crossed = ctx.physics.try_move(actor, tryx, tryy)
    if not ok:
        # NOTE: floaters never adjust height here (see docstring).
        # Open any specials among ALL contacted lines (vanilla reads
        # the failed attempt's spechit, not just crossed ones).
        res = ctx.physics.check_position(actor, tryx, tryy)
        actor.movedir = DI_NODIR
        good = False
        for ld in reversed(res.spechit):
            if ctx.world.use_special_line(ld, 0, False):
                good = True
        return good
    actor.z = actor.floorz  # not FLOAT here
    return True


def try_walk(actor, ctx: AIContext) -> bool:
    """P_TryWalk: move and roll a fresh movecount on success."""
    from pydoom.m_random import p_random
    if not move_actor(actor, ctx):
        return False
    actor.movecount = p_random() & 15
    return True


def new_chase_dir(actor, ctx: AIContext) -> None:
    """P_NewChaseDir: pick a promising direction and step into it."""
    from pydoom.m_random import p_random
    if actor.target is None:
        raise ValueError("P_NewChaseDir: called with no target")
    olddir = actor.movedir
    turnaround = OPPOSITE[olddir]
    deltax = actor.target.x - actor.x
    deltay = actor.target.y - actor.y
    d = [0, 8, 8]
    d[1] = 0 if deltax > 10 * 65536 else (4 if deltax < -10 * 65536 else 8)
    d[2] = 6 if deltay < -10 * 65536 else (2 if deltay > 10 * 65536 else 8)
    if d[1] != DI_NODIR and d[2] != DI_NODIR:
        actor.movedir = DIAGS[((deltay < 0) << 1) + (deltax > 0)]
        if actor.movedir != turnaround and try_walk(actor, ctx):
            return
    if p_random() > 200 or abs(deltay) > abs(deltax):
        d[1], d[2] = d[2], d[1]
    if d[1] == turnaround:
        d[1] = DI_NODIR
    if d[2] == turnaround:
        d[2] = DI_NODIR
    if d[1] != DI_NODIR:
        actor.movedir = d[1]
        if try_walk(actor, ctx):
            return
    if d[2] != DI_NODIR:
        actor.movedir = d[2]
        if try_walk(actor, ctx):
            return
    if olddir != DI_NODIR:
        actor.movedir = olddir
        if try_walk(actor, ctx):
            return
    if p_random() & 1:
        order = range(0, 8)
    else:
        order = range(7, -1, -1)
    for tdir in order:
        if tdir != turnaround:
            actor.movedir = tdir
            if try_walk(actor, ctx):
                return
    if turnaround != DI_NODIR:
        actor.movedir = turnaround
        if try_walk(actor, ctx):
            return
    actor.movedir = DI_NODIR


def recursive_sound(sec, blocks: int, ctx: AIContext) -> None:
    """P_RecursiveSound: flood noise through open two-sided lines."""
    if (sec.validcount == ctx.physics.validcount
            and sec.soundtraversed <= blocks + 1):
        return  # already flooded
    sec.validcount = ctx.physics.validcount
    sec.soundtraversed = blocks + 1
    sec.soundtarget = ctx.noise_target
    for line in sec.lines:
        if not (line.flags & ML_TWOSIDED):
            continue
        opentop, openbottom, openrange, _low = (
            ctx.physics.line_opening(line))
        if openrange <= 0:
            continue  # closed door
        other = (line.backsector if line.frontsector is sec
                 else line.frontsector)
        if other is None:
            continue
        if line.flags & ML_SOUNDBLOCK:
            if not blocks:
                recursive_sound(other, 1, ctx)
        else:
            recursive_sound(other, blocks, ctx)


def noise_alert(target, emitter, ctx: AIContext) -> None:
    """P_NoiseAlert: wake a whole flood region toward target."""
    ctx.noise_target = target
    ctx.physics.validcount += 1
    recursive_sound(emitter.sector, 0, ctx)


def _face_target(actor, ctx=None) -> None:
    """A_FaceTarget: snap angle toward the target (sounds skipped)."""
    if actor.target is not None:
        actor.angle = point_to_angle2(actor.x, actor.y,
                                      actor.target.x, actor.target.y)


def _wake_sound(actor) -> None:
    """A_Look seeyou: posit cycle, bgsit cycle, direct otherwise."""
    from pydoom import audio
    from pydoom.info import MT_NAMES
    entry = audio.MONSTERS.get(MT_NAMES[actor.type])
    if entry is None:
        return
    see = entry[0]
    if see is None:
        return
    if see == "posit1":
        see = f"posit{p_random() % 3 + 1}"
    elif see == "bgsit1":
        see = f"bgsit{p_random() % 2 + 1}"
    audio.play(see, actor.x, actor.y, actor)


def a_look(actor, ctx: AIContext) -> None:
    """A_Look: stay idle until a player is sighted (then growl)."""
    from pydoom.mobjs import set_mobj_state
    actor.threshold = 0
    targ = actor.sector.soundtarget if actor.sector is not None else None
    if targ is not None and (targ.flags & _MF_SHOOTABLE):
        actor.target = targ
        if actor.flags & _MF_AMBUSH:
            if check_sight(actor, actor.target, ctx):
                _wake_sound(actor)
                set_mobj_state(actor, actor.seestate, ctx)
                return
        else:
            _wake_sound(actor)
            set_mobj_state(actor, actor.seestate, ctx)
            return
    if not look_for_players(actor, ctx, False):
        return
    _wake_sound(actor)
    set_mobj_state(actor, actor.seestate, ctx)


def a_chase(actor, ctx: AIContext) -> None:
    """A_Chase: close in, melee/missile states at range (see combat.py)."""
    from pydoom.mobjs import set_mobj_state
    if actor.reactiontime:
        actor.reactiontime -= 1
    if actor.threshold:
        if actor.target is None or actor.target.health <= 0:
            actor.threshold = 0
        else:
            actor.threshold -= 1
    if actor.movedir < 8:
        actor.angle &= (7 << 29)
        raw = (actor.angle - (actor.movedir << 29)) & _U32
        delta = raw if raw < 0x80000000 else raw - 0x100000000
        if delta > 0:
            actor.angle = (actor.angle - ANG90 // 2) & _U32
        elif delta < 0:
            actor.angle = (actor.angle + ANG90 // 2) & _U32
    if actor.target is None or not (actor.target.flags & _MF_SHOOTABLE):
        if look_for_players(actor, ctx, True):
            return
        set_mobj_state(actor, actor.spawnstate, ctx)
        return
    if actor.flags & _MF_JUSTATTACKED:
        actor.flags &= ~_MF_JUSTATTACKED
        if ctx.skill != "nightmare" and not ctx.fast:
            new_chase_dir(actor, ctx)
        return
    if actor.meleestate and check_melee_range(actor, ctx):
        set_mobj_state(actor, actor.meleestate, ctx)
        return
    if actor.missilestate:
        if not (ctx.skill != "nightmare" and not ctx.fast
                and actor.movecount):
            if check_missile_range(actor, ctx):
                set_mobj_state(actor, actor.missilestate, ctx)
                actor.flags |= _MF_JUSTATTACKED
                return
    # chase towards player
    actor.movecount -= 1
    if actor.movecount < 0 or not move_actor(actor, ctx):
        new_chase_dir(actor, ctx)
    from pydoom import audio
    from pydoom.info import MT_NAMES
    entry = audio.MONSTERS.get(MT_NAMES[actor.type])
    if entry is not None and entry[3] is not None \
            and p_random() < 3:
        audio.play(entry[3], actor.x, actor.y, actor)  # NOTE: idle growl


def _face_target(actor, ctx=None) -> None:
    """A_FaceTarget: snap angle toward the target (sounds skipped)."""
    if actor.target is not None:
        actor.angle = point_to_angle2(actor.x, actor.y,
                                      actor.target.x, actor.target.y)


ACTIONS = {
    "A_Look": a_look,
    "A_Chase": a_chase,
    "A_FaceTarget": _face_target,
    # NOTE: the remaining A_* (screams, pains, lights, psprites) are
    # sound/visual-only in vanilla; unknown actions are safe no-ops.
}
