"""Doors, floors, plats, switches and line triggers (p_doors.c +
p_switch.c use part + p_floor.c/p_plats.c movers + P_CrossSpecialLine).

Covers T_VerticalDoor, EV_DoDoor, EV_VerticalDoor (manual + locked
with keys), EV_DoFloor (common types), EV_DoPlat/T_PlatRaise (lifts),
EV_LightTurnOn (tag lights), EV_BuildStairs, EV_Teleport/P_TeleportMove,
P_PlayerInSpecialSector (slime/secrets/E1M8 burn-out), P_UseLines,
P_CrossSpecialLine, P_ChangeSwitchTexture, P_StartButton, A_BossDeath
floor drops (via combat) and the button revert tick.
Sector motion uses T_MovePlane (p_floor.c) with a player blocker
instead of mobjs.

Scope (documented, never silent):

* No audio: S_StartSound calls are no-ops.
* Keys arrive as a bitmask (see pydoom.player): locked manual and S1
  switch doors open when the color is held, else the vanilla PD_*
  denial. Monsters stay keyless and never open locked doors.
* Crush damage reaches mobjs through world.crush_hook (the sim hurts
  everything the ceiling sits on, PIT_ChangeSector-style); move_plane
  itself only knows an optional player blocker tuple. Monsters never
  use doors.
* Only door/floor/plat/light/stairs/teleport/exit/ceiling specials
  are executed (manual DR family, S1/SR door/floor/plat/light switches
  and buttons, W1/WR walk-over triggers for the same families, S1/SR
  exit and teleport lines, W1/WR/S1 ceiling crushers). Unimplemented
  specials report messages instead; the remaining specials and
  monster triggers arrive with the gamesim.
* Switch textures use the episode-1 pairs; episode 2/3 pairs are
  included when their textures exist in the WAD.
* Thinkers are a plain list owned by World (p_tick.c arrives later).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydoom import tables
from pydoom.fixed import FRACUNIT, MAXINT
from pydoom.info import MF_FLAGS
from pydoom.mapdata import ML_SECRET, ML_TWOSIDED, Map
from pydoom.physics import Physics, point_on_line_side

__all__ = [
    "VDOORSPEED",
    "VDOORWAIT",
    "BUTTONTIME",
    "MAXBUTTONS",
    "USERANGE",
    "FLOORSPEED",
    "CEILSPEED",
    "PLATSPEED",
    "PLATWAIT",
    "MAXPLATS",
    "DoorType",
    "PlaneResult",
    "VerticalDoor",
    "FloorMover",
    "Ceiling",
    "Plat",
    "Button",
    "World",
    "move_plane",
]

VDOORSPEED = FRACUNIT * 2  # p_spec.h
VDOORWAIT = 150
BUTTONTIME = 35  # 1 second of button texture
MAXBUTTONS = 16
USERANGE = 64 * FRACUNIT  # p_local.h
FLOORSPEED = FRACUNIT  # p_spec.h
CEILSPEED = FRACUNIT  # p_spec.h: crushers move 1 unit/tic (fast x2)
PLATSPEED = FRACUNIT
PLATWAIT = 3  # seconds (*35 tics) a lift waits
MAXPLATS = 30


class DoorType:
    """vldoor_e in declaration order (p_spec.h)."""

    NORMAL = 0
    CLOSE30THENOPEN = 1
    CLOSE = 2
    OPEN = 3
    RAISEIN5MINS = 4
    BLAZERAISE = 5
    BLAZEOPEN = 6
    BLAZECLOSE = 7


class PlaneResult:
    """result_e: OK, CRUSHED, PASTDEST."""

    OK = 0
    CRUSHED = 1
    PASTDEST = 2


# d_englsh.h lock messages.
PD_BLUEO = "You need a blue key to activate this object"
PD_REDO = "You need a red key to activate this object"
PD_YELLOWO = "You need a yellow key to activate this object"
PD_BLUEK = "You need a blue key to open this door"
PD_REDK = "You need a red key to open this door"
PD_YELLOWK = "You need a yellow key to open this door"

# Switch texture pairs (name1, name2) from p_switch.c alphSwitchList.
# Episode gating happens at init by texture availability.
SWITCH_PAIRS = [
    ("SW1BRCOM", "SW2BRCOM"), ("SW1BRN1", "SW2BRN1"),
    ("SW1BRN2", "SW2BRN2"), ("SW1BRNGN", "SW2BRNGN"),
    ("SW1BROWN", "SW2BROWN"), ("SW1COMM", "SW2COMM"),
    ("SW1COMP", "SW2COMP"), ("SW1DIRT", "SW2DIRT"),
    ("SW1EXIT", "SW2EXIT"), ("SW1GRAY", "SW2GRAY"),
    ("SW1GRAY1", "SW2GRAY1"), ("SW1METAL", "SW2METAL"),
    ("SW1PIPE", "SW2PIPE"), ("SW1SLAD", "SW2SLAD"),
    ("SW1STARG", "SW2STARG"), ("SW1STON1", "SW2STON1"),
    ("SW1STON2", "SW2STON2"), ("SW1STONE", "SW2STONE"),
    ("SW1STRTN", "SW2STRTN"),
    ("SW1BLUE", "SW2BLUE"), ("SW1CMT", "SW2CMT"),
    ("SW1GARG", "SW2GARG"), ("SW1GSTON", "SW2GSTON"),
    ("SW1HOT", "SW2HOT"), ("SW1LION", "SW2LION"),
    ("SW1SATYR", "SW2SATYR"), ("SW1SKIN", "SW2SKIN"),
    ("SW1VINE", "SW2VINE"), ("SW1WOOD", "SW2WOOD"),
    ("SW1PANEL", "SW2PANEL"), ("SW1ROCK", "SW2ROCK"),
    ("SW1MET2", "SW2MET2"), ("SW1WDMET", "SW2WDMET"),
    ("SW1BRIK", "SW2BRIK"), ("SW1MOD1", "SW2MOD1"),
    ("SW1ZIM", "SW2ZIM"), ("SW1STON6", "SW2STON6"),
    ("SW1TEK", "SW2TEK"), ("SW1MARB", "SW2MARB"),
    ("SW1SKULL", "SW2SKULL"),
]

# Manual (DR) door specials usable with the use key, by lock color.
# None means unlocked.
_MANUAL_LOCKS = {26: PD_BLUEK, 32: PD_BLUEK,
                 28: PD_REDK, 33: PD_REDK,
                 27: PD_YELLOWK, 34: PD_YELLOWK}
_MANUAL_LOCK_COLOR = {26: "blue", 32: "blue",
                      28: "red", 33: "red",
                      27: "yellow", 34: "yellow"}
_MANUAL_DOORS = frozenset({1, 26, 27, 28, 31, 32, 33, 34, 117, 118})

# Switch/button specials mapped to (door type, use_again). Only door
# actions are supported; anything else reports "not yet".
_SWITCH_DOORS = {
    29: (DoorType.NORMAL, False), 50: (DoorType.CLOSE, False),
    61: (DoorType.OPEN, True), 63: (DoorType.NORMAL, True),
    99: (DoorType.OPEN, False), 133: (DoorType.BLAZEOPEN, False),
    103: (DoorType.OPEN, False), 111: (DoorType.BLAZERAISE, False),
    112: (DoorType.BLAZEOPEN, False), 113: (DoorType.BLAZECLOSE, False),
    114: (DoorType.BLAZERAISE, True), 115: (DoorType.BLAZEOPEN, True),
    116: (DoorType.BLAZECLOSE, True),
    134: (DoorType.OPEN, False), 135: (DoorType.BLAZEOPEN, False),
    136: (DoorType.OPEN, False), 137: (DoorType.BLAZEOPEN, False),
}
# S1 floor switches and SR floor buttons: (floor type, use_again).
_SWITCH_FLOORS = {
    18: ("raiseFloorToNearest", False), 101: ("raiseFloor", False),
    102: ("lowerFloor", False), 140: ("raiseFloor512", False),
    23: ("lowerFloorToLowest", False), 71: ("turboLower", False),
    131: ("raiseFloorTurbo", False), 55: ("raiseFloorCrush", False),
    45: ("lowerFloor", True), 60: ("lowerFloorToLowest", True),
    64: ("raiseFloor", True), 65: ("raiseFloorCrush", True),
    69: ("raiseFloorToNearest", True), 70: ("turboLower", True),
    132: ("raiseFloorTurbo", True),
}
# S1 plat switches and SR plat buttons: (plat type, amount, use_again).
_SWITCH_PLATS = {
    14: ("raiseAndChange", 32, False), 15: ("raiseAndChange", 24, False),
    20: ("raiseToNearestAndChange", 0, False),
    21: ("downWaitUpStay", 0, False), 22: ("raiseToNearestAndChange", 0,
                                            False),
    122: ("blazeDWUS", 0, False),
    62: ("downWaitUpStay", 0, True), 66: ("raiseAndChange", 24, True),
    67: ("raiseAndChange", 32, True),
    68: ("raiseToNearestAndChange", 0, True),
    123: ("blazeDWUS", 0, True),
}
# Light switches/buttons: fixed brightness (buttons revert texture).
_SWITCH_LIGHTS = {138: 255, 139: 35}

# Walk-over triggers: W1 once-maps clear the special (except exits),
# WR retrigger-maps never clear. Values are (kind, arg) run by _fire_walk.
_WALK_ONCE = {
    2: ("door", DoorType.OPEN), 3: ("door", DoorType.CLOSE),
    4: ("door", DoorType.NORMAL),
    8: ("stairs", None),
    16: ("door", DoorType.CLOSE30THENOPEN),
    108: ("door", DoorType.BLAZERAISE), 109: ("door", DoorType.BLAZEOPEN),
    110: ("door", DoorType.BLAZECLOSE),
    10: ("plat", ("downWaitUpStay", 0)),
    53: ("plat", ("perpetualRaise", 0)),
    121: ("plat", ("blazeDWUS", 0)),
    5: ("floor", "raiseFloor"), 19: ("floor", "lowerFloor"),
    38: ("floor", "lowerFloorToLowest"), 36: ("floor", "turboLower"),
    58: ("floor", "raiseFloor24"), 119: ("floor", "raiseFloorToNearest"),
    130: ("floor", "raiseFloorTurbo"),
    6: ("ceiling", "fastCrushAndRaise"),
    25: ("ceiling", "crushAndRaise"), 40: ("ceiling", "raiseToHighest"),
    44: ("ceiling", "lowerAndCrush"),
    12: ("light", 0), 13: ("light", 255), 35: ("light", 35),
    52: ("exit", None), 39: ("teleport", None),
}
_WALK_RETRIGGER = {
    75: ("door", DoorType.CLOSE),
    76: ("door", DoorType.CLOSE30THENOPEN),
    86: ("door", DoorType.OPEN), 90: ("door", DoorType.NORMAL),
    105: ("door", DoorType.BLAZERAISE), 106: ("door", DoorType.BLAZEOPEN),
    107: ("door", DoorType.BLAZECLOSE),
    88: ("plat", ("downWaitUpStay", 0)),
    120: ("plat", ("blazeDWUS", 0)),
    87: ("plat", ("perpetualRaise", 0)),
    82: ("floor", "lowerFloorToLowest"), 83: ("floor", "lowerFloor"),
    91: ("floor", "raiseFloor"), 92: ("floor", "raiseFloor24"),
    98: ("floor", "turboLower"), 128: ("floor", "raiseFloorToNearest"),
    129: ("floor", "raiseFloorTurbo"),
    79: ("light", 35), 80: ("light", 0), 81: ("light", 255),
    72: ("ceiling", "lowerAndCrush"), 73: ("ceiling", "crushAndRaise"),
    77: ("ceiling", "fastCrushAndRaise"),
    97: ("teleport", None),
}
_SWITCH_LOCKS = {133: PD_BLUEO, 135: PD_REDO, 137: PD_YELLOWO,
                 99: PD_BLUEO, 134: PD_REDO, 136: PD_YELLOWO}
_SWITCH_LOCK_COLOR = {133: "blue", 135: "red", 137: "yellow",
                      99: "blue", 134: "red", 136: "yellow"}

# Button placement slots (bwhere_e): top, middle, bottom.
TOP, MIDDLE, BOTTOM = 0, 1, 2


def move_plane(sector, speed: int, dest: int, crush: bool,
               floor_or_ceiling: int, direction: int,
               blocker=None) -> int:
    """T_MovePlane (p_floor.c) with an optional player blocker.

    blocker is (sector, z, height): when the moving plane would leave
    the blocker unfit inside *its own* sector, the move reverts and
    reports CRUSHED, like P_ChangeSector does for mobjs standing there.
    crush=True (crushers) is accepted but behaves the same: without
    mobjs there is nothing to damage.
    """
    if floor_or_ceiling == 0:
        if direction == -1:
            if sector.floorheight - speed < dest:
                sector.floorheight = dest
                return PlaneResult.PASTDEST
            sector.floorheight -= speed
        else:
            if sector.floorheight + speed > dest:
                sector.floorheight = dest
                return PlaneResult.PASTDEST
            sector.floorheight += speed
            if blocker is not None and _blocks(blocker, sector):
                sector.floorheight -= speed
                return PlaneResult.CRUSHED
    else:
        if direction == -1:
            if sector.ceilingheight - speed < dest:
                sector.ceilingheight = dest
                return PlaneResult.PASTDEST
            sector.ceilingheight -= speed
            if blocker is not None and _blocks(blocker, sector):
                sector.ceilingheight += speed
                return PlaneResult.CRUSHED
        else:
            if sector.ceilingheight + speed > dest:
                sector.ceilingheight = dest
                return PlaneResult.PASTDEST
            sector.ceilingheight += speed
    return PlaneResult.OK


def _blocks(blocker, sector) -> bool:
    bsector, z, height = blocker
    if bsector is not sector:
        return False
    return sector.ceilingheight - max(sector.floorheight, z) < height


@dataclass(eq=False)
class VerticalDoor:
    """vldoor_t thinker state."""

    sector: object = field(default=None, repr=False)
    type: int = DoorType.NORMAL
    topheight: int = 0
    speed: int = VDOORSPEED
    direction: int = 1  # 1 = up, 0 = waiting, -1 = down, 2 = initial wait
    topwait: int = VDOORWAIT
    topcountdown: int = 0
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_VerticalDoor (sounds removed, blocker = viewer)."""
        door = self
        if door.direction == 0:
            # WAITING at top.
            door.topcountdown -= 1
            if door.topcountdown == 0:
                if door.type in (DoorType.BLAZERAISE, DoorType.NORMAL):
                    door.direction = -1
                    from pydoom import audio
                    audio.play("dorcls", door.sector.soundorg[0],
                               door.sector.soundorg[1], door.sector)
                elif door.type == DoorType.CLOSE30THENOPEN:
                    door.direction = 1
        elif door.direction == 2:
            # INITIAL WAIT (raiseIn5Mins only).
            door.topcountdown -= 1
            if door.topcountdown == 0 and door.type == DoorType.RAISEIN5MINS:
                door.direction = 1
                door.type = DoorType.NORMAL
        elif door.direction == -1:
            res = move_plane(door.sector, door.speed,
                             door.sector.floorheight, False, 1, -1,
                             world.blocker)
            if res == PlaneResult.PASTDEST:
                if door.type in (DoorType.BLAZERAISE, DoorType.BLAZECLOSE,
                                 DoorType.NORMAL, DoorType.CLOSE):
                    door.sector.specialdata = None
                    door.dead = True
                elif door.type == DoorType.CLOSE30THENOPEN:
                    door.direction = 0
                    door.topcountdown = 35 * 30
            elif res == PlaneResult.CRUSHED:
                if door.type not in (DoorType.BLAZECLOSE, DoorType.CLOSE):
                    door.direction = 1  # crushed: go back up
        elif door.direction == 1:
            res = move_plane(door.sector, door.speed, door.topheight,
                             False, 1, 1, world.blocker)
            if res == PlaneResult.PASTDEST:
                if door.type in (DoorType.BLAZERAISE, DoorType.NORMAL):
                    door.direction = 0
                    door.topcountdown = door.topwait
                elif door.type in (DoorType.CLOSE30THENOPEN,
                                   DoorType.BLAZEOPEN, DoorType.OPEN):
                    door.sector.specialdata = None
                    door.dead = True


