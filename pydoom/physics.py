"""Movement physics: blockmap, intercepts, position checks, sliding.

Ports the movement part of p_map.c (PIT_CheckLine, P_CheckPosition,
P_TryMove, P_SlideMove, P_HitSlideLine, PTR_SlideTraverse) and p_maputl.c
(P_AproxDistance, P_PointOnLineSide, P_BoxOnLineSide,
P_PointOnDivlineSide, P_InterceptVector, P_LineOpening,
P_BlockLinesIterator, PIT_AddLineIntercepts, P_TraverseIntercepts,
P_PathTraverse).

Scope (documented, never silent):

* Thing collision needs a ThingIndex (mobjs.py): without one, bodies
  pass through everything. Special (pickup) things never block and are
  reported as touched; pickups, missile hits, skull slams and damage
  arrive with the gamesim.
* Crossed special lines are REPORTED (CheckResult.spechit) but never
  executed: P_CrossSpecialLine needs the gamesim (doors.py executes
  them for the viewer).
* Movers are plain Mover dataclasses, not mobj_t. The viewer drives a
  player-like mover (radius 16, height 56, MF_DROPOFF).
* The intercepts list grows unbounded; the C static array overflows
  (undefined behavior) past MAXINTERCEPTS instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydoom import tables
from pydoom.angles import point_on_side, point_to_angle2
from pydoom.fixed import (
    ANG90,
    ANG180,
    FRACBITS,
    FRACUNIT,
    MAXINT,
    fixed_div,
    fixed_mul,
)
from pydoom.mapdata import (
    BOX,
    MAPBLOCKSHIFT,
    MAXRADIUS,
    ML_BLOCKING,
    ML_BLOCKMONSTERS,
    ML_TWOSIDED,
    Map,
    SlopeType,
)

__all__ = [
    "PLAYER_RADIUS",
    "PLAYER_HEIGHT",
    "STEPHEIGHT",
    "MAPBTOFRAC",
    "MAPBLOCKSIZE",
    "MAXINTERCEPTS",
    "PT_ADDLINES",
    "PT_ADDTHINGS",
    "PT_EARLYOUT",
    "MF_MISSILE",
    "MF_DROPOFF",
    "MF_FLOAT",
    "MF_TELEPORT",
    "MF_NOCLIP",
    "Mover",
    "CheckResult",
    "Physics",
    "aprox_distance",
    "point_on_line_side",
    "box_on_line_side",
]

# Player body (MT_PLAYER in info.c) and step limit (p_map.c literals).
PLAYER_RADIUS = 16 * FRACUNIT
PLAYER_HEIGHT = 56 * FRACUNIT
STEPHEIGHT = 24 * FRACUNIT

# p_local.h blockmap geometry.
MAPBTOFRAC = MAPBLOCKSHIFT - FRACBITS  # 7
MAPBLOCKSIZE = 128 * FRACUNIT

MAXINTERCEPTS = 128  # informational only, see docstring

# Path traverse flags (p_local.h).
PT_ADDLINES = 1
PT_ADDTHINGS = 2
PT_EARLYOUT = 4

# Mover flags used here (p_mobj.h).
MF_MISSILE = 0x10000
MF_SKULLFLY = 0x4000000
MF_DROPOFF = 0x400
MF_FLOAT = 0x4000
MF_TELEPORT = 0x8000
MF_NOCLIP = 0x1000
MF_SOLID = 0x2
MF_SHOOTABLE = 0x4
MF_SPECIAL = 0x1

_U32 = 0xFFFFFFFF
_SIGNBIT = 0x80000000


def aprox_distance(dx: int, dy: int) -> int:
    """P_AproxDistance: fast dx+dy-max/2 estimation."""
    dx, dy = abs(dx), abs(dy)
    if dx < dy:
        return dx + dy - (dx >> 1)
    return dx + dy - (dy >> 1)


def point_on_line_side(x: int, y: int, line) -> int:
    """P_PointOnLineSide: 0 (front) or 1 (back) of a map line."""
    assert line.v1 is not None and line.v2 is not None
    if line.dx == 0:
        if x <= line.v1.x:
            return int(line.dy > 0)
        return int(line.dy < 0)
    if line.dy == 0:
        if y <= line.v1.y:
            return int(line.dx < 0)
        return int(line.dx > 0)
    dx = x - line.v1.x
    dy = y - line.v1.y
    left = fixed_mul(line.dy >> FRACBITS, dx)
    right = fixed_mul(dy, line.dx >> FRACBITS)
    if right < left:
        return 0
    return 1


def box_on_line_side(tmbox: list[int], line) -> int:
    """P_BoxOnLineSide: side of both box corners, -1 if split."""
    if line.slopetype == SlopeType.ST_HORIZONTAL:
        assert line.v1 is not None
        p1 = int(tmbox[BOX.BOXTOP] > line.v1.y)
        p2 = int(tmbox[BOX.BOXBOTTOM] > line.v1.y)
        if line.dx < 0:
            p1 ^= 1
            p2 ^= 1
    elif line.slopetype == SlopeType.ST_VERTICAL:
        assert line.v1 is not None
        p1 = int(tmbox[BOX.BOXRIGHT] < line.v1.x)
        p2 = int(tmbox[BOX.BOXLEFT] < line.v1.x)
        if line.dy < 0:
            p1 ^= 1
            p2 ^= 1
    elif line.slopetype == SlopeType.ST_POSITIVE:
        p1 = point_on_line_side(tmbox[BOX.BOXLEFT], tmbox[BOX.BOXTOP], line)
        p2 = point_on_line_side(tmbox[BOX.BOXRIGHT], tmbox[BOX.BOXBOTTOM], line)
    else:
        p1 = point_on_line_side(tmbox[BOX.BOXRIGHT], tmbox[BOX.BOXTOP], line)
        p2 = point_on_line_side(tmbox[BOX.BOXLEFT], tmbox[BOX.BOXBOTTOM], line)
    if p1 == p2:
        return p1
    return -1


def point_on_divline_side(x: int, y: int, dl: tuple[int, int, int, int]) -> int:
    """P_PointOnDivlineSide on an (x, y, dx, dy) tuple."""
    lx, ly, ldx, ldy = dl
    if ldx == 0:
        if x <= lx:
            return int(ldy > 0)
        return int(ldy < 0)
    if ldy == 0:
        if y <= ly:
            return int(ldx < 0)
        return int(ldx > 0)
    dx = x - lx
    dy = y - ly
    # Sign-bit fast path on 32-bit two's complement words.
    if ((ldy & _U32) ^ (ldx & _U32) ^ (dx & _U32) ^ (dy & _U32)) & _SIGNBIT:
        if ((ldy & _U32) ^ (dx & _U32)) & _SIGNBIT:
            return 1
        return 0
    left = fixed_mul(ldy >> 8, dx >> 8)
    right = fixed_mul(dy >> 8, ldx >> 8)
    if right < left:
        return 0
    return 1


def intercept_vector(
    v2: tuple[int, int, int, int], v1: tuple[int, int, int, int]
) -> int:
    """P_InterceptVector: fractional intercept along v2 (usually trace)."""
    den = fixed_mul(v1[3] >> 8, v2[2]) - fixed_mul(v1[2] >> 8, v2[3])
    if den == 0:
        return 0  # parallel
    num = fixed_mul((v1[0] - v2[0]) >> 8, v1[3]) + fixed_mul(
        (v2[1] - v1[1]) >> 8, v1[2]
    )
    return fixed_div(num, den)


@dataclass(eq=False)
class Mover:
    """A moving body (player-like subset of mobj_t)."""

    x: int = 0
    y: int = 0
    z: int = 0
    momx: int = 0
    momy: int = 0
    radius: int = PLAYER_RADIUS
    height: int = PLAYER_HEIGHT
    flags: int = MF_DROPOFF
    is_player: bool = True
    floorz: int = 0
    ceilingz: int = 0


@dataclass(eq=False)
class CheckResult:
    ok: bool = False
    floorz: int = 0
    ceilingz: int = 0
    dropoffz: int = 0
    spechit: list = field(default_factory=list)
    touched: list = field(default_factory=list)  # special things met
    sub: object = None  # destination subsector (for sector re-linking)


class Physics:
    """Collision queries over a Map (owns the validcount stamp).

    things is an optional ThingIndex (mobjs.py): with it, moving
    bodies collide with solid things and report touched specials.
    damage_hook(mover, thing) handles skull slams and missile hits;
    without it those just stop (combat.py sets it).
    """

    def __init__(self, game_map: Map, things=None) -> None:
        self.map = game_map
        self.things = things
        self.validcount = 0
        self.damage_hook = None
        bm = game_map.blockmap
        # Full directory words: header (map units) + offset lists.
        self._blump = [bm.orgx >> FRACBITS, bm.orgy >> FRACBITS,
                       bm.width, bm.height] + bm.lists
        self._bmap = self._blump[4:]

    # -- blockmap iteration (P_BlockLinesIterator) --

    def block_lines(self, bx: int, by: int, func) -> bool:
        bm = self.map.blockmap
        if bx < 0 or by < 0 or bx >= bm.width or by >= bm.height:
            return True
        offset = self._bmap[by * bm.width + bx]
        i = offset
        while self._blump[i] != -1:
            line = self.map.lines[self._blump[i]]
            if line.validcount != self.validcount:
                line.validcount = self.validcount
                if not func(line):
                    return False
            i += 1
        return True

    # -- openings (P_LineOpening) --

    @staticmethod
    def line_opening(line) -> tuple[int, int, int, int]:
        """(opentop, openbottom, openrange, lowfloor) through a line."""
        if line.sidenum[1] == -1 or line.backsector is None:
            return 0, 0, 0, 0  # single sided
        front, back = line.frontsector, line.backsector
        assert front is not None
        opentop = min(front.ceilingheight, back.ceilingheight)
        if front.floorheight > back.floorheight:
            openbottom, lowfloor = front.floorheight, back.floorheight
        else:
            openbottom, lowfloor = back.floorheight, front.floorheight
        return opentop, openbottom, opentop - openbottom, lowfloor

    # -- BSP point location (R_PointInSubsector, no renderer needed) --

    def subsector_at(self, x: int, y: int):
        nodes = self.map.nodes
        if not nodes:
            return self.map.subsectors[0]
        nodenum = len(nodes) - 1
        while not (nodenum & 0x8000):
            node = nodes[nodenum]
            nodenum = node.children[point_on_side(x, y, node)]
        return self.map.subsectors[nodenum & ~0x8000]

    # -- position check (P_CheckPosition + PIT_CheckLine) --

    def check_position(self, mover: Mover, x: int, y: int) -> CheckResult:
        res = CheckResult()
        r = mover.radius
        tmbbox = [y + r, y - r, x - r, x + r]  # BOX order
        sub = self.subsector_at(x, y)
        assert sub.sector is not None
        res.sub = sub
        floorz = dropoffz = sub.sector.floorheight
        ceilingz = sub.sector.ceilingheight
        self.validcount += 1
        spechit: list = []

        if mover.flags & MF_NOCLIP:
            res.ok = True
            res.floorz, res.ceilingz, res.dropoffz = floorz, ceilingz, dropoffz
            return res

        # Check things first (grouped by origin block, extended by
        # MAXRADIUS since origins can overlap into adjacent blocks).
        touched: list = []
        bm = self.map.blockmap
        if self.things is not None:
            xl = (tmbbox[BOX.BOXLEFT] - bm.orgx - MAXRADIUS) >> MAPBLOCKSHIFT
            xh = (tmbbox[BOX.BOXRIGHT] - bm.orgx + MAXRADIUS) >> MAPBLOCKSHIFT
            yl = (tmbbox[BOX.BOXBOTTOM] - bm.orgy - MAXRADIUS) >> MAPBLOCKSHIFT
            yh = (tmbbox[BOX.BOXTOP] - bm.orgy + MAXRADIUS) >> MAPBLOCKSHIFT
            for bx in range(xl, xh + 1):
                for by in range(yl, yh + 1):
                    for thing in self.things.iter_block(bx, by):
                        if not self._pit_check_thing(mover, x, y, thing,
                                                     touched):
                            return res
        res.touched = touched

        # check lines
        xl = (tmbbox[BOX.BOXLEFT] - bm.orgx) >> MAPBLOCKSHIFT
        xh = (tmbbox[BOX.BOXRIGHT] - bm.orgx) >> MAPBLOCKSHIFT
        yl = (tmbbox[BOX.BOXBOTTOM] - bm.orgy) >> MAPBLOCKSHIFT
        yh = (tmbbox[BOX.BOXTOP] - bm.orgy) >> MAPBLOCKSHIFT
        for bx in range(xl, xh + 1):
            for by in range(yl, yh + 1):
                ok, floorz, ceilingz, dropoffz = self._iter_lines(
                    bx, by, tmbbox, mover, floorz, ceilingz, dropoffz, spechit
                )
                if not ok:
                    return res
        res.ok = True
        res.floorz, res.ceilingz, res.dropoffz = floorz, ceilingz, dropoffz
        res.spechit = spechit
        return res

    def _iter_lines(self, bx, by, tmbbox, mover, floorz, ceilingz,
                    dropoffz, spechit):
        self._tmfloorz, self._tmceilingz, self._tmdropoffz = (
            floorz, ceilingz, dropoffz)

        def visit(line) -> bool:
            return self._pit(line, tmbbox, mover, spechit)

        ok = self.block_lines(bx, by, visit)
        return ok, self._tmfloorz, self._tmceilingz, self._tmdropoffz

    def _pit_check_thing(self, mover, x: int, y: int, thing,
                         touched: list) -> bool:
        # PIT_CheckThing without pickups or damage (see mobjs.py):
        # solid things block, special things are walkable and reported
        # as touched for the gamesim.
        if not (thing.flags & (MF_SOLID | MF_SPECIAL | MF_SHOOTABLE)):
            return True
        if thing is mover:
            return True  # don't clip against self
        blockdist = thing.radius + mover.radius
        if abs(thing.x - x) >= blockdist or abs(thing.y - y) >= blockdist:
            return True  # didn't hit it
        if mover.flags & MF_SKULLFLY:
            # Lost-soul slam: damage via hook, else just stop.
            if self.damage_hook is not None:
                return self.damage_hook(mover, thing)
            mover.momx = mover.momy = mover.momz = 0
            return False
        if mover.flags & MF_MISSILE:
            mh = getattr(mover, "height", 0)
            if mover.z > thing.z + thing.height:
                return True  # overhead
            if mover.z + mh < thing.z:
                return True  # underneath
            # Same-species pass-through needs no hook (pure type check).
            from pydoom.info import MT_INDEX
            target = getattr(mover, "target", None)
            if target is not None and (
                target.type == thing.type
                or (target.type == MT_INDEX["KNIGHT"]
                    and thing.type == MT_INDEX["BRUISER"])
                or (target.type == MT_INDEX["BRUISER"]
                    and thing.type == MT_INDEX["KNIGHT"])
            ):
                if thing is target:
                    return True  # don't hit the shooter
                if thing.type != MT_INDEX["PLAYER"]:
                    from pydoom.combat import explode_missile
                    explode_missile(mover, None)
                    return False  # explode, but do no damage
            if self.damage_hook is not None:
                return self.damage_hook(mover, thing)
            return not (thing.flags & MF_SOLID)
        if thing.flags & MF_SPECIAL:
            touched.append(thing)
        return not (thing.flags & MF_SOLID)

    def _pit(self, line, tmbbox, mover, spechit) -> bool:
        if (tmbbox[BOX.BOXRIGHT] <= line.bbox[BOX.BOXLEFT]
                or tmbbox[BOX.BOXLEFT] >= line.bbox[BOX.BOXRIGHT]
                or tmbbox[BOX.BOXTOP] <= line.bbox[BOX.BOXBOTTOM]
                or tmbbox[BOX.BOXBOTTOM] >= line.bbox[BOX.BOXTOP]):
            return True
        if box_on_line_side(tmbbox, line) != -1:
            return True
        if line.backsector is None:
            return False  # one sided line
        if not (mover.flags & MF_MISSILE):
            if line.flags & ML_BLOCKING:
                return False
            if not mover.is_player and line.flags & ML_BLOCKMONSTERS:
                return False
        opentop, openbottom, _openrange, lowfloor = self.line_opening(line)
        if opentop < self._tmceilingz:
            self._tmceilingz = opentop
        if openbottom > self._tmfloorz:
            self._tmfloorz = openbottom
        if lowfloor < self._tmdropoffz:
            self._tmdropoffz = lowfloor
        if line.special:
            spechit.append(line)
        return True

    # -- try move (P_TryMove) --

    def try_move(self, mover: Mover, x: int, y: int) -> tuple[bool, list]:
        """Attempt the move; returns (moved, crossed special lines).

        crossed holds (line, side) tuples, side being the side the
        mover started on (P_CrossSpecialLine refuses backside hops);
        it is reported on success AND failure (P_Move uses the
        failed-move list to open doors); callers execute only the
        successful crossings, except monster movement.
        """
        oldx, oldy = mover.x, mover.y
        res = self.check_position(mover, x, y)
        if not res.ok:
            return False, res.spechit
        if not (mover.flags & MF_NOCLIP):
            if res.ceilingz - res.floorz < mover.height:
                return False, []
            if (not (mover.flags & MF_TELEPORT)
                    and res.ceilingz - mover.z < mover.height):
                return False, []
            if (not (mover.flags & MF_TELEPORT)
                    and res.floorz - mover.z > STEPHEIGHT):
                return False, []
            if (not (mover.flags & (MF_DROPOFF | MF_FLOAT))
                    and res.floorz - res.dropoffz > STEPHEIGHT):
                return False, []
        mover.floorz, mover.ceilingz = res.floorz, res.ceilingz
        mover.x, mover.y = x, y
        if self.things is not None:
            # NOTE: P_SetThingPosition on every move: walking bodies
            # change blocks without momentum, so blocklinks go stale
            # (ghosts) unless re-filed here. Never-linked camera bodies
            # are left out by relink().
            self.things.relink(mover)
        sec = getattr(mover, "sector", None)
        if sec is not None and res.sub is not None:
            # NOTE: sector refs go stale the same way, and stale sectors
            # poison REJECT/soundtarget (E1M1 blue room: the roamed player
            # kept its spawn sector, which REJECT-blocks the zombies).
            # Camera Mover bodies carry no sector and skip this.
            newsec = res.sub.sector
            if newsec is not None and newsec is not sec:
                try:
                    sec.thinglist.remove(mover)
                except ValueError:
                    pass
                mover.sector = newsec
                newsec.thinglist.append(mover)
        # NOTE: z is never touched here (vanilla leaves height to
        # ZMovement/gravity); callers glue grounded bodies to the floor.
        crossed = []
        if not (mover.flags & (MF_TELEPORT | MF_NOCLIP)):
            for ld in res.spechit:
                old_side = point_on_line_side(oldx, oldy, ld)
                if point_on_line_side(x, y, ld) != old_side:
                    if ld.special:
                        crossed.append((ld, old_side))
        return True, crossed

    # -- slide (P_SlideMove + P_HitSlideLine + PTR_SlideTraverse) --

    def slide_move(self, mover: Mover, crossed: list | None = None) -> None:
        if crossed is None:
            crossed = []
        self._slidemo = mover
        hitcount = 0
        while True:
            hitcount += 1
            if hitcount == 3:
                # stairstep: try y then x separately.
                ok, got = self.try_move(mover, mover.x, mover.y + mover.momy)
                if ok:
                    crossed.extend(got)
                else:
                    ok2, got2 = self.try_move(
                        mover, mover.x + mover.momx, mover.y)
                    if ok2:
                        crossed.extend(got2)
                return
            if mover.momx > 0:
                leadx, trailx = mover.x + mover.radius, mover.x - mover.radius
            else:
                leadx, trailx = mover.x - mover.radius, mover.x + mover.radius
            if mover.momy > 0:
                leady, traily = mover.y + mover.radius, mover.y - mover.radius
            else:
                leady, traily = mover.y - mover.radius, mover.y + mover.radius
            self._bestfrac = FRACUNIT + 1
            self._bestline = None
            mx, my = mover.momx, mover.momy
            self.path_traverse(leadx, leady, leadx + mx, leady + my,
                               PT_ADDLINES, self._slide_traverse)
            self.path_traverse(trailx, leady, trailx + mx, leady + my,
                               PT_ADDLINES, self._slide_traverse)
            self.path_traverse(leadx, traily, leadx + mx, traily + my,
                               PT_ADDLINES, self._slide_traverse)
            if self._bestfrac == FRACUNIT + 1:
                ok, got = self.try_move(mover, mover.x, mover.y + mover.momy)
                if ok:
                    crossed.extend(got)
                else:
                    ok2, got2 = self.try_move(
                        mover, mover.x + mover.momx, mover.y)
                    if ok2:
                        crossed.extend(got2)
                return
            bestfrac = self._bestfrac - 0x800  # fudge from the wall
            if bestfrac > 0:
                ok, got = self.try_move(
                    mover,
                    mover.x + fixed_mul(mx, bestfrac),
                    mover.y + fixed_mul(my, bestfrac),
                )
                if ok:
                    crossed.extend(got)
                else:
                    # stairstep (same two attempts as hitcount == 3).
                    ok2, got2 = self.try_move(
                        mover, mover.x, mover.y + mover.momy)
                    if ok2:
                        crossed.extend(got2)
                    else:
                        ok3, got3 = self.try_move(
                            mover, mover.x + mover.momx, mover.y)
                        if ok3:
                            crossed.extend(got3)
                    return
            bestfrac = FRACUNIT - (bestfrac + 0x800)
            if bestfrac > FRACUNIT:
                bestfrac = FRACUNIT
            if bestfrac <= 0:
                return
            self._tmxmove = fixed_mul(mx, bestfrac)
            self._tmymove = fixed_mul(my, bestfrac)
            assert self._bestline is not None
            self._hit_slide_line(self._bestline)
            mover.momx, mover.momy = self._tmxmove, self._tmymove
            ok, got = self.try_move(
                mover, mover.x + mover.momx, mover.y + mover.momy
            )
            if ok:
                crossed.extend(got)
            else:
                continue  # retry:
            return

    def _slide_traverse(self, intercept) -> bool:
        line = intercept[1]
        assert line is not None
        if not (line.flags & ML_TWOSIDED):
            if point_on_line_side(
                self._slidemo.x, self._slidemo.y, line
            ):
                return True  # don't hit the back side
        else:
            opentop, openbottom, openrange, _low = self.line_opening(line)
            if openrange < self._slidemo.height:
                pass  # doesn't fit: blocking (see below)
            elif opentop - self._slidemo.z < self._slidemo.height:
                pass  # mobj is too high: blocking (see below)
            elif openbottom - self._slidemo.z > STEPHEIGHT:
                pass  # too big a step up: blocking (see below)
            else:
                return True  # this line doesn't block movement
        if intercept[0] < self._bestfrac:
            self._bestfrac = intercept[0]
            self._bestline = line
        return False  # stop

    def _hit_slide_line(self, line) -> None:
        if line.slopetype == SlopeType.ST_HORIZONTAL:
            self._tmymove = 0
            return
        if line.slopetype == SlopeType.ST_VERTICAL:
            self._tmxmove = 0
            return
        side = point_on_line_side(self._slidemo.x, self._slidemo.y, line)
        lineangle = point_to_angle2(0, 0, line.dx, line.dy)
        if side == 1:
            lineangle = (lineangle + ANG180) & _U32
        moveangle = point_to_angle2(0, 0, self._tmxmove, self._tmymove)
        deltaangle = (moveangle - lineangle) & _U32
        if deltaangle > ANG180:
            deltaangle = (deltaangle + ANG180) & _U32
        lineangle >>= 19  # ANGLETOFINESHIFT
        deltaangle >>= 19
        movelen = aprox_distance(self._tmxmove, self._tmymove)
        newlen = fixed_mul(movelen, tables.finecosine(deltaangle))
        self._tmxmove = fixed_mul(newlen, tables.finecosine(lineangle))
        self._tmymove = fixed_mul(newlen, tables.finesine[lineangle])

    # -- path traversal (P_PathTraverse and friends) --

    def path_traverse(self, x1, y1, x2, y2, flags, trav) -> bool:
        self._earlyout = bool(flags & PT_EARLYOUT)
        self.validcount += 1
        self._intercepts = []
        if ((x1 - self.map.blockmap.orgx) & (MAPBLOCKSIZE - 1)) == 0:
            x1 += FRACUNIT  # don't side exactly on a line
        if ((y1 - self.map.blockmap.orgy) & (MAPBLOCKSIZE - 1)) == 0:
            y1 += FRACUNIT
        trace = [x1, y1, x2 - x1, y2 - y1]
        self._trace = trace
        orgx, orgy = self.map.blockmap.orgx, self.map.blockmap.orgy
        ax1, ay1 = x1 - orgx, y1 - orgy
        ax2, ay2 = x2 - orgx, y2 - orgy
        xt1, yt1 = ax1 >> MAPBLOCKSHIFT, ay1 >> MAPBLOCKSHIFT
        xt2, yt2 = ax2 >> MAPBLOCKSHIFT, ay2 >> MAPBLOCKSHIFT
        if xt2 > xt1:
            mapxstep = 1
            partial = FRACUNIT - ((ax1 >> MAPBTOFRAC) & (FRACUNIT - 1))
            ystep = fixed_div(ay2 - ay1, abs(ax2 - ax1))
        elif xt2 < xt1:
            mapxstep = -1
            partial = (ax1 >> MAPBTOFRAC) & (FRACUNIT - 1)
            ystep = fixed_div(ay2 - ay1, abs(ax2 - ax1))
        else:
            mapxstep = 0
            partial = FRACUNIT
            ystep = 256 * FRACUNIT
        yintercept = (ay1 >> MAPBTOFRAC) + fixed_mul(partial, ystep)
        if yt2 > yt1:
            mapystep = 1
            partial = FRACUNIT - ((ay1 >> MAPBTOFRAC) & (FRACUNIT - 1))
            xstep = fixed_div(ax2 - ax1, abs(ay2 - ay1))
        elif yt2 < yt1:
            mapystep = -1
            partial = (ay1 >> MAPBTOFRAC) & (FRACUNIT - 1)
            xstep = fixed_div(ax2 - ax1, abs(ay2 - ay1))
        else:
            mapystep = 0
            partial = FRACUNIT
            xstep = 256 * FRACUNIT
        xintercept = (ax1 >> MAPBTOFRAC) + fixed_mul(partial, xstep)

        mapx, mapy = xt1, yt1
        for _ in range(64):
            if flags & PT_ADDLINES:
                if not self._block_lines_traverse(mapx, mapy):
                    return False
            if flags & PT_ADDTHINGS:
                if not self._block_things_traverse(mapx, mapy):
                    return False
            if mapx == xt2 and mapy == yt2:
                break
            if (yintercept >> FRACBITS) == mapy:
                yintercept += ystep
                mapx += mapxstep
            elif (xintercept >> FRACBITS) == mapx:
                xintercept += xstep
                mapy += mapystep
        return self._traverse_intercepts(trav, FRACUNIT)

    def _block_lines_traverse(self, bx: int, by: int) -> bool:
        bm = self.map.blockmap
        if bx < 0 or by < 0 or bx >= bm.width or by >= bm.height:
            return True
        offset = self._bmap[by * bm.width + bx]
        i = offset
        while self._blump[i] != -1:
            line = self.map.lines[self._blump[i]]
            if line.validcount != self.validcount:
                line.validcount = self.validcount
                if not self._add_line_intercept(line):
                    return False
            i += 1
        return True

    def _add_line_intercept(self, line) -> bool:
        trace = self._trace
        assert line.v1 is not None and line.v2 is not None
        if (trace[2] > 16 * FRACUNIT or trace[2] < -16 * FRACUNIT
                or trace[3] > 16 * FRACUNIT or trace[3] < -16 * FRACUNIT):
            s1 = point_on_divline_side(line.v1.x, line.v1.y, tuple(trace))
            s2 = point_on_divline_side(line.v2.x, line.v2.y, tuple(trace))
        else:
            s1 = point_on_line_side(trace[0], trace[1], line)
            s2 = point_on_line_side(trace[0] + trace[2],
                                    trace[1] + trace[3], line)
        if s1 == s2:
            return True  # line isn't crossed
        dl = (line.v1.x, line.v1.y, line.dx, line.dy)
        frac = intercept_vector(tuple(trace), dl)
        if frac < 0:
            return True  # behind source
        if self._earlyout and frac < FRACUNIT and line.backsector is None:
            return False  # stop checking
        self._intercepts.append((frac, line, None))
        return True

    def _block_things_traverse(self, bx: int, by: int) -> bool:
        bm = self.map.blockmap
        if bx < 0 or by < 0 or bx >= bm.width or by >= bm.height:
            return True
        if self.things is None:
            return True
        for thing in self.things.iter_block(bx, by):
            if not self._add_thing_intercept(thing):
                return False
        return True

    def _add_thing_intercept(self, thing) -> bool:
        # PIT_AddThingIntercepts: corner-to-corner crossection test.
        trace = self._trace
        r = thing.radius
        # tracepositive = (trace.dx ^ trace.dy) > 0, signed 32-bit.
        xor = (trace[2] & _U32) ^ (trace[3] & _U32)
        if xor != 0 and (xor & _SIGNBIT) == 0:
            x1, y1 = thing.x - r, thing.y + r
            x2, y2 = thing.x + r, thing.y - r
        else:
            x1, y1 = thing.x - r, thing.y - r
            x2, y2 = thing.x + r, thing.y + r
        s1 = point_on_divline_side(x1, y1, tuple(trace))
        s2 = point_on_divline_side(x2, y2, tuple(trace))
        if s1 == s2:
            return True  # line isn't crossed
        dl = (x1, y1, x2 - x1, y2 - y1)
        frac = intercept_vector(tuple(trace), dl)
        if frac < 0:
            return True  # behind source
        self._intercepts.append((frac, None, thing))
        return True  # keep going

    def _traverse_intercepts(self, trav, maxfrac: int) -> bool:
        # Like the C count-- loop: at most one visit per intercept,
        # visited entries parked at MAXINT.
        remaining = list(self._intercepts)
        for _ in range(len(remaining)):
            best = min(range(len(remaining)), key=lambda i: remaining[i][0])
            frac, _line, _thing = remaining[best]
            if frac > maxfrac:
                return True  # checked everything in range
            if not trav(remaining[best]):
                return False  # don't bother going farther
            remaining[best] = (MAXINT, None, None)
        return True  # everything was traversed
