"""Sprite billboards (milestone H, phase 3, sprites slice).

CPU feed mirroring renderer._project_sprite culling, rotation and
lighting exactly (same fixed-point math, same tables), but emitting
world-space camera-facing quads instead of screen spans: corners
follow mobjs and the camera per frame, UVs stay static per patch
(u in [0, width] mirrored on flip, v = gzt - z in [height, 0]),
and the colormap value is precomputed per sprite (vanilla grades a
whole sprite by one scalelight entry: lightnum from the sector plus
extralight/visor, indexed by xscale).

MF_SHADOW carriers are EMITTED with fuzz=True (corners identical;
the fuzz program shades them from the backdrop index target instead
of their patch). Pure CPU, no GL imports.

Unlike renderer.project_mobjs there is no 128-sprite cap: every
passing billboard draws depth-tested (like dsda-doom-style GL
ports), so crowded views never lose near monsters. In non-overflow
scenes the emitted set matches the software vissprites exactly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydoom.angles import point_to_angle
from pydoom.fixed import ANG45, FRACBITS, fixed_div, fixed_mul
from pydoom.glrender.light import scalelight_lut
from pydoom.info import FF_FRAMEMASK, FF_FULLBRIGHT, MF_SHADOW
from pydoom.renderer import (
    LIGHTLEVELS,
    LIGHTSCALESHIFT,
    LIGHTSEGSHIFT,
    MAXLIGHTSCALE,
    MINZ,
    SCREENWIDTH,
)

__all__ = ["SpriteBillboard", "SpriteFeed"]

_U32 = 0xFFFFFFFF


@dataclass
class SpriteBillboard:
    """One camera-facing quad (world units; z absolute).

    (left_x, left_y) sits -leftoffset along the view-right axis from
    the origin, (right_x, right_y) at width-leftoffset;
    z_bottom/z_top are gzt - height / gzt. u0/u1 mirror on flip,
    v0/v1 are height/0 rows, lump selects the sprite patch,
    colormap is the precomputed scalelight value (0..31), depth is
    xscale for diagnostics (draw order is depth-tested, unsorted).
    """

    lump: int = 0  # texman sprite-relative patch index
    flip: bool = False
    colormap: int = 0
    fuzz: bool = False  # MF_SHADOW: shade from backdrop, not patch
    left_x: float = 0.0
    left_y: float = 0.0
    right_x: float = 0.0
    right_y: float = 0.0
    z_bottom: float = 0.0
    z_top: float = 0.0
    u0: float = 0.0
    u1: float = 0.0
    v0: float = 0.0
    v1: float = 0.0
    depth: int = 0


@dataclass
class SpriteFeed:
    """Per-frame projector (mirrors Renderer sprite state)."""

    sprites: list = field(default_factory=list)  # init_sprite_defs table
    projection: int = (SCREENWIDTH // 2) << FRACBITS
    viewwidth: int = SCREENWIDTH
    centerxfrac: int = (SCREENWIDTH // 2) << FRACBITS

    def project(self, mobjs, viewx: int, viewy: int,
                angle_bam: int, texman, extra_light: int = 0,
                fullbright: bool = False) -> list:
        """Billboards for visible mobjs (same set/patch/flip/light
        as renderer.project_mobjs + _project_sprite)."""
        from pydoom import tables
        viewcos = tables.finecosine(angle_bam >> 19)
        viewsin = tables.finesine[angle_bam >> 19]
        scalelight = scalelight_lut()  # NOTE: static, one per frame
        out = []
        for mo in mobjs:
            if mo.dead or mo.state == 0:
                continue
            bb = self._one(mo, viewx, viewy, angle_bam, viewcos,
                           viewsin, texman, extra_light, fullbright,
                           scalelight)
            if bb is not None:
                out.append(bb)
        # NOTE: no MAXVISSPRITES cap (unlike the software vissprites):
        # GL ports depth-test every passing billboard, so a crowded
        # view keeps far sprites instead of dropping near monsters.
        return out

    def _one(self, mo, viewx: int, viewy: int, angle_bam: int,
             viewcos: int, viewsin: int, texman, extra_light: int,
             fullbright: bool, scalelight: bytes):
        tr_x = mo.x - viewx
        tr_y = mo.y - viewy
        gxt = fixed_mul(tr_x, viewcos)
        gyt = -fixed_mul(tr_y, viewsin)
        tz = gxt - gyt
        if tz < MINZ:
            return None
        xscale = fixed_div(self.projection, tz)
        gxt = -fixed_mul(tr_x, viewsin)
        gyt = fixed_mul(tr_y, viewcos)
        tx = -(gyt + gxt)
        if abs(tx) > (tz << 2):
            return None
        sprframes = self.sprites[mo.sprite]
        if (mo.frame & FF_FRAMEMASK) >= len(sprframes):
            return None
        sprframe = sprframes[mo.frame & FF_FRAMEMASK]
        if sprframe["rotate"]:
            ang = point_to_angle(mo.x, mo.y, viewx, viewy)
            rot = ((ang - mo.angle + (ANG45 // 2) * 9) & _U32) >> 29
            lump = sprframe["lump"][rot]
            flip = sprframe["flip"][rot]
        else:
            lump = sprframe["lump"][0]
            flip = sprframe["flip"][0]
        patch = texman.get_sprite_patch(lump)
        sprwidth = patch.width << FRACBITS
        tx -= patch.leftoffset << FRACBITS
        x1 = (self.centerxfrac + fixed_mul(tx, xscale)) >> FRACBITS
        if x1 > self.viewwidth:
            return None
        tx += sprwidth
        x2 = ((self.centerxfrac + fixed_mul(tx, xscale))
              >> FRACBITS) - 1
        if x2 < 0:
            return None
        gzt = mo.z + (patch.topoffset << FRACBITS)
        fuzz = bool(mo.flags & MF_SHADOW)
        if fuzz:
            colormap = 0  # NOTE: unused (backdrop shades fuzz)
        elif mo.frame & FF_FULLBRIGHT:
            colormap = 0
        else:
            lightlevel = (mo.sector.lightlevel
                          if mo.sector is not None else 0)
            lightnum = (lightlevel >> LIGHTSEGSHIFT) + extra_light
            if fullbright:
                lightnum = LIGHTLEVELS - 1
            lightnum = min(max(lightnum, 0), LIGHTLEVELS - 1)
            index = xscale >> LIGHTSCALESHIFT
            if index >= MAXLIGHTSCALE:
                index = MAXLIGHTSCALE - 1
            colormap = scalelight[lightnum * MAXLIGHTSCALE + index]
        # NOTE: camera-facing corners (cylindrical billboard: yaw-only
        # camera, so fully-facing and cylindrical coincide).
        rx, ry = viewsin / 65536.0, -viewcos / 65536.0
        loff = patch.leftoffset
        roff = patch.width - patch.leftoffset
        px, py = mo.x / 65536.0, mo.y / 65536.0
        return SpriteBillboard(
            lump=lump, flip=flip, colormap=colormap, fuzz=fuzz,
            left_x=px - rx * loff, left_y=py - ry * loff,
            right_x=px + rx * roff, right_y=py + ry * roff,
            z_bottom=gzt / 65536.0 - patch.height,
            z_top=gzt / 65536.0,
            u0=float(patch.width) if flip else 0.0,
            u1=0.0 if flip else float(patch.width),
            v0=float(patch.height), v1=0.0, depth=xscale)