@dataclass(eq=False)
class Button:
    line: object = field(default=None, repr=False)
    where: int = TOP
    btexture: int = 0
    btimer: int = 0


@dataclass(eq=False)
class LightFlash:
    """T_LightFlash: broken random flicker between min and max."""

    sector: object = field(default=None, repr=False)
    maxlight: int = 0
    minlight: int = 0
    maxtime: int = 64
    mintime: int = 7
    count: int = 0
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_LightFlash (p_lights.c); P_Random via m_random."""
        from pydoom.m_random import p_random
        self.count -= 1
        if self.count:
            return
        if self.sector.lightlevel == self.maxlight:
            self.sector.lightlevel = self.minlight
            self.count = (p_random() & self.mintime) + 1
        else:
            self.sector.lightlevel = self.maxlight
            self.count = (p_random() & self.maxtime) + 1


@dataclass(eq=False)
class StrobeFlash:
    """T_StrobeFlash: regular dark/bright strobe (sync or not)."""

    sector: object = field(default=None, repr=False)
    maxlight: int = 0
    minlight: int = 0
    darktime: int = 35
    brighttime: int = 5
    count: int = 0
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_StrobeFlash (p_lights.c)."""
        self.count -= 1
        if self.count:
            return
        if self.sector.lightlevel == self.minlight:
            self.sector.lightlevel = self.maxlight
            self.count = self.brighttime
        else:
            self.sector.lightlevel = self.minlight
            self.count = self.darktime


