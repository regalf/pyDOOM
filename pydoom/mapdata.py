"""Map loading (``p_setup.c``) and data formats (``doomdata.h``, ``r_defs.h``).

Same load order as ``P_SetupLevel``::

    BLOCKMAP -> VERTEXES -> SECTORS -> SIDEDEFS -> LINEDEFS
        -> SSECTORS -> NODES -> SEGS -> REJECT -> GroupLines -> THINGS

Pragmatic simplifications vs. the C code (documented, never silent):

* ``floorpic``/``ceilingpic`` (sectors) and ``top/mid/bottomtexture``
  (sidedefs) are kept as **names** (8-char strings). The numeric
  ``floorpic``/``toptexture``/etc. fields stay at ``-1`` until the
  future texture module provides a resolver
  (``R_FlatNumForName`` / ``R_TextureNumForName``). This avoids a
  renderer -> maploader dependency.
* The C ``P_LoadThings`` filters out Doom II monsters when
  ``gamemode != commercial`` (with the historic bug: ``break`` instead
  of ``continue``); here we load **all** raw things. Skill/gamemode
  filtering will be the responsibility of the spawner (``p_mobj``).
* No zone memory: structures are plain Python dataclasses held by
  :class:`Map`. Cross-references (e.g. ``seg.linedef``) are real object
  references, not indices, just like the C pointers.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from enum import IntEnum

from pydoom.fixed import FRACBITS, fixed_div
from pydoom.wad import WadFile

__all__ = [
    "ML_LABEL",
    "ML_THINGS",
    "ML_LINEDEFS",
    "ML_SIDEDEFS",
    "ML_VERTEXES",
    "ML_SEGS",
    "ML_SSECTORS",
    "ML_NODES",
    "ML_SECTORS",
    "ML_REJECT",
    "ML_BLOCKMAP",
    "ML_BLOCKING",
    "ML_BLOCKMONSTERS",
    "ML_TWOSIDED",
    "ML_DONTPEGTOP",
    "ML_DONTPEGBOTTOM",
    "ML_SECRET",
    "ML_SOUNDBLOCK",
    "ML_DONTDRAW",
    "ML_MAPPED",
    "SlopeType",
    "BOX",
    "NF_SUBSECTOR",
    "MAPBLOCKSHIFT",
    "MAXRADIUS",
    "Vertex",
    "Sector",
    "Side",
    "Line",
    "Seg",
    "Subsector",
    "Node",
    "MapThing",
    "Blockmap",
    "Map",
]

# --- Map lump order (doomdata.h) ---
ML_LABEL = 0
ML_THINGS = 1
ML_LINEDEFS = 2
ML_SIDEDEFS = 3
ML_VERTEXES = 4
ML_SEGS = 5
ML_SSECTORS = 6
ML_NODES = 7
ML_SECTORS = 8
ML_REJECT = 9
ML_BLOCKMAP = 10

# --- LineDef flags (doomdata.h) ---
ML_BLOCKING = 1
ML_BLOCKMONSTERS = 2
ML_TWOSIDED = 4
ML_DONTPEGTOP = 8
ML_DONTPEGBOTTOM = 16
ML_SECRET = 32
ML_SOUNDBLOCK = 64
ML_DONTDRAW = 128
ML_MAPPED = 256


class SlopeType(IntEnum):
    """``slopetype_t`` from r_defs.h (same order)."""

    ST_HORIZONTAL = 0
    ST_VERTICAL = 1
    ST_POSITIVE = 2
    ST_NEGATIVE = 3


class BOX(IntEnum):
    """bbox indices from m_bbox.h: note BOXTOP=0 comes first (historic)."""

    BOXTOP = 0
    BOXBOTTOM = 1
    BOXLEFT = 2
    BOXRIGHT = 3


NF_SUBSECTOR = 0x8000  # BSP leaf flag (doomdata.h)

# p_local.h
MAPBLOCKSHIFT = FRACBITS + 7  # 128 map-unit blocks
MAXRADIUS = 32 * (1 << FRACBITS)

# --- Lump binary structs (all little-endian: SHORT/LONG are identity) ---
_S_VERTEX = struct.Struct("<hh")          # mapvertex_t
_S_SIDEDEF = struct.Struct("<hh8s8s8sh")  # mapsidedef_t
_S_LINEDEF = struct.Struct("<hhhhhhh")    # maplinedef_t
_S_SECTOR = struct.Struct("<hh8s8shhh")   # mapsector_t
_S_SUBSECTOR = struct.Struct("<hh")       # mapsubsector_t
_S_SEG = struct.Struct("<hhhhhh")         # mapseg_t
_S_NODE_HDR = struct.Struct("<hhhh")      # x, y, dx, dy
_S_NODE_BBOX = struct.Struct("<hhhh")     # one bbox (4 shorts)
_S_NODE_CHILD = struct.Struct("<HH")      # children: unsigned short!
_S_THING = struct.Struct("<hhhhh")        # mapthing_t
_S_BLOCKMAP_HDR = struct.Struct("<hhhh")  # orgx, orgy, width, height


def _lump_name(raw: bytes) -> str:
    """8-byte texture/flat name -> str (strip NUL, upper like in the WAD)."""
    return raw.split(b"\x00")[0].decode("ascii").upper()


# --- Runtime structures (r_defs.h) ---

@dataclass(eq=False)
class Vertex:
    x: int  # fixed_t
    y: int  # fixed_t


@dataclass(eq=False)
class Sector:
    floorheight: int  # fixed_t
    ceilingheight: int  # fixed_t
    floorpic_name: str = ""
    ceilingpic_name: str = ""
    floorpic: int = -1  # resolved by the texture module (R_FlatNumForName)
    ceilingpic: int = -1
    lightlevel: int = 0
    special: int = 0
    tag: int = 0
    linecount: int = 0
    lines: list["Line"] = field(default_factory=list, repr=False)
    blockbox: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    soundorg: tuple[int, int] = (0, 0)
    specialdata: object = field(default=None, repr=False)  # active thinker
    thinglist: list = field(default_factory=list, repr=False)
    validcount: int = 0  # traversal stamp (shared physics/AI)
    soundtraversed: int = 0  # noise flood level (p_enemy.c)
    soundtarget: object = field(default=None, repr=False)  # noise target


@dataclass(eq=False)
class Side:
    textureoffset: int  # fixed_t
    rowoffset: int  # fixed_t
    top_name: str = ""
    mid_name: str = ""
    bottom_name: str = ""
    toptexture: int = -1  # resolved by the texture module
    midtexture: int = -1
    bottomtexture: int = -1
    sector: "Sector | None" = field(default=None, repr=False)


@dataclass(eq=False)
class Line:
    v1: "Vertex | None" = field(default=None, repr=False)
    v2: "Vertex | None" = field(default=None, repr=False)
    dx: int = 0  # fixed_t
    dy: int = 0  # fixed_t
    flags: int = 0
    special: int = 0
    tag: int = 0
    sidenum: list[int] = field(default_factory=lambda: [-1, -1])
    bbox: list[int] = field(default_factory=lambda: [0, 0, 0, 0])
    slopetype: SlopeType = SlopeType.ST_HORIZONTAL
    frontsector: "Sector | None" = field(default=None, repr=False)
    backsector: "Sector | None" = field(default=None, repr=False)
    validcount: int = 0  # blockmap/traverse dedup stamp (p_maputl.c)


@dataclass(eq=False)
class Seg:
    v1: "Vertex | None" = field(default=None, repr=False)
    v2: "Vertex | None" = field(default=None, repr=False)
    angle: int = 0  # angle_t (BAM, short<<16)
    offset: int = 0  # fixed_t
    linedef: "Line | None" = field(default=None, repr=False)
    sidenum: int = 0
    sidedef: "Side | None" = field(default=None, repr=False)
    frontsector: "Sector | None" = field(default=None, repr=False)
    backsector: "Sector | None" = field(default=None, repr=False)


@dataclass(eq=False)
class Subsector:
    numlines: int = 0
    firstline: int = 0
    sector: "Sector | None" = field(default=None, repr=False)


@dataclass(eq=False)
class Node:
    x: int = 0  # fixed_t (partition line)
    y: int = 0
    dx: int = 0
    dy: int = 0
    bbox: list[list[int]] = field(default_factory=lambda: [[0] * 4, [0] * 4])
    children: list[int] = field(default_factory=lambda: [0, 0])


@dataclass(eq=False)
class MapThing:
    """Raw ``mapthing_t`` (no skill/gamemode filtering, see module docstring)."""

    x: int = 0
    y: int = 0
    angle: int = 0
    type: int = 0
    options: int = 0


@dataclass(eq=False)
class Blockmap:
    orgx: int = 0  # fixed_t
    orgy: int = 0  # fixed_t
    width: int = 0
    height: int = 0
    lists: list[int] = field(default_factory=list)  # offsets/linedefs past the header


@dataclass(eq=False)
class Map:
    """All data of one map, loaded with :meth:`from_wad`."""

    marker: str = ""
    vertexes: list[Vertex] = field(default_factory=list)
    sectors: list[Sector] = field(default_factory=list)
    sides: list[Side] = field(default_factory=list)
    lines: list[Line] = field(default_factory=list)
    segs: list[Seg] = field(default_factory=list)
    subsectors: list[Subsector] = field(default_factory=list)
    nodes: list[Node] = field(default_factory=list)
    things: list[MapThing] = field(default_factory=list)
    blockmap: Blockmap = field(default_factory=Blockmap)
    reject: bytes = b""

    # -- entry point (P_SetupLevel, loading part only) --

    @classmethod
    def from_wad(cls, wad: WadFile, marker: str) -> "Map":
        lump = wad.map_lump_indices(marker.upper())
        m = cls(marker=marker.upper())
        # Same order as P_SetupLevel: blockmap first (needed by GroupLines),
        # linedefs after sidedefs (they resolve sectors), segs last
        # (they resolve lines/sides/sectors).
        m._load_blockmap(wad.cache_lump(lump["BLOCKMAP"]))
        m._load_vertexes(wad.cache_lump(lump["VERTEXES"]))
        m._load_sectors(wad.cache_lump(lump["SECTORS"]))
        m._load_sidedefs(wad.cache_lump(lump["SIDEDEFS"]))
        m._load_linedefs(wad.cache_lump(lump["LINEDEFS"]))
        m._load_subsectors(wad.cache_lump(lump["SSECTORS"]))
        m._load_nodes(wad.cache_lump(lump["NODES"]))
        m._load_segs(wad.cache_lump(lump["SEGS"]))
        m.reject = bytes(wad.cache_lump(lump["REJECT"]))
        m._group_lines()
        m._load_things(wad.cache_lump(lump["THINGS"]))
        return m

    # -- single-lump loaders (P_Load*) --

    def _load_vertexes(self, data: bytes) -> None:
        n = len(data) // _S_VERTEX.size
        self.vertexes = [
            Vertex(x=x << FRACBITS, y=y << FRACBITS)
            for x, y in _S_VERTEX.iter_unpack(data[: n * _S_VERTEX.size])
        ]

    def _load_sectors(self, data: bytes) -> None:
        n = len(data) // _S_SECTOR.size
        self.sectors = []
        for fh, ch, fn, cn, light, special, tag in _S_SECTOR.iter_unpack(
            data[: n * _S_SECTOR.size]
        ):
            self.sectors.append(
                Sector(
                    floorheight=fh << FRACBITS,
                    ceilingheight=ch << FRACBITS,
                    floorpic_name=_lump_name(fn),
                    ceilingpic_name=_lump_name(cn),
                    lightlevel=light,
                    special=special,
                    tag=tag,
                )
            )

    def _load_sidedefs(self, data: bytes) -> None:
        n = len(data) // _S_SIDEDEF.size
        self.sides = []
        for toff, roff, top, bottom, mid, sec in _S_SIDEDEF.iter_unpack(
            data[: n * _S_SIDEDEF.size]
        ):
            self.sides.append(
                Side(
                    textureoffset=toff << FRACBITS,
                    rowoffset=roff << FRACBITS,
                    top_name=_lump_name(top),
                    bottom_name=_lump_name(bottom),
                    mid_name=_lump_name(mid),
                    sector=self.sectors[sec],
                )
            )

    def _load_linedefs(self, data: bytes) -> None:
        n = len(data) // _S_LINEDEF.size
        self.lines = []
        for v1i, v2i, flags, special, tag, s0, s1 in _S_LINEDEF.iter_unpack(
            data[: n * _S_LINEDEF.size]
        ):
            v1 = self.vertexes[v1i]
            v2 = self.vertexes[v2i]
            dx = v2.x - v1.x
            dy = v2.y - v1.y
            if dx == 0:
                slope = SlopeType.ST_VERTICAL
            elif dy == 0:
                slope = SlopeType.ST_HORIZONTAL
            else:
                slope = (
                    SlopeType.ST_POSITIVE
                    if fixed_div(dy, dx) > 0
                    else SlopeType.ST_NEGATIVE
                )
            bbox = [0, 0, 0, 0]
            bbox[BOX.BOXTOP] = max(v1.y, v2.y)
            bbox[BOX.BOXBOTTOM] = min(v1.y, v2.y)
            bbox[BOX.BOXLEFT] = min(v1.x, v2.x)
            bbox[BOX.BOXRIGHT] = max(v1.x, v2.x)
            front = self.sides[s0].sector if s0 != -1 else None
            back = self.sides[s1].sector if s1 != -1 else None
            self.lines.append(
                Line(
                    v1=v1,
                    v2=v2,
                    dx=dx,
                    dy=dy,
                    flags=flags,
                    special=special,
                    tag=tag,
                    sidenum=[s0, s1],
                    bbox=bbox,
                    slopetype=slope,
                    frontsector=front,
                    backsector=back,
                )
            )

    def _load_subsectors(self, data: bytes) -> None:
        n = len(data) // _S_SUBSECTOR.size
        self.subsectors = [
            Subsector(numlines=num, firstline=first)
            for num, first in _S_SUBSECTOR.iter_unpack(data[: n * _S_SUBSECTOR.size])
        ]

    def _load_nodes(self, data: bytes) -> None:
        rec_size = _S_NODE_HDR.size + 2 * _S_NODE_BBOX.size + _S_NODE_CHILD.size
        n = len(data) // rec_size
        self.nodes = []
        off = 0
        for _ in range(n):
            x, y, dx, dy = _S_NODE_HDR.unpack_from(data, off)
            off += _S_NODE_HDR.size
            bbox: list[list[int]] = []
            for _ in range(2):
                top, bottom, left, right = _S_NODE_BBOX.unpack_from(data, off)
                off += _S_NODE_BBOX.size
                # File order is top, bottom, left, right and the BOX.*
                # indices are BOXTOP=0, BOXBOTTOM=1, BOXLEFT=2, BOXRIGHT=3,
                # so they already match the read order.
                bbox.append(
                    [
                        top << FRACBITS,
                        bottom << FRACBITS,
                        left << FRACBITS,
                        right << FRACBITS,
                    ]
                )
            c0, c1 = _S_NODE_CHILD.unpack_from(data, off)
            off += _S_NODE_CHILD.size
            self.nodes.append(
                Node(
                    x=x << FRACBITS,
                    y=y << FRACBITS,
                    dx=dx << FRACBITS,
                    dy=dy << FRACBITS,
                    bbox=bbox,
                    children=[c0, c1],
                )
            )

    def _load_segs(self, data: bytes) -> None:
        n = len(data) // _S_SEG.size
        self.segs = []
        for v1i, v2i, angle, linedef, side, offset in _S_SEG.iter_unpack(
            data[: n * _S_SEG.size]
        ):
            ldef = self.lines[linedef]
            sdef = self.sides[ldef.sidenum[side]]
            front = sdef.sector
            if ldef.flags & ML_TWOSIDED:
                back = self.sides[ldef.sidenum[side ^ 1]].sector
            else:
                back = None
            self.segs.append(
                Seg(
                    v1=self.vertexes[v1i],
                    v2=self.vertexes[v2i],
                    angle=(angle << 16) & 0xFFFFFFFF,
                    offset=offset << 16,
                    linedef=ldef,
                    sidenum=side,
                    sidedef=sdef,
                    frontsector=front,
                    backsector=back,
                )
            )

    def _load_blockmap(self, data: bytes) -> None:
        count = len(data) // 2
        words = list(struct.unpack(f"<{count}h", data[: count * 2])) if count else []
        if len(words) < 4:
            self.blockmap = Blockmap()
            return
        self.blockmap = Blockmap(
            orgx=words[0] << FRACBITS,
            orgy=words[1] << FRACBITS,
            width=words[2],
            height=words[3],
            lists=words[4:],
        )

    def _load_things(self, data: bytes) -> None:
        n = len(data) // _S_THING.size
        self.things = [
            MapThing(x=x, y=y, angle=angle, type=typ, options=opts)
            for x, y, angle, typ, opts in _S_THING.iter_unpack(
                data[: n * _S_THING.size]
            )
        ]

    # -- P_GroupLines --

    def _group_lines(self) -> None:
        # 1. owning sector of each subsector, from its first seg.
        for ss in self.subsectors:
            ss.sector = self.segs[ss.firstline].sidedef.sector
        # 2. count lines per sector (two-sided lines count twice).
        for li in self.lines:
            assert li.frontsector is not None
            li.frontsector.linecount += 1
            if li.backsector is not None and li.backsector is not li.frontsector:
                li.backsector.linecount += 1
        # 3. line tables, bboxes, soundorg and blockbox per sector.
        bm = self.blockmap
        for sector in self.sectors:
            sector.lines = [
                li
                for li in self.lines
                if li.frontsector is sector or li.backsector is sector
            ]
            assert len(sector.lines) == sector.linecount, "P_GroupLines: miscounted"
            top = bottom = left = right = None
            for li in sector.lines:
                for v in (li.v1, li.v2):
                    assert v is not None
                    top = v.y if top is None else max(top, v.y)
                    bottom = v.y if bottom is None else min(bottom, v.y)
                    left = v.x if left is None else min(left, v.x)
                    right = v.x if right is None else max(right, v.x)
            if top is None:  # sector without lines: should not happen
                continue
            assert bottom is not None and left is not None and right is not None
            sector.soundorg = ((left + right) // 2, (bottom + top) // 2)

            def clamp_block(v: int, lo: int, hi: int) -> int:
                return max(lo, min(hi, v))

            sector.blockbox[BOX.BOXTOP] = clamp_block(
                (top - bm.orgy + MAXRADIUS) >> MAPBLOCKSHIFT, 0, bm.height - 1
            )
            sector.blockbox[BOX.BOXBOTTOM] = clamp_block(
                (bottom - bm.orgy - MAXRADIUS) >> MAPBLOCKSHIFT, 0, bm.height - 1
            )
            sector.blockbox[BOX.BOXRIGHT] = clamp_block(
                (right - bm.orgx + MAXRADIUS) >> MAPBLOCKSHIFT, 0, bm.width - 1
            )
            sector.blockbox[BOX.BOXLEFT] = clamp_block(
                (left - bm.orgx - MAXRADIUS) >> MAPBLOCKSHIFT, 0, bm.width - 1
            )

    # -- summary --

    def summary(self) -> str:
        return (
            f"Map {self.marker}: "
            f"{len(self.vertexes)} vertexes, {len(self.lines)} lines, "
            f"{len(self.sides)} sides, {len(self.sectors)} sectors, "
            f"{len(self.segs)} segs, {len(self.subsectors)} subsectors, "
            f"{len(self.nodes)} nodes, {len(self.things)} things, "
            f"blockmap {self.blockmap.width}x{self.blockmap.height}, "
            f"reject {len(self.reject)} bytes"
        )
