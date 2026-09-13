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
v(z) = texbase + (z - viewz) * focal/depth, with texbase the static
part of the software texturemid (T_static + rowoffset, viewz rides a
per-frame uniform). World units are map units (fixed >> 16 as float);
1 texel == 1 map unit in both axes, like the software column drawer.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from pydoom.mapdata import (
    ML_DONTPEGBOTTOM,
    ML_DONTPEGTOP,
    Map,
)
from pydoom.renderer import LIGHTLEVELS, LIGHTSEGSHIFT
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
    light: int = 0  # base lightnum 0..15 (sector + orient tweak)


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
        light = np.zeros((n * 4,), dtype=np.float32)
        index = np.zeros((n * 6,), dtype=np.uint32)
        for q, quad in enumerate(self.quads):
            b = q * 4
            pos[b + 0] = (quad.x1, quad.y1, quad.z_bottom)
            pos[b + 1] = (quad.x2, quad.y2, quad.z_bottom)
            pos[b + 2] = (quad.x2, quad.y2, quad.z_top)
            pos[b + 3] = (quad.x1, quad.y1, quad.z_top)
            u[b:b + 4] = (quad.u1, quad.u2, quad.u2, quad.u1)
            texbase[b:b + 4] = quad.texbase
            light[b:b + 4] = quad.light
            ib = q * 6
            index[ib:ib + 6] = (b, b + 1, b + 2, b, b + 2, b + 3)
        return {"positions": pos, "u": u, "texbase": texbase,
                "light": light, "index": index}


def _orient_light(seg, front_light: int) -> int:
    """scalelight row base: sector lightlevel plus the vanilla
    horizontal-dark / vertical-bright tweak (static part only;
    extralight/fullbright ride per-frame uniforms in Phase 2)."""
    lightnum = front_light >> LIGHTSEGSHIFT
    if seg.v1.y == seg.v2.y:
        lightnum -= 1
    elif seg.v1.x == seg.v2.x:
        lightnum += 1
    return min(max(lightnum, 0), LIGHTLEVELS - 1)


def _emit(out: StaticGeometry, si: int, tier: str, texnum: int,
          x1: float, y1: float, x2: float, y2: float,
          zb: int, zt: int, u1: float, length: float,
          static: int, rowoffset: int, light: int) -> None:
    # NOTE: fixed-point in, floats out; degenerate spans (closed-door
    # masked) are skipped, the software clips those to nothing anyway.
    if texnum and zt > zb:
        out.quads.append(WallQuad(
            seg=si, tier=tier, texnum=texnum,
            x1=x1, y1=y1, x2=x2, y2=y2,
            z_bottom=zb / 65536.0, z_top=zt / 65536.0,
            u1=u1, u2=u1 + length,
            texbase=(static + rowoffset) / 65536.0,
            light=light))


def build_walls(game_map: Map, texman: TextureManager,
                skyflat: int) -> StaticGeometry:
    """Wall quads for every seg, tier rules verbatim from
    _store_wall_range (fixed-point comparisons, float output)."""
    out = StaticGeometry()
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
        light = _orient_light(seg, front.lightlevel)

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
                      static, side.rowoffset, light)
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
                  static, side.rowoffset, light)
        if back.floorheight > front.floorheight and side.bottomtexture:
            if line.flags & ML_DONTPEGBOTTOM:
                # NOTE: vanilla quirk kept verbatim: unpegged-bottom
                # bottoms anchor at the FRONT CEILING, not the floor.
                static = front.ceilingheight
            else:
                static = back.floorheight
            _emit(out, si, "bottom", side.bottomtexture, x1, y1, x2, y2,
                  front.floorheight, back.floorheight, u1, length,
                  static, side.rowoffset, light)
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
                  length, static, side.rowoffset, light)
    return out


__all__ = ["TIERS", "StaticGeometry", "WallQuad", "build_walls"]