@dataclass(eq=False)
class GlowLight:
    """T_Glow: lightlevel breathes between min and max (GLOWSPEED)."""

    sector: object = field(default=None, repr=False)
    maxlight: int = 0
    minlight: int = 0
    direction: int = -1
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_Glow (p_lights.c)."""
        if self.direction == -1:
            self.sector.lightlevel -= 8
            if self.sector.lightlevel <= self.minlight:
                self.sector.lightlevel += 8
                self.direction = 1
        else:
            self.sector.lightlevel += 8
            if self.sector.lightlevel >= self.maxlight:
                self.sector.lightlevel -= 8
                self.direction = -1


@dataclass(eq=False)
class FireFlicker:
    """T_FireFlicker: torch-like jitter under the max (every 4 tics)."""

    sector: object = field(default=None, repr=False)
    maxlight: int = 0
    minlight: int = 0
    count: int = 0
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_FireFlicker (p_lights.c)."""
        from pydoom.m_random import p_random
        self.count -= 1
        if self.count:
            return
        amount = (p_random() & 3) * 16
        if self.sector.lightlevel - amount < self.minlight:
            self.sector.lightlevel = self.minlight
        else:
            self.sector.lightlevel = self.maxlight - amount
        self.count = 4


@dataclass(eq=False)
class FloorMover:
    """floormove_t thinker (p_floor.c), type as a string name."""

    sector: object = field(default=None, repr=False)
    type: str = "raiseFloor"
    crush: bool = False
    direction: int = 1
    newspecial: int = 0
    texture: int = 0
    floordestheight: int = 0
    speed: int = FLOORSPEED
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_MoveFloor (sounds removed)."""
        res = move_plane(self.sector, self.speed, self.floordestheight,
                         self.crush, 0, self.direction, world.blocker)
        if res == PlaneResult.PASTDEST:
            if self.direction == -1 and self.type == "lowerAndChange":
                self.sector.special = self.newspecial
                self.sector.floorpic = self.texture
            if self.direction == 1 and self.type == "donutRaise":
                self.sector.special = self.newspecial
                self.sector.floorpic = self.texture
            self.sector.specialdata = None
            self.dead = True


@dataclass(eq=False)
@dataclass(eq=False)
class Ceiling:
    """ceiling_t thinker (p_ceilng.c T_MoveCeiling), type as a string."""

    sector: object = field(default=None, repr=False)
    type: str = "crushAndRaise"
    crush: bool = False
    direction: int = -1
    topheight: int = 0
    bottomheight: int = 0
    speed: int = CEILSPEED
    tag: int = 0
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_MoveCeiling: raisers exit at top, crushers bounce, lowerers
        park at the floor. Crush damage runs through world.crush_hook
        every 4th tic (PIT_ChangeSector); grinding crushers slow down
        (fast ones never do, like vanilla)."""
        from pydoom import audio
        sec = self.sector
        if self.direction == 1:
            res = move_plane(sec, self.speed, self.topheight,
                             False, 1, 1, world.blocker)
            if not world.time & 7 and self.type != "silentCrushAndRaise":
                audio.play("stnmov", sec.soundorg[0], sec.soundorg[1],
                           sec)
            if res == PlaneResult.PASTDEST:
                if self.type == "raiseToHighest":
                    sec.specialdata = None
                    self.dead = True
                else:  # crushers bounce back down (silent pings pstop)
                    if self.type == "silentCrushAndRaise":
                        audio.play("pstop", sec.soundorg[0],
                                   sec.soundorg[1], sec)
                    self.direction = -1
        elif self.direction == -1:
            res = move_plane(sec, self.speed, self.bottomheight,
                             self.crush, 1, -1, world.blocker)
            if not world.time & 7 and self.type != "silentCrushAndRaise":
                audio.play("stnmov", sec.soundorg[0], sec.soundorg[1],
                           sec)
            if self.crush and not world.time & 3 \
                    and world.crush_hook is not None:
                world.crush_hook(sec)
            if res == PlaneResult.PASTDEST:
                if self.type in ("crushAndRaise", "fastCrushAndRaise"):
                    self.speed = CEILSPEED
                    self.direction = 1
                elif self.type == "silentCrushAndRaise":
                    audio.play("pstop", sec.soundorg[0], sec.soundorg[1],
                               sec)
                    self.speed = CEILSPEED
                    self.direction = 1
                else:  # lowerAndCrush/lowerToFloor park at the floor
                    sec.specialdata = None
                    self.dead = True
            elif res == PlaneResult.CRUSHED:
                if self.type in ("silentCrushAndRaise", "crushAndRaise",
                                 "lowerAndCrush"):
                    self.speed = CEILSPEED // 8


