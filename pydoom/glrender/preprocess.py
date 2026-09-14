"""Static wall geometry (milestone H, phase 1, walls slice).

gl_preprocess.c analog for walls: one quad per non-empty seg tier,
mirroring renderer._store_wall_range tier selection exactly
(single-sided mid, two-sided top/bottom, masked mid, DONTPEGTOP/
DONTPEGBOTTOM, outdoor sky hack). Pure CPU + numpy, no GL imports:
the output feeds VBOs later, but the tier math is unit tested here.

Per-vertex mapping contract (what the Phase 2 shader evaluates):
u(P) = texels along the wall from seg v1 + textureoffset + seg.offset
(the software texturecolumn, minus its view-dependent detour through
finetangent: both describe the same projective mapping, so
perspective-correct interpolation of u lands on the same texels).
v(z) = texbase - z, with texbase the static part of the software
texturemid (T_static + rowoffset): wall textures are world-pinned
(the viewz inside texturemid only cancels the projection's viewz,
so V is fully static and perspective interpolation lands exact). World units are map units (fixed >> 16 as float);
1 texel == 1 map unit in both axes, like the software column drawer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from fractions import Fraction

import numpy as np

from pydoom.mapdata import (
    ML_DONTPEGBOTTOM,
    ML_DONTPEGTOP,
    NF_SUBSECTOR,
    Map,
    Sector,
)
from pydoom.textures import TextureManager, texture_height_fixed

TIERS = ("mid", "top", "bottom", "masked")


@dataclass
class WallQuad:
    """One textured wall tier: world rect + texture params (all floats
    in map units/texels, heights absolute world Z)."""

    seg: int  # index into game_map.segs
    tier: str  # one of TIERS
    texnum: int  # texman index (never 0: "-" quads are omitted)
    x1: float = 0.0  # seg v1 world pos
    y1: float = 0.0
    x2: float = 0.0  # seg v2 world pos
    y2: float = 0.0
    z_bottom: float = 0.0  # absolute world Z span
    z_top: float = 0.0
    u1: float = 0.0  # texel u at v1 (offsets included)
    u2: float = 0.0  # texel u at v2
    texbase: float = 0.0  # static part of texturemid (+rowoffset)
    sector: int = 0  # frontsector index (its base lightnum rides the
    # sector-light texture, so flicker/strobe/movers never rebuild)
    tweak: int = 0  # vanilla orient tweak -1/0/+1 (static, folded
    # into the shader row next to the sector base)
    nx: float = 0.0  # front-unit normal (front is RIGHT of v1->v2)
    ny: float = 0.0


@dataclass
class StaticGeometry:
    """Preprocessed walls of one map (planes/sky land in later slices)."""

    quads: list[WallQuad] = field(default_factory=list)

    def to_arrays(self) -> dict:
        """Flat VBO-ready arrays (float32 verts, uint32 indices).

        Vertex order per quad: (v1,bottom) (v2,bottom) (v2,top)
        (v1,top); triangles (0,1,2) (0,2,3). Winding/culling is
        Phase 2 business (starts disabled); the order stays fixed.
        """
        n = len(self.quads)
        pos = np.zeros((n * 4, 3), dtype=np.float32)
        u = np.zeros((n * 4,), dtype=np.float32)
        texbase = np.zeros((n * 4,), dtype=np.float32)
        sector = np.zeros((n * 4,), dtype=np.float32)
        tweak = np.zeros((n * 4,), dtype=np.float32)
        normal = np.zeros((n * 4, 2), dtype=np.float32)
        index = np.zeros((n * 6,), dtype=np.uint32)
        for q, quad in enumerate(self.quads):
            b = q * 4
            pos[b + 0] = (quad.x1, quad.y1, quad.z_bottom)
            pos[b + 1] = (quad.x2, quad.y2, quad.z_bottom)
            pos[b + 2] = (quad.x2, quad.y2, quad.z_top)
            pos[b + 3] = (quad.x1, quad.y1, quad.z_top)
            u[b:b + 4] = (quad.u1, quad.u2, quad.u2, quad.u1)
            texbase[b:b + 4] = quad.texbase
            sector[b:b + 4] = quad.sector
            tweak[b:b + 4] = quad.tweak + 1  # NOTE: 0/1/2, never
            # negative (GLSL int() truncates toward zero)
            normal[b:b + 4] = (quad.nx, quad.ny)
            ib = q * 6
            index[ib:ib + 6] = (b, b + 1, b + 2, b, b + 2, b + 3)
        return {"positions": pos, "u": u, "texbase": texbase,
                "sector": sector, "tweak": tweak, "normal": normal,
                "index": index}


def _orient_tweak(seg) -> int:
    """Vanilla horizontal-dark / vertical-bright tweak (-1/0/+1,
    the static part of _orient_light; the sector base lightnum rides
    the sector-light texture now, so movers never rebuild geometry
    for light changes)."""
    if seg.v1.y == seg.v2.y:
        return -1
    if seg.v1.x == seg.v2.x:
        return 1
    return 0


def _emit(out: StaticGeometry, si: int, tier: str, texnum: int,
          x1: float, y1: float, x2: float, y2: float,
          zb: int, zt: int, u1: float, length: float,
          static: int, rowoffset: int, sector: int, tweak: int,
          nx: float, ny: float) -> None:
    # NOTE: fixed-point in, floats out; degenerate spans (closed-door
    # masked) are skipped, the software clips those to nothing anyway.
    if texnum and zt > zb:
        out.quads.append(WallQuad(
            seg=si, tier=tier, texnum=texnum,
            x1=x1, y1=y1, x2=x2, y2=y2,
            z_bottom=zb / 65536.0, z_top=zt / 65536.0,
            u1=u1, u2=u1 + length,
            texbase=(static + rowoffset) / 65536.0,
            sector=sector, tweak=tweak, nx=nx, ny=ny))


def build_walls(game_map: Map, texman: TextureManager,
                skyflat: int) -> StaticGeometry:
    """Wall quads for every seg, tier rules verbatim from
    _store_wall_range (fixed-point comparisons, float output)."""
    out = StaticGeometry()
    sector_index = {id(s): i for i, s in enumerate(game_map.sectors)}
    for si, seg in enumerate(game_map.segs):
        assert seg.v1 is not None and seg.v2 is not None
        assert seg.sidedef is not None and seg.linedef is not None
        assert seg.frontsector is not None
        side, line, front = seg.sidedef, seg.linedef, seg.frontsector
        back = seg.backsector
        x1 = seg.v1.x / 65536.0
        y1 = seg.v1.y / 65536.0
        x2 = seg.v2.x / 65536.0
        y2 = seg.v2.y / 65536.0
        length = math.hypot(x2 - x1, y2 - y1)
        u1 = (side.textureoffset + seg.offset) / 65536.0
        fsi = sector_index[id(front)]
        tweak = _orient_tweak(seg)
        # NOTE: front-unit normal (front is RIGHT of v1->v2, so the
        # normal is (dy, -dx)/len); degenerate segs get (0, 0) (their
        # quads have zero area and rasterize nothing anyway).
        if length > 1e-9:
            nx, ny = (y2 - y1) / length, (x1 - x2) / length
        else:
            nx, ny = 0.0, 0.0

        if back is None:
            if side.midtexture:
                theight = texture_height_fixed(
                    texman.textures[side.midtexture])
                if line.flags & ML_DONTPEGBOTTOM:
                    static = front.floorheight + theight
                else:
                    static = front.ceilingheight
                _emit(out, si, "mid", side.midtexture, x1, y1, x2, y2,
                      front.floorheight, front.ceilingheight, u1, length,
                      static, side.rowoffset, fsi, tweak, nx, ny)
            continue
        # NOTE: outdoor sky hack (both ceilings sky): worldtop drops
        # to worldhigh, which can only kill the top tier below.
        worldtop = front.ceilingheight
        if (front.ceilingpic == skyflat
                and back.ceilingpic == skyflat):
            worldtop = back.ceilingheight
        if back.ceilingheight < worldtop and side.toptexture:
            theight = texture_height_fixed(
                texman.textures[side.toptexture])
            if line.flags & ML_DONTPEGTOP:
                static = front.ceilingheight
            else:
                static = back.ceilingheight + theight
            _emit(out, si, "top", side.toptexture, x1, y1, x2, y2,
                  back.ceilingheight, front.ceilingheight, u1, length,
                  static, side.rowoffset, fsi, tweak, nx, ny)
        if back.floorheight > front.floorheight and side.bottomtexture:
            if line.flags & ML_DONTPEGBOTTOM:
                # NOTE: vanilla quirk kept verbatim: unpegged-bottom
                # bottoms anchor at the FRONT CEILING, not the floor.
                static = front.ceilingheight
            else:
                static = back.floorheight
            _emit(out, si, "bottom", side.bottomtexture, x1, y1, x2, y2,
                  front.floorheight, back.floorheight, u1, length,
                  static, side.rowoffset, fsi, tweak, nx, ny)
        if side.midtexture:
            # NOTE: masked mid (R_RenderMaskedSegRange rule): z spans
            # the opening; texturemid anchors at the lower ceiling,
            # or the taller floor + texture height when unpegged.
            if line.flags & ML_DONTPEGBOTTOM:
                static = max(front.floorheight,
                             back.floorheight) + texture_height_fixed(
                    texman.textures[side.midtexture])
            else:
                static = min(front.ceilingheight, back.ceilingheight)
            _emit(out, si, "masked", side.midtexture, x1, y1, x2, y2,
                  max(front.floorheight, back.floorheight),
                  min(front.ceilingheight, back.ceilingheight), u1,
                  length, static, side.rowoffset, fsi, tweak, nx, ny)
    return out


__all__ = [
    "SURFACES",
    "TIERS",
    "PlaneGeometry",
    "PlaneTri",
    "StaticGeometry",
    "WallQuad",
    "build_planes",
    "build_walls",
    "emit_planes",
    "leaf_sector_fans",
]


# -- sector floors/ceilings/sky (gl_preprocess plane analog) --

SURFACES = ("floor", "ceiling", "sky")


@dataclass
class PlaneTri:
    """One floor/ceiling/sky triangle (absolute world Z; uv in world
    units, i.e. flat ROWS: the shader mods by 64, matching the
    software (xfrac>>16)&63 / (yfrac>>16)&63 texel selection).

    NOTE: v carries vanilla's mirrored Y (R_MapPlane negates viewy:
    flat row = (-yworld) mod 64). Sky tris tag the ceiling hole the
    Phase 3 sky surface fills (fullbright, u from the view angle).
    """

    sector: int
    surface: str  # one of SURFACES
    flat: int  # flatnum, -1 for sky
    x1: float = 0.0
    y1: float = 0.0
    x2: float = 0.0
    y2: float = 0.0
    x3: float = 0.0
    y3: float = 0.0
    z: float = 0.0
    # NOTE: no light field (planes are lit by their own sector: the
    # shader reads the base lightnum from the sector-light texture
    # via tri.sector, so flicker/strobe never rebuild geometry).


@dataclass
class PlaneGeometry:
    """Triangulated floors/ceilings/sky of one map (non-indexed:
    fans share few verts, dedup buys nothing)."""

    tris: list[PlaneTri] = field(default_factory=list)

    def to_arrays(self) -> dict:
        n = len(self.tris)
        pos = np.zeros((n * 3, 3), dtype=np.float32)
        uv = np.zeros((n * 3, 2), dtype=np.float32)
        flat = np.zeros((n * 3,), dtype=np.int32)
        sector = np.zeros((n * 3,), dtype=np.float32)
        for t, tri in enumerate(self.tris):
            b = t * 3
            pos[b + 0] = (tri.x1, tri.y1, tri.z)
            pos[b + 1] = (tri.x2, tri.y2, tri.z)
            pos[b + 2] = (tri.x3, tri.y3, tri.z)
            uv[b + 0] = (tri.x1, -tri.y1)
            uv[b + 1] = (tri.x2, -tri.y2)
            uv[b + 2] = (tri.x3, -tri.y3)
            flat[b:b + 3] = tri.flat
            sector[b:b + 3] = tri.sector
        return {"positions": pos, "uv": uv, "flat": flat,
                "sector": sector}


def _leaf_polys(game_map: Map) -> dict:
    """BSP-leaf convex polygons (exact Fractions).

    Starts from the map vertex bbox and clips down the node tree;
    children[0] keeps the RIGHT side of the partition direction
    (front, matching point_on_side up to on-line points, which land
    in BOTH children so shared edges stay watertight). Leaves tile
    the bbox exactly (asserted): this is the engine's own space
    partition, so hacky sectors (E3M8 overlaps, stub-wall mouths,
    pillars, islands) need no special cases at all.
    """
    if not game_map.vertexes or not game_map.nodes:
        return {}
    xs = [v.x for v in game_map.vertexes]
    ys = [v.y for v in game_map.vertexes]
    lo_x, hi_x, lo_y, hi_y = min(xs), max(xs), min(ys), max(ys)
    bbox = [(Fraction(lo_x), Fraction(lo_y)),
            (Fraction(hi_x), Fraction(lo_y)),
            (Fraction(hi_x), Fraction(hi_y)),
            (Fraction(lo_x), Fraction(hi_y))]
    out: dict = {}

    def clip(poly: list, bx: int, by: int,
             dx: int, dy: int) -> tuple:
        """Sutherland-Hodgman both sides (on-line vertices join both
        children: shared leaf edges stay bit-identical)."""
        front, back = [], []
        n = len(poly)
        for i in range(n):
            cx, cy = poly[i]
            nx, ny = poly[(i + 1) % n]
            sc = dx * (cy - by) - dy * (cx - bx)
            sn = dx * (ny - by) - dy * (nx - bx)
            if sc <= 0:
                front.append((cx, cy))
            if sc >= 0:
                back.append((cx, cy))
            if sc * sn < 0:
                t = Fraction(sc, sc - sn)
                ix, iy = cx + (nx - cx) * t, cy + (ny - cy) * t
                front.append((ix, iy))
                back.append((ix, iy))
        return front, back

    def rec(idx: int, poly: list) -> None:
        if idx & NF_SUBSECTOR:
            out[idx & ~NF_SUBSECTOR] = poly
            return
        node = game_map.nodes[idx]
        front, back = clip(poly, node.x, node.y, node.dx, node.dy)
        rec(node.children[0], front)
        rec(node.children[1], back)

    rec(len(game_map.nodes) - 1, bbox)
    # NOTE: leaves must tile the bbox exactly (no gaps/overlaps in
    # the walk); area2-style unsigned sum on both sides.
    total = Fraction(0)
    for poly in out.values():
        total += abs(sum((b[0] - a[0]) * (b[1] + a[1])
                         for a, b in zip(poly, poly[1:] + poly[:1])))
    assert total == Fraction(hi_x - lo_x) * (hi_y - lo_y) * 2, \
        (total, lo_x, hi_x, lo_y, hi_y)
    return out


def _area2(loop: list) -> int:
    """Twice the signed area (exact integer math, y-up: positive is
    clockwise, i.e. our interior-right outer loops; holes negative).

    NOTE: this is the negated shoelace (sum (x2-x1)(y2+y1)), so the
    ear clipper below re-negates when comparing against cross()."""
    return sum((x2 - x1) * (y2 + y1)
               for (x1, y1), (x2, y2)
               in zip(loop, loop[1:] + loop[:1]))


def _surface_tris(si: int, sector: Sector, skyflat: int,
                   p1: tuple, p2: tuple, p3: tuple) -> list:
    """Floor tri plus ceiling (or sky-tagged) tri for one triangle
    (live heights/pics: the caller re-emits from cached fans on
    sector moves; light rides the sector-light texture)."""
    fl = [(p[0] / 65536.0, p[1] / 65536.0) for p in (p1, p2, p3)]
    out = [PlaneTri(sector=si, surface="floor", flat=sector.floorpic,
                    x1=fl[0][0], y1=fl[0][1], x2=fl[1][0],
                    y2=fl[1][1], x3=fl[2][0], y3=fl[2][1],
                    z=sector.floorheight / 65536.0)]
    if sector.ceilingpic == skyflat or sector.floorpic == skyflat:
        # NOTE: vanilla draws any sky visplane (floor or ceiling)
        # through the sky column drawer, fullbright.
        out.append(PlaneTri(sector=si, surface="sky", flat=-1,
                            x1=fl[0][0], y1=fl[0][1], x2=fl[1][0],
                            y2=fl[1][1], x3=fl[2][0], y3=fl[2][1],
                            z=sector.ceilingheight / 65536.0))
    else:
        out.append(PlaneTri(sector=si, surface="ceiling",
                            flat=sector.ceilingpic,
                            x1=fl[0][0], y1=fl[0][1], x2=fl[1][0],
                            y2=fl[1][1], x3=fl[2][0], y3=fl[2][1],
                            z=sector.ceilingheight / 65536.0))
    return out


def leaf_sector_fans(game_map: Map) -> list:
    """Cached plane topology (BSP-only, live-state-free).

    [(sector_idx, fan tris as fixed-point triples)]: the Fraction
    clipping runs once per map here; per-frame refresh re-emits
    floats via emit_planes (no Fractions, no leaf walk). Fan order
    matches build_planes exactly (same leaf walk, same skips)."""
    sector_index = {id(s): i for i, s in enumerate(game_map.sectors)}
    out = []
    for leaf, poly in _leaf_polys(game_map).items():
        if len(poly) < 3:
            continue  # NOTE: degenerate sliver leaf, no pixels
        sector = game_map.subsectors[leaf].sector
        assert sector is not None
        si = sector_index[id(sector)]
        p0 = poly[0]
        fans = []
        for i in range(1, len(poly) - 1):
            a, b, c = p0, poly[i], poly[i + 1]
            if (b[0] - a[0]) * (c[1] - a[1]) == \
                    (b[1] - a[1]) * (c[0] - a[0]):
                continue  # NOTE: collinear fan tri, no pixels
            fans.append((a, b, c))
        if fans:
            out.append((si, fans))
    return out


def emit_planes(fans: list, game_map: Map,
                skyflat: int) -> PlaneGeometry:
    """Float emission from cached fans + LIVE sector state.

    Bit-identical to build_planes on an unmutated map (same order,
    same floats); sector movers (doors/plats/donuts) re-emit through
    here per frame instead of re-clipping the BSP."""
    out = PlaneGeometry()
    sectors = game_map.sectors
    for si, tris in fans:
        sector = sectors[si]
        for a, b, c in tris:
            out.tris.extend(_surface_tris(si, sector, skyflat,
                                          a, b, c))
    return out


def build_planes(game_map: Map, skyflat: int) -> PlaneGeometry:
    """Floor/ceiling/sky triangles for every sector: fan each BSP
    leaf polygon (convex by construction, collinear fan tris skipped
    exactly) and tag it with its subsector's sector.

    Leaves tile the map, so pillars, islands, disjoint parts and
    overlapping oddities all land correctly with no loop walking,
    no winding rules and no gap heuristics."""
    return emit_planes(leaf_sector_fans(game_map), game_map, skyflat)