@dataclass(eq=False)
class Plat:
    """plat_t thinker (p_plats.c); status as a string name."""

    sector: object = field(default=None, repr=False)
    speed: int = FLOORSPEED
    low: int = 0
    high: int = 0
    wait: int = 0
    count: int = 0
    status: str = "down"  # up, down, waiting, in_stasis
    oldstatus: str = "down"
    crush: bool = False
    tag: int = 0
    type: str = "downWaitUpStay"
    dead: bool = False

    def think(self, world: "World") -> None:
        """T_PlatRaise (sounds removed)."""
        if self.status == "up":
            res = move_plane(self.sector, self.speed, self.high,
                             self.crush, 0, 1, world.blocker)
            if res == PlaneResult.CRUSHED and not self.crush:
                self.count = self.wait
                self.status = "down"
            elif res == PlaneResult.PASTDEST:
                self.count = self.wait
                self.status = "waiting"
                if self.type in ("blazeDWUS", "downWaitUpStay",
                                 "raiseAndChange",
                                 "raiseToNearestAndChange"):
                    world.remove_plat(self)
        elif self.status == "down":
            res = move_plane(self.sector, self.speed, self.low,
                             False, 0, -1, world.blocker)
            if res == PlaneResult.PASTDEST:
                self.count = self.wait
                self.status = "waiting"
        elif self.status == "waiting":
            self.count -= 1
            if self.count == 0:
                if self.sector.floorheight == self.low:
                    self.status = "up"
                else:
                    self.status = "down"
        # in_stasis: thinker parked, does nothing.


class World:
    """Level action state: thinkers, buttons, switches (gamesim lite)."""

    def __init__(self, game_map: Map, texman) -> None:
        self.map = game_map
        self.texman = texman
        self.thinkers: list = []
        self.activeplats: list[Plat] = []
        self.buttons: list[Button] = [Button() for _ in range(MAXBUTTONS)]
        # Switch texture pairs resolved to numbers (both must exist).
        self.switchlist: list[int] = []
        for name1, name2 in SWITCH_PAIRS:
            t1 = texman.check_texture_num_for_name(name1)
            t2 = texman.check_texture_num_for_name(name2)
            if t1 > 0 and t2 > 0:
                self.switchlist += [t1, t2]
        self.numswitches = len(self.switchlist) // 2
        # Optional player blocker for crush checks: (sector, z,
        # height) in fixed-point, refreshed by the viewer each tic.
        self.blocker = None
        # PIT_ChangeSector crush damage: the sim (viewer) sets a
        # callable(sector) that hurts everything the ceiling sits on.
        self.crush_hook = None
        # Intermission denominators (kills, items, secrets), set after
        # spawn; (0, 0, 0) keeps headless Worlds tally-free.
        self.totals = (0, 0, 0)
        self.message: str | None = None
        self.time = 0  # leveltime: sector damage ticks every 32
        self.exit_kind: str | None = None  # None | "normal" | "secret"
        self.teleport_angle = None  # BAM facing after a teleport (viewer)
        # NOTE: P_SpawnSpecials scrolling walls (first-side offset anim).
        self.scroll_lines = [li for li in game_map.lines
                             if li.special == 48]
        self.spawn_light_thinkers()

    # -- per-tic updates (p_tick.c lite + P_UpdateSpecials buttons) --

    def tick(self) -> None:
        self.time += 1
        for line in self.scroll_lines:
            if line.sidenum[0] != -1:
                side = self.map.sides[line.sidenum[0]]
                side.textureoffset += FRACUNIT  # EFFECT FIRSTCOL SCROLL +
        for thinker in list(self.thinkers):
            thinker.think(self)
        for t in self.thinkers:
            if t.dead and isinstance(t, Plat) and t.sector is not None:
                from pydoom import audio
                audio.play("pstop", t.sector.soundorg[0],
                           t.sector.soundorg[1], t.sector)
        self.thinkers = [t for t in self.thinkers if not t.dead]
        for button in self.buttons:
            if button.btimer:
                button.btimer -= 1
                if not button.btimer:
                    side = self.map.sides[button.line.sidenum[0]]
                    if button.where == TOP:
                        side.toptexture = button.btexture
                    elif button.where == MIDDLE:
                        side.midtexture = button.btexture
                    else:
                        side.bottomtexture = button.btexture
                    button.line = None

    # -- sector helpers (p_spec.c) --

    def find_lowest_ceiling(self, sector) -> int:
        height = MAXINT
        for line in sector.lines:
            if not (line.flags & ML_TWOSIDED):
                continue
            other = (line.backsector if line.frontsector is sector
                     else line.frontsector)
            if other is None:
                continue
            if other.ceilingheight < height:
                height = other.ceilingheight
        return height

    def find_highest_ceiling(self, sector) -> int:
        """P_FindHighestCeilingSurrounding (raiseToHighest target)."""
        height = -MAXINT
        for line in sector.lines:
            if not (line.flags & ML_TWOSIDED):
                continue
            other = (line.backsector if line.frontsector is sector
                     else line.frontsector)
            if other is None:
                continue
            if other.ceilingheight > height:
                height = other.ceilingheight
        return height

    def find_sectors_from_tag(self, tag: int):
        return [s for s in self.map.sectors if s.tag == tag]

    @staticmethod
    def _neighbors(sector):
        for line in sector.lines:
            if not (line.flags & ML_TWOSIDED):
                continue
            other = (line.backsector if line.frontsector is sector
                     else line.frontsector)
            if other is not None:
                yield other

    def find_min_light(self, sector, maxlight: int) -> int:
        """P_FindMinSurroundingLight: dimmest neighboring lightlevel."""
        low = maxlight
        for other in self._neighbors(sector):
            if other.lightlevel < low:
                low = other.lightlevel
        return low

    def spawn_light_thinkers(self) -> None:
        """P_SpawnSpecials light part: flicker/strobe/glow/fire thinkers
        per sector special (damage/secret/exit specials stay live)."""
        from pydoom.m_random import p_random
        for sec in self.map.sectors:
            special = sec.special
            if special == 1:
                fl = LightFlash(sector=sec, maxlight=sec.lightlevel,
                                minlight=self.find_min_light(
                                    sec, sec.lightlevel),
                                maxtime=64, mintime=7,
                                count=(p_random() & 64) + 1)
                self.thinkers.append(fl)
                sec.special = 0
            elif special in (2, 3, 4, 12, 13):
                fast = special in (2, 4, 13)
                strobe = StrobeFlash(
                    sector=sec, maxlight=sec.lightlevel,
                    minlight=self.find_min_light(sec, sec.lightlevel),
                    darktime=15 if fast else 35, brighttime=5,
                    count=1 if special in (12, 13)
                    else (p_random() & 7) + 1)
                if strobe.minlight == strobe.maxlight:
                    strobe.minlight = 0
                self.thinkers.append(strobe)
                if special != 4:
                    sec.special = 0  # NOTE: 4 keeps hurting (strobe+slime)
            elif special == 8:
                self.thinkers.append(GlowLight(
                    sector=sec, maxlight=sec.lightlevel,
                    minlight=self.find_min_light(sec, sec.lightlevel),
                    direction=-1))
                sec.special = 0
            elif special == 17:
                self.thinkers.append(FireFlicker(
                    sector=sec, maxlight=sec.lightlevel,
                    minlight=self.find_min_light(
                        sec, sec.lightlevel) + 16,
                    count=4))
                sec.special = 0

    def find_lowest_floor(self, sector) -> int:
        floor = sector.floorheight
        for other in self._neighbors(sector):
            if other.floorheight < floor:
                floor = other.floorheight
        return floor

    def find_highest_floor(self, sector) -> int:
        floor = -500 * FRACUNIT
        for other in self._neighbors(sector):
            if other.floorheight > floor:
                floor = other.floorheight
        return floor

    def find_next_highest_floor(self, sector, current: int) -> int:
        # P_FindNextHighestFloor (capped list in C; unbounded here).
        best = current
        for other in self._neighbors(sector):
            if other.floorheight > best:
                if best == current or other.floorheight < best:
                    best = other.floorheight
        return best

    # -- floors (p_floor.c; sounds removed) --

    def do_floor(self, line, ftype: str, amount: int = 0) -> bool:
        """EV_DoFloor for the supported types. Returns rtn."""
        from pydoom.textures import texture_height_fixed

        rtn = False
        for sec in self.find_sectors_from_tag(line.tag):
            if sec.specialdata is not None:
                continue  # already moving: keep going
            rtn = True
            floor = FloorMover(sector=sec, type=ftype, crush=False)
            self.thinkers.append(floor)
            sec.specialdata = floor
            if ftype == "lowerFloor":
                floor.direction, floor.speed = -1, FLOORSPEED
                floor.floordestheight = self.find_highest_floor(sec)
            elif ftype == "lowerFloorToLowest":
                floor.direction, floor.speed = -1, FLOORSPEED
                floor.floordestheight = self.find_lowest_floor(sec)
            elif ftype == "turboLower":
                floor.direction, floor.speed = -1, FLOORSPEED * 4
                floor.floordestheight = self.find_highest_floor(sec)
                if floor.floordestheight != sec.floorheight:
                    floor.floordestheight += 8 * FRACUNIT
            elif ftype in ("raiseFloorCrush", "raiseFloor"):
                floor.crush = ftype == "raiseFloorCrush"
                floor.direction, floor.speed = 1, FLOORSPEED
                floor.floordestheight = self.find_lowest_ceiling(sec)
                if floor.floordestheight > sec.ceilingheight:
                    floor.floordestheight = sec.ceilingheight
                floor.floordestheight -= 8 * FRACUNIT * (
                    ftype == "raiseFloorCrush")
            elif ftype == "raiseFloorTurbo":
                floor.direction, floor.speed = 1, FLOORSPEED * 4
                floor.floordestheight = self.find_next_highest_floor(
                    sec, sec.floorheight)
            elif ftype == "raiseFloorToNearest":
                floor.direction, floor.speed = 1, FLOORSPEED
                floor.floordestheight = self.find_next_highest_floor(
                    sec, sec.floorheight)
            elif ftype == "raiseFloor24":
                floor.direction, floor.speed = 1, FLOORSPEED
                floor.floordestheight = sec.floorheight + 24 * FRACUNIT
            elif ftype == "raiseFloor512":
                floor.direction, floor.speed = 1, FLOORSPEED
                floor.floordestheight = sec.floorheight + 512 * FRACUNIT
            elif ftype == "raiseFloor24AndChange":
                floor.direction, floor.speed = 1, FLOORSPEED
                floor.floordestheight = sec.floorheight + 24 * FRACUNIT
                sec.floorpic = line.frontsector.floorpic
                sec.special = line.frontsector.special
            elif ftype == "raiseToTexture":
                floor.direction, floor.speed = 1, FLOORSPEED
                minsize = MAXINT
                for tline in sec.lines:
                    if not (tline.flags & ML_TWOSIDED):
                        continue
                    for sindex in tline.sidenum:
                        if sindex == -1:
                            continue
                        tex = self.map.sides[sindex].bottomtexture
                        if tex >= 0:
                            size = texture_height_fixed(
                                self.texman.textures[tex])
                            minsize = min(minsize, size)
                if minsize == MAXINT:
                    # Guard: vanilla would launch the floor skyward;
                    # refuse instead.
                    self.thinkers.remove(floor)
                    sec.specialdata = None
                    rtn = False
                    continue
                floor.floordestheight = sec.floorheight + minsize
            elif ftype == "lowerAndChange":
                floor.direction, floor.speed = -1, FLOORSPEED
                floor.floordestheight = self.find_lowest_floor(sec)
                floor.texture = sec.floorpic
                for tline in sec.lines:
                    if not (tline.flags & ML_TWOSIDED):
                        continue
                    other = (tline.backsector
                             if tline.frontsector is sec
                             else tline.frontsector)
                    if other is not None and (
                            other.floorheight == floor.floordestheight):
                        floor.texture = other.floorpic
                        floor.newspecial = other.special
                        break
            else:  # pragma: no cover - unknown type
                self.thinkers.remove(floor)
                sec.specialdata = None
                rtn = False
        return rtn

    # -- plats / lifts (p_plats.c; sounds removed) --

    def do_plat(self, line, ptype: str, amount: int = 0) -> bool:
        """EV_DoPlat. Returns rtn."""
        _ = amount
        if ptype == "perpetualRaise":
            self.activate_in_stasis(line.tag)
        rtn = False
        for sec in self.find_sectors_from_tag(line.tag):
            if sec.specialdata is not None:
                continue
            rtn = True
            plat = Plat(sector=sec, type=ptype, crush=False, tag=line.tag)
            self.thinkers.append(plat)
            sec.specialdata = plat
            self.activeplats.append(plat)
            from pydoom import audio
            audio.play("stnmov", sec.soundorg[0], sec.soundorg[1], sec)
            if len(self.activeplats) > MAXPLATS:
                raise OverflowError("P_AddActivePlat: no more plats!")
            if ptype in ("raiseToNearestAndChange", "raiseAndChange"):
                plat.speed = PLATSPEED // 2
                plat.wait, plat.status = 0, "up"
                if ptype == "raiseAndChange":
                    plat.high = sec.floorheight + amount * FRACUNIT
                else:
                    plat.high = self.find_next_highest_floor(
                        sec, sec.floorheight)
                sec.floorpic = self.map.sides[
                    line.sidenum[0]].sector.floorpic
                sec.special = 0
            elif ptype in ("downWaitUpStay", "blazeDWUS"):
                plat.speed = PLATSPEED * (8 if ptype == "blazeDWUS" else 4)
                plat.low = self.find_lowest_floor(sec)
                if plat.low > sec.floorheight:
                    plat.low = sec.floorheight
                plat.high = sec.floorheight
                plat.wait = 35 * PLATWAIT
                plat.status = "down"
            elif ptype == "perpetualRaise":
                plat.speed = PLATSPEED
                plat.low = self.find_lowest_floor(sec)
                if plat.low > sec.floorheight:
                    plat.low = sec.floorheight
                plat.high = self.find_highest_floor(sec)
                if plat.high < sec.floorheight:
                    plat.high = sec.floorheight
                plat.wait = 35 * PLATWAIT
                from pydoom.m_random import p_random
                plat.status = "up" if p_random() & 1 else "down"
        return rtn

    def activate_in_stasis(self, tag: int) -> None:
        for plat in self.activeplats:
            if plat.tag == tag and plat.status == "in_stasis":
                plat.status = plat.oldstatus

    def stop_plat(self, line) -> None:
        """EV_StopPlat: park tagged plats in stasis."""
        for plat in self.activeplats:
            if plat.status != "in_stasis" and plat.tag == line.tag:
                plat.oldstatus = plat.status
                plat.status = "in_stasis"

    def remove_plat(self, plat: "Plat") -> None:
        """P_RemoveActivePlat."""
        plat.sector.specialdata = None
        plat.dead = True
        self.activeplats.remove(plat)

    # -- lights (p_lights.c lite) --

    def light_turn_on(self, line, bright: int) -> None:
        """EV_LightTurnOn: brightest-neighbor (0) or fixed level."""
        if not bright:
            for sec in self.find_sectors_from_tag(line.tag):
                for other in self._neighbors(sec):
                    bright = max(bright, other.lightlevel)
        for sec in self.find_sectors_from_tag(line.tag):
            sec.lightlevel = bright

    def do_ceiling(self, line, ctype: str) -> bool:
        """EV_DoCeiling (p_ceilng.c): crushers bounce, lowerers park,
        raisers exit at top. lowerAndCrush grinds without hurting
        (crush stays false), exactly like vanilla. No in-stasis
        reactivation (EV_CeilingCrushStop is not wired either)."""
        rtn = False
        for sec in self.find_sectors_from_tag(line.tag):
            if sec.specialdata is not None:
                continue  # already moving: keep going
            ceil = Ceiling(sector=sec, type=ctype, tag=sec.tag)
            if ctype == "fastCrushAndRaise":
                ceil.crush = True
                ceil.topheight = sec.ceilingheight
                ceil.bottomheight = sec.floorheight + 8 * FRACUNIT
                ceil.direction = -1
                ceil.speed = CEILSPEED * 2
            elif ctype in ("silentCrushAndRaise", "crushAndRaise",
                           "lowerAndCrush", "lowerToFloor"):
                if ctype in ("silentCrushAndRaise", "crushAndRaise"):
                    ceil.crush = True
                    ceil.topheight = sec.ceilingheight
                ceil.bottomheight = sec.floorheight
                if ctype != "lowerToFloor":
                    ceil.bottomheight += 8 * FRACUNIT
                ceil.direction = -1
                ceil.speed = CEILSPEED
            elif ctype == "raiseToHighest":
                ceil.topheight = self.find_highest_ceiling(sec)
                ceil.direction = 1
                ceil.speed = CEILSPEED
            else:
                continue
            self.thinkers.append(ceil)
            sec.specialdata = ceil
            rtn = True
        return rtn

    # -- doors (p_doors.c; sounds removed) --

    def do_door(self, line, dtype: int) -> bool:
        """EV_DoDoor: open/close tagged sectors. Returns rtn."""
        from pydoom import audio
        rtn = False
        for sec in self.find_sectors_from_tag(line.tag):
            if sec.specialdata is not None:
                continue
            rtn = True
            door = VerticalDoor(sector=sec, type=dtype, topwait=VDOORWAIT,
                                speed=VDOORSPEED)
            self.thinkers.append(door)
            sec.specialdata = door
            audio.play("dorcls" if dtype in (DoorType.BLAZECLOSE,
                                                 DoorType.CLOSE,
                                                 DoorType.CLOSE30THENOPEN)
                       else "doropn",
                       sec.soundorg[0], sec.soundorg[1], sec)
            if dtype == DoorType.BLAZECLOSE:
                door.topheight = self.find_lowest_ceiling(sec) - 4 * FRACUNIT
                door.direction = -1
                door.speed = VDOORSPEED * 4
            elif dtype == DoorType.CLOSE:
                door.topheight = self.find_lowest_ceiling(sec) - 4 * FRACUNIT
                door.direction = -1
            elif dtype == DoorType.CLOSE30THENOPEN:
                door.topheight = sec.ceilingheight
                door.direction = -1
            elif dtype in (DoorType.BLAZERAISE, DoorType.BLAZEOPEN):
                door.direction = 1
                door.topheight = self.find_lowest_ceiling(sec) - 4 * FRACUNIT
                door.speed = VDOORSPEED * 4
            else:  # NORMAL, OPEN
                door.direction = 1
                door.topheight = self.find_lowest_ceiling(sec) - 4 * FRACUNIT
        return rtn

    def vertical_door(self, line, is_player: bool,
                        keys: int = 0) -> str | None:
        """EV_VerticalDoor: manual door on its own sector."""
        from pydoom import audio
        if line.special in _MANUAL_LOCKS:
            from pydoom.player import KEY_COLORS
            if not (keys & KEY_COLORS[_MANUAL_LOCK_COLOR[line.special]]):
                if not is_player:
                    return None  # monsters never open locked doors
                sec0 = self.map.sides[line.sidenum[0]].sector
                audio.play("noway", sec0.soundorg[0], sec0.soundorg[1], line)
                return _MANUAL_LOCKS[line.special]
        if line.sidenum[1] == -1:
            return None  # guard: vanilla would crash here
        sec = self.map.sides[line.sidenum[1]].sector
        if sec.specialdata is not None:
            door = sec.specialdata
            if line.special in (1, 26, 27, 28, 117):
                if door.direction == -1:
                    door.direction = 1  # go back up
                    audio.play("doropn", sec.soundorg[0], sec.soundorg[1], sec)
                else:
                    if not is_player:
                        return None  # monsters never close doors
                    door.direction = -1
                    audio.play("dorcls", sec.soundorg[0], sec.soundorg[1], sec)
            return None
        door = VerticalDoor(sector=sec, direction=1, speed=VDOORSPEED,
                            topwait=VDOORWAIT)
        self.thinkers.append(door)
        sec.specialdata = door
        audio.play("doropn", sec.soundorg[0], sec.soundorg[1], sec)
        if line.special in (1, 26, 27, 28):
            door.type = DoorType.NORMAL
        elif line.special in (31, 32, 33, 34):
            door.type = DoorType.OPEN
            line.special = 0
        elif line.special == 117:
            door.type = DoorType.BLAZERAISE
            door.speed = VDOORSPEED * 4
        elif line.special == 118:
            door.type = DoorType.BLAZEOPEN
            line.special = 0
            door.speed = VDOORSPEED * 4
        else:
            self.thinkers.remove(door)
            sec.specialdata = None
            return None
        door.topheight = self.find_lowest_ceiling(sec) - 4 * FRACUNIT
        return None

    # -- switches (p_switch.c; sounds removed) --

    def change_switch_texture(self, line, use_again: bool) -> None:
        # NOTE: exit switches click swtchx, the rest swtchn (p_switch.c).
        special = line.special
        from pydoom import audio
        audio.play("swtchx" if special == 11 else "swtchn",
                   (line.v1.x + line.v2.x) // 2,
                   (line.v1.y + line.v2.y) // 2, line)
        if not use_again:
            line.special = 0
        side = self.map.sides[line.sidenum[0]]
        for i in range(self.numswitches * 2):
            if self.switchlist[i] == side.toptexture:
                side.toptexture = self.switchlist[i ^ 1]
                if use_again:
                    self._start_button(line, TOP, self.switchlist[i])
                return
            if self.switchlist[i] == side.midtexture:
                side.midtexture = self.switchlist[i ^ 1]
                if use_again:
                    self._start_button(line, MIDDLE, self.switchlist[i])
                return
            if self.switchlist[i] == side.bottomtexture:
                side.bottomtexture = self.switchlist[i ^ 1]
                if use_again:
                    self._start_button(line, BOTTOM, self.switchlist[i])
                return

    def _start_button(self, line, where: int, texture: int) -> None:
        for button in self.buttons:
            if button.btimer and button.line is line:
                return  # already pressed
        for button in self.buttons:
            if not button.btimer:
                button.line, button.where = line, where
                button.btexture, button.btimer = texture, BUTTONTIME
                return
        raise OverflowError("P_StartButton: no button slots left!")

    def use_special_line(self, line, side: int, is_player: bool,
                           keys: int = 0, mover=None, physics=None,
                           mobjs=None) -> str | None:
        """P_UseSpecialLine, manual doors + door/floor/plat/light switches."""
        if side:
            return None  # only case 124 uses backs; unused
        if not is_player and (
            (line.flags & ML_SECRET)
            or line.special not in (1, 32, 33, 34)
        ):
            return None
        special = line.special
        if special in _MANUAL_DOORS:
            return self.vertical_door(line, is_player, keys)
        if special in _SWITCH_LOCKS:
            from pydoom.player import KEY_COLORS
            if not (keys & KEY_COLORS[_SWITCH_LOCK_COLOR[special]]):
                from pydoom import audio
                if mover is not None:
                    audio.play("noway", mover.x, mover.y, mover)
                return _SWITCH_LOCKS[special]
            # NOTE: key held, fall through to the door action below.
        if special == 97:
            # NOTE: SR teleport needs the activator body (E1M8 exit).
            if mover is None or physics is None or mobjs is None:
                return None
            if self.teleport(line, mover, physics, mobjs, side):
                self.change_switch_texture(line, True)
            return None
        if special in _SWITCH_DOORS:
            dtype, use_again = _SWITCH_DOORS[special]
            if self.do_door(line, dtype):
                self.change_switch_texture(line, use_again)
            return None
        if special in _SWITCH_FLOORS:
            ftype, use_again = _SWITCH_FLOORS[special]
            if self.do_floor(line, ftype):
                self.change_switch_texture(line, use_again)
            return None
        if special in _SWITCH_PLATS:
            ptype, amount, use_again = _SWITCH_PLATS[special]
            if self.do_plat(line, ptype, amount):
                self.change_switch_texture(line, use_again)
            return None
        if special in _SWITCH_LIGHTS:
            self.light_turn_on(line, _SWITCH_LIGHTS[special])
            self.change_switch_texture(line, True)
            return None
        if special in (11, 51):
            self.change_switch_texture(line, False)
            self.exit_kind = "secret" if special == 51 else "normal"
            return None
        if special == 9:
            if self.do_donut(line):
                self.change_switch_texture(line, False)
            return None
        if special == 7:
            if self.build_stairs(line, 8 * FRACUNIT, FLOORSPEED // 4):
                self.change_switch_texture(line, False)
            return None
        if special == 127:
            if self.build_stairs(line, 16 * FRACUNIT, FLOORSPEED * 4):
                self.change_switch_texture(line, True)
            return None
        if special == 141:
            # NOTE: S1 silent crusher (no stnmov/pstop chatter).
            if self.do_ceiling(line, "silentCrushAndRaise"):
                self.change_switch_texture(line, False)
            return None
        if special in (41, 43, 49):
            return f"Switch {special}: not implemented yet"
        return None

    def do_donut(self, line) -> bool:
        """EV_DoDonut: ring rises to the pool, pillar drops into it."""
        rtn = False
        for s1 in self.find_sectors_from_tag(line.tag):
            if s1.specialdata is not None:
                continue  # already moving: keep going
            if not s1.lines:
                continue
            first = s1.lines[0]
            if not (first.flags & ML_TWOSIDED):
                continue
            s2 = (first.backsector if first.frontsector is s1
                  else first.frontsector)
            if s2 is None:
                continue
            for ld in s2.lines:
                if not (ld.flags & ML_TWOSIDED):
                    continue
                if ld.backsector is s1:
                    continue
                s3 = ld.backsector
                if s3 is None:
                    continue
                # NOTE: rising slime takes the pool's skin and clears.
                rise = FloorMover(sector=s2, type="donutRaise", crush=False)
                rise.direction, rise.speed = 1, FLOORSPEED // 2
                rise.floordestheight = s3.floorheight
                rise.texture, rise.newspecial = s3.floorpic, 0
                self.thinkers.append(rise)
                s2.specialdata = rise
                drop = FloorMover(sector=s1, type="lowerFloor", crush=False)
                drop.direction, drop.speed = -1, FLOORSPEED // 2
                drop.floordestheight = s3.floorheight
                self.thinkers.append(drop)
                s1.specialdata = drop
                rtn = True
                break
        return rtn

    def shoot_special_line(self, line, is_player: bool) -> None:
        """P_ShootSpecialLine: guns pop 24/46/47 (monsters only 46)."""
        if not is_player and line.special != 46:
            return
        if line.special == 24:
            if self.do_floor(line, "raiseFloor"):
                self.change_switch_texture(line, False)
        elif line.special == 46:
            if self.do_door(line, DoorType.OPEN):
                self.change_switch_texture(line, True)
        elif line.special == 47:
            if self.do_plat(line, "raiseToNearestAndChange", 0):
                self.change_switch_texture(line, False)

    def cross_special_line(self, line, is_player: bool, mover=None,
                             physics=None, mobjs=None) -> str | None:
        """P_CrossSpecialLine: W1 (once) and WR (retrigger) walk-overs."""
        if not is_player and line.special not in (
                39, 97, 125, 126, 4, 10, 88):
            return None  # monsters trigger almost nothing
        special = line.special
        if special in _WALK_ONCE:
            kind, arg = _WALK_ONCE[special]
            done = self._fire_walk(kind, arg, line)
            if special not in (52,):  # exits don't clear
                line.special = 0
            return done
        if special in _WALK_RETRIGGER:
            kind, arg = _WALK_RETRIGGER[special]
            return self._fire_walk(kind, arg, line)
        if special in (39, 97):
            if special == 39 and mover is not None and physics is not None \
                    and mobjs is not None:
                if self.teleport(line, mover, physics, mobjs):
                    line.special = 0  # W1 fires once
                return None
            return "Teleporter (not implemented yet)"
        return None

    def _fire_walk(self, kind: str, arg, line) -> str | None:
        if kind == "door":
            self.do_door(line, arg)
        elif kind == "stairs":
            self.build_stairs(line, 8 * FRACUNIT, FLOORSPEED // 4)
        elif kind == "floor":
            self.do_floor(line, arg)
        elif kind == "ceiling":
            self.do_ceiling(line, arg)
        elif kind == "plat":
            ptype, amount = arg
            self.do_plat(line, ptype, amount)
        elif kind == "light":
            self.light_turn_on(line, arg)
        elif kind == "exit":
            self.exit_kind = "normal"
            return None
        elif kind == "teleport":
            return "Teleporter (not implemented yet)"
        return None

    def use_lines(self, x: int, y: int, angle_bam: int,
                  physics: Physics, keys: int = 0, mover=None,
                  mobjs=None) -> str | None:
        """P_UseLines: trace USERANGE forward, use the first special."""
        angle = (angle_bam & 0xFFFFFFFF) >> 19  # ANGLETOFINESHIFT
        x2 = x + 64 * tables.finecosine(angle)
        y2 = y + 64 * tables.finesine[angle]
        hit: list = []

        def trav(intercept) -> bool:
            line = intercept[1]
            if not line.special:
                opentop, openbottom, openrange, _low = (
                    physics.line_opening(line)
                )
                if openrange <= 0:
                    hit.append("Can't use through a wall")
                    return False
                return True
            side = 0
            if point_on_line_side(x, y, line) == 1:
                side = 1
            hit.append(self.use_special_line(line, side, True, keys,
                                               mover, physics, mobjs))
            return False  # one special per use

        physics.path_traverse(x, y, x2, y2, 1, trav)  # PT_ADDLINES
        return hit[0] if hit else None

    def teleport(self, line, mover, physics, mobjs, side: int = 0) -> bool:
        """P_TeleportMove + EV_Teleport: hop to the tagged teleportman.

        Telefrags the landing first, then refuses a still-blocked pad
        (P_CheckPosition) with no fog at all, like vanilla. On success:
        fog at both ends, angle/height from the destination (E1M8 exit
        chain). False when no destination pads the line's tag. Missiles
        never ride, and the back side stays shut so arrivals can step
        off.
        """
        from pydoom.info import MT_INDEX
        if getattr(mover, "flags", 0) & MF_FLAGS["MF_MISSILE"]:
            return False
        if side == 1:
            return False
        dest = None
        for mo in mobjs:
            if mo.dead or mo.type != MT_INDEX["TELEPORTMAN"]:
                continue
            if mo.sector is not None and mo.sector.tag == line.tag:
                dest = mo
                break
        if dest is None:
            return False
        from pydoom import tables
        from pydoom.mobjs import refresh_sector, spawn_mobj
        from pydoom import audio
        if physics.things is not None:
            from pydoom.physics import MAPBLOCKSHIFT, MAXRADIUS
            bm = physics.map.blockmap
            r = mover.radius
            xl = (dest.x - r - bm.orgx - MAXRADIUS) >> MAPBLOCKSHIFT
            xh = (dest.x + r - bm.orgx + MAXRADIUS) >> MAPBLOCKSHIFT
            yl = (dest.y - r - bm.orgy - MAXRADIUS) >> MAPBLOCKSHIFT
            yh = (dest.y + r - bm.orgy + MAXRADIUS) >> MAPBLOCKSHIFT
            for bx in range(xl, xh + 1):
                for by in range(yl, yh + 1):
                    for th in list(physics.things.iter_block(bx, by)):
                        if th is mover or th.dead:
                            continue
                        if not (th.flags & 6):  # solid or shootable
                            continue
                        from pydoom.combat import damage_mobj
                        damage_mobj(th, mover, mover, 10000, None)
        if not physics.check_position(mover, dest.x, dest.y).ok:
            return False  # NOTE: landing still blocked, stay put
        fog = spawn_mobj(None, physics, physics.things, mover.x, mover.y,
                         mover.z, MT_INDEX["TFOG"])
        fog.momz = 65536
        audio.play("telept", mover.x, mover.y, fog)
        physics.things.move(mover, dest.x, dest.y)
        mover.momx = mover.momy = mover.momz = 0
        mover.angle = dest.angle  # Mobj field; camera Movers gain one
        if getattr(mover, "is_player", False):
            mover.reactiontime = 18  # NOTE: don't move for a bit
        self.teleport_angle = dest.angle
        if dest.sector is not None:
            mover.floorz = dest.sector.floorheight
            mover.ceilingz = dest.sector.ceilingheight
            mover.z = mover.floorz
        refresh_sector(mover, physics)
        fa = (dest.angle & 0xFFFFFFFF) >> 19
        fog2 = spawn_mobj(None, physics, physics.things,
                          dest.x + 20 * tables.finecosine(fa),
                          dest.y + 20 * tables.finesine[fa],
                          mover.z, MT_INDEX["TFOG"])
        fog2.momz = 65536
        audio.play("telept", fog2.x, fog2.y, fog2)
        mobjs.append(fog)
        mobjs.append(fog2)
        return True

    def player_in_special_sector(self, player_mo, ps, ctx=None):
        """P_PlayerInSpecialSector: slime damage, secrets, E1M8 exit burn.

        Returns a message for secrets, if any (exit is signalled via
        exit_kind, like the line exits).
        """
        if player_mo.sector is None:
            return None
        if player_mo.z != player_mo.sector.floorheight:
            return None  # NOTE: falling, not all the way down yet
        special = player_mo.sector.special
        if special == 9:
            player_mo.sector.special = 0
            ps.secretcount += 1  # NOTE: intermission tally
            return "A SECRET IS REVEALED!"
        if special in (5, 7, 16, 4):
            from pydoom.combat import damage_mobj
            from pydoom.m_random import p_random
            from pydoom.player import PW_IRONFEET
            suit = bool(ps.powers.get(PW_IRONFEET))
            if special in (5, 7):
                if suit:
                    return None  # suit holds on mild slime
            elif suit and p_random() >= 5:
                return None  # strobe hurt only leaks through rarely
            if not (self.time & 0x1F):
                damage = 20 if special in (16, 4) else (
                    10 if special == 5 else 5)
                damage_mobj(player_mo, None, None, damage, ctx)
            return None
        if special == 11:
            from pydoom.combat import damage_mobj
            if not (self.time & 0x1F):
                damage_mobj(player_mo, None, None, 20, ctx)
            if player_mo.health <= 10:
                self.exit_kind = "normal"  # E1M8 finale burn-out
            return None
        return None

    def lower_floors_by_tag(self, tag: int) -> bool:
        """A_BossDeath helper: drop every tagged floor to lowest."""
        from types import SimpleNamespace
        return self.do_floor(SimpleNamespace(tag=tag), "lowerFloorToLowest")

    def build_stairs(self, line, stairsize: int, speed: int) -> bool:
        """EV_BuildStairs: raise tagged sectors in same-texture steps."""
        rtn = False
        for sec in self.find_sectors_from_tag(line.tag):
            if sec.specialdata is not None:
                continue  # already moving: keep going
            rtn = True
            dest = sec.floorheight + stairsize
            texture = sec.floorpic
            self._start_stair_step(sec, dest, speed)
            # NOTE: climb out over two-sided lines whose front faces us
            # into same-textured backs, one step per sector.
            cur, curdest = sec, dest
            while True:
                advanced = False
                for ld in cur.lines:
                    if not (ld.flags & ML_TWOSIDED):
                        continue
                    if ld.frontsector is not cur:
                        continue
                    back = ld.backsector
                    if back is None or back.floorpic != texture:
                        continue
                    if back.specialdata is not None:
                        continue
                    curdest += stairsize
                    self._start_stair_step(back, curdest, speed)
                    cur, advanced = back, True
                    break
                if not advanced:
                    break
        return rtn

    def _start_stair_step(self, sec, dest: int, speed: int) -> None:
        floor = FloorMover(sector=sec, type="buildStairs", crush=False)
        floor.direction, floor.speed = 1, speed
        floor.floordestheight = dest
        self.thinkers.append(floor)
        sec.specialdata = floor
