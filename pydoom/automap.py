"""Automap overlay (port of am_map.c, linuxdoom-1.10).

Faithful port of the automap *logic*: fixed-point map<->framebuffer
transforms, Cohen-Sutherland clipping (AM_clipMline), zoom/pan/follow
tics, wall color rules, grid, player arrow and thing triangles.

Pragmatic simplifications (documented, never silent):

* The frame buffer is a pygame Surface instead of the VGA linear
  buffer, and lines are drawn with pygame.draw.line instead of the
  hand-rolled Bresenham (AM_drawFline). Clipping is still the exact
  C algorithm, so the visible output matches.
* No game state exists yet (no players, powers, netgame, cheats), so:
  the "player" is the player-1 start from the map's THINGS lump,
  the computer-map branch (pw_allmap) is skipped, and the IDDT cheat
  levels are driven directly by ``cycle_cheat()``.
* Markers (AM_addMark) are drawn as small square outlines instead of
  the AMMNUM0-9 patches, which need the sprite drawer.
* ``ML_MAPPED`` is never set on lines (nothing explores the map), so
  with ``cheating == 0`` only the player arrow and crosshair show,
  exactly like a fresh vanilla level. The viewer defaults to 1.
"""

from __future__ import annotations

import pygame

from pydoom import tables
from pydoom.angles import thing_degrees_to_bam  # re-exported for compatibility
from pydoom.fixed import FRACBITS, FRACUNIT, MAXINT, fixed_div, fixed_mul
from pydoom.mapdata import (
    ML_DONTDRAW,
    ML_MAPPED,
    ML_SECRET,
    Map,
)
from pydoom.palette import load_playpal

__all__ = [
    "PLAYERRADIUS",
    "MAPBLOCKUNITS",
    "Automap",
    "thing_degrees_to_bam",
]

# Palette index ranges (am_map.c color defines).
WALLCOLORS = 256 - 5 * 16  # REDS
WALLRANGE = 16
TSWALLCOLORS = 6 * 16  # GRAYS
FDWALLCOLORS = 4 * 16  # BROWNS
CDWALLCOLORS = 256 - 32 + 7  # YELLOWS
THINGCOLORS = 7 * 16  # GREENS
SECRETWALLCOLORS = WALLCOLORS
GRIDCOLORS = 6 * 16 + 16 // 2  # GRAYS + GRAYSRANGE/2
XHAIRCOLORS = 6 * 16  # GRAYS
WHITE = 256 - 47
BLACK = 0
BACKGROUND = BLACK

AM_NUMMARKPOINTS = 10

INITSCALEMTOF = int(0.2 * FRACUNIT)
F_PANINC = 4  # fb pixels panned per tic
M_ZOOMIN = int(1.02 * FRACUNIT)
M_ZOOMOUT = int(FRACUNIT / 1.02)

TELEPORT_SPECIAL = 39

PLAYERRADIUS = 16 * FRACUNIT  # p_local.h
MAPBLOCKUNITS = 128  # p_local.h


def _cdiv(a: int, b: int) -> int:
    """C-style integer division (truncates toward zero, unlike //)."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def _cmod(a: int, b: int) -> int:
    """C-style remainder (sign follows the dividend)."""
    return a - _cdiv(a, b) * b


def _player_arrow() -> list[tuple[tuple[int, int], tuple[int, int]]]:
    # #define R ((8*PLAYERRADIUS)/7), integer division like the C code.
    r = (8 * PLAYERRADIUS) // 7
    return [
        ((-r + r // 8, 0), (r, 0)),
        ((r, 0), (r - r // 2, r // 4)),
        ((r, 0), (r - r // 2, -r // 4)),
        ((-r + r // 8, 0), (-r - r // 8, r // 4)),
        ((-r + r // 8, 0), (-r - r // 8, -r // 4)),
        ((-r + 3 * r // 8, 0), (-r + r // 8, r // 4)),
        ((-r + 3 * r // 8, 0), (-r + r // 8, -r // 4)),
    ]


def _triangle_guy() -> list[tuple[tuple[int, int], tuple[int, int]]]:
    # #define R (FRACUNIT); C float->int conversion truncates toward zero.
    r = FRACUNIT
    return [
        ((int(-0.867 * r), int(-0.5 * r)), (int(0.867 * r), int(-0.5 * r))),
        ((int(0.867 * r), int(-0.5 * r)), (0, r)),
        ((0, r), (int(-0.867 * r), int(-0.5 * r))),
    ]


def _thin_triangle_guy() -> list[tuple[tuple[int, int], tuple[int, int]]]:
    r = FRACUNIT
    return [
        ((int(-0.5 * r), int(-0.7 * r)), (r, 0)),
        ((r, 0), (int(-0.5 * r), int(0.7 * r))),
        ((int(-0.5 * r), int(0.7 * r)), (int(-0.5 * r), int(-0.7 * r))),
    ]


PLAYER_ARROW = _player_arrow()
TRIANGLE_GUY = _triangle_guy()
THIN_TRIANGLE_GUY = _thin_triangle_guy()


class Automap:
    """Automap state machine (all the former file-static globals)."""

    def __init__(
        self,
        game_map: Map,
        width: int,
        height: int,
        palette: list[tuple[int, int, int]],
        player_thing: int = 1,
    ) -> None:
        self.f_w = width
        self.f_h = height
        self.palette = palette

        self.followplayer = True
        self.grid = False
        self.cheating = 1  # viewer default: show all walls (see docstring)

        self.scale_mtof = INITSCALEMTOF
        self.scale_ftom = fixed_div(FRACUNIT, INITSCALEMTOF)
        self.m_paninc = [0, 0]
        self.mtof_zoommul = FRACUNIT
        self.ftom_zoommul = FRACUNIT
        self.bigstate = False

        self.m_x = self.m_y = self.m_x2 = self.m_y2 = 0
        self.m_w = self.m_h = 0
        self.min_x = self.min_y = self.max_x = self.max_y = 0
        self.max_w = self.max_h = 0
        self.min_scale_mtof = self.max_scale_mtof = 0
        self.old_m = (0, 0, 0, 0)
        self.f_oldloc = (MAXINT, MAXINT)

        self.markpoints: list[tuple[int, int]] = [(-1, -1)] * AM_NUMMARKPOINTS
        self.markpointnum = 0

        # Player stand-in: fixed-point position + BAM angle.
        self.plr_x = 0
        self.plr_y = 0
        self.plr_angle = 0

        self.load_map(game_map, player_thing)

    # -- setup (AM_LevelInit / AM_initVariables / AM_findMinMaxBoundaries) --

    def load_map(self, game_map: Map, player_thing: int = 1) -> None:
        self.map = game_map
        starts = [t for t in game_map.things if t.type == player_thing]
        if not starts:
            raise ValueError(f"map {game_map.marker}: no player start found")
        start = starts[0]
        self.plr_x = start.x << FRACBITS
        self.plr_y = start.y << FRACBITS
        self.plr_angle = thing_degrees_to_bam(start.angle)

        self.clear_marks()
        self._find_min_max_boundaries()
        self.scale_mtof = fixed_div(self.min_scale_mtof, int(0.7 * FRACUNIT))
        if self.scale_mtof > self.max_scale_mtof:
            self.scale_mtof = self.min_scale_mtof
        self.scale_ftom = fixed_div(FRACUNIT, self.scale_mtof)

        self.m_paninc = [0, 0]
        self.ftom_zoommul = FRACUNIT
        self.mtof_zoommul = FRACUNIT
        self.m_w = self._ftom(self.f_w)
        self.m_h = self._ftom(self.f_h)
        self.m_x = self.plr_x - self.m_w // 2
        self.m_y = self.plr_y - self.m_h // 2
        self._change_window_loc()
        self.old_m = (self.m_x, self.m_y, self.m_w, self.m_h)
        self.f_oldloc = (MAXINT, MAXINT)

    def _find_min_max_boundaries(self) -> None:
        min_x = min_y = MAXINT
        max_x = max_y = -MAXINT
        for v in self.map.vertexes:
            if v.x < min_x:
                min_x = v.x
            elif v.x > max_x:
                max_x = v.x
            if v.y < min_y:
                min_y = v.y
            elif v.y > max_y:
                max_y = v.y
        self.min_x, self.min_y, self.max_x, self.max_y = min_x, min_y, max_x, max_y
        self.max_w = max_x - min_x
        self.max_h = max_y - min_y
        a = fixed_div(self.f_w << FRACBITS, self.max_w)
        b = fixed_div(self.f_h << FRACBITS, self.max_h)
        self.min_scale_mtof = a if a < b else b
        self.max_scale_mtof = fixed_div(
            self.f_h << FRACBITS, 2 * PLAYERRADIUS
        )

    # -- coordinate transforms (FTOM / MTOF / CXMTOF / CYMTOF) --

    def _ftom(self, x: int) -> int:
        return fixed_mul(x << 16, self.scale_ftom)

    def _mtof(self, x: int) -> int:
        return fixed_mul(x, self.scale_mtof) >> 16

    def _cxmtof(self, x: int) -> int:
        return 0 + self._mtof(x - self.m_x)

    def _cymtof(self, y: int) -> int:
        # f_x/f_y are always 0 here (fullscreen window), like f_x=f_y=0
        # after AM_LevelInit.
        return 0 + (self.f_h - self._mtof(y - self.m_y))

    # -- input state (AM_Responder, cut down to viewer needs) --

    def key_down_pan(self, dx: int, dy: int) -> None:
        if not self.followplayer:
            self.m_paninc[0] = self._ftom(F_PANINC) * dx
            self.m_paninc[1] = self._ftom(F_PANINC) * dy

    def key_up_pan(self) -> None:
        self.m_paninc = [0, 0]

    def zoom_hold(self, zoom_in: bool) -> None:
        if zoom_in:
            self.mtof_zoommul = M_ZOOMIN
            self.ftom_zoommul = M_ZOOMOUT
        else:
            self.mtof_zoommul = M_ZOOMOUT
            self.ftom_zoommul = M_ZOOMIN

    def zoom_release(self) -> None:
        self.mtof_zoommul = FRACUNIT
        self.ftom_zoommul = FRACUNIT

    def toggle_big(self) -> None:
        self.bigstate = not self.bigstate
        if self.bigstate:
            self._save_scale_and_loc()
            self._min_out_window_scale()
        else:
            self._restore_scale_and_loc()

    def toggle_follow(self) -> None:
        self.followplayer = not self.followplayer
        self.f_oldloc = (MAXINT, MAXINT)

    def toggle_grid(self) -> None:
        self.grid = not self.grid

    def cycle_cheat(self) -> None:
        # Mirrors the IDDT cycle 0 -> 1 -> 2 -> 0.
        self.cheating = (self.cheating + 1) % 3

    def add_mark(self) -> int:
        n = self.markpointnum
        self.markpoints[n] = (self.m_x + self.m_w // 2, self.m_y + self.m_h // 2)
        self.markpointnum = (n + 1) % AM_NUMMARKPOINTS
        return n

    def clear_marks(self) -> None:
        self.markpoints = [(-1, -1)] * AM_NUMMARKPOINTS
        self.markpointnum = 0

    # -- per-tic updates (AM_Ticker and friends) --

    def ticker(self) -> None:
        if self.followplayer:
            self._do_follow_player()
        if self.ftom_zoommul != FRACUNIT:
            self._change_window_scale()
        if self.m_paninc[0] or self.m_paninc[1]:
            self._change_window_loc()

    def _activate_new_scale(self) -> None:
        self.m_x += self.m_w // 2
        self.m_y += self.m_h // 2
        self.m_w = self._ftom(self.f_w)
        self.m_h = self._ftom(self.f_h)
        self.m_x -= self.m_w // 2
        self.m_y -= self.m_h // 2
        self.m_x2 = self.m_x + self.m_w
        self.m_y2 = self.m_y + self.m_h

    def _save_scale_and_loc(self) -> None:
        self.old_m = (self.m_x, self.m_y, self.m_w, self.m_h)

    def _restore_scale_and_loc(self) -> None:
        _, _, self.m_w, self.m_h = self.old_m
        if not self.followplayer:
            self.m_x, self.m_y, _, _ = self.old_m
        else:
            self.m_x = self.plr_x - self.m_w // 2
            self.m_y = self.plr_y - self.m_h // 2
        self.m_x2 = self.m_x + self.m_w
        self.m_y2 = self.m_y + self.m_h
        self.scale_mtof = fixed_div(self.f_w << FRACBITS, self.m_w)
        self.scale_ftom = fixed_div(FRACUNIT, self.scale_mtof)

    def _min_out_window_scale(self) -> None:
        self.scale_mtof = self.min_scale_mtof
        self.scale_ftom = fixed_div(FRACUNIT, self.scale_mtof)
        self._activate_new_scale()

    def _max_out_window_scale(self) -> None:
        self.scale_mtof = self.max_scale_mtof
        self.scale_ftom = fixed_div(FRACUNIT, self.scale_mtof)
        self._activate_new_scale()

    def _change_window_scale(self) -> None:
        self.scale_mtof = fixed_mul(self.scale_mtof, self.mtof_zoommul)
        self.scale_ftom = fixed_div(FRACUNIT, self.scale_mtof)
        if self.scale_mtof < self.min_scale_mtof:
            self._min_out_window_scale()
        elif self.scale_mtof > self.max_scale_mtof:
            self._max_out_window_scale()
        else:
            self._activate_new_scale()

    def _change_window_loc(self) -> None:
        if self.m_paninc[0] or self.m_paninc[1]:
            self.followplayer = False
            self.f_oldloc = (MAXINT, self.f_oldloc[1])
        self.m_x += self.m_paninc[0]
        self.m_y += self.m_paninc[1]
        if self.m_x + self.m_w // 2 > self.max_x:
            self.m_x = self.max_x - self.m_w // 2
        elif self.m_x + self.m_w // 2 < self.min_x:
            self.m_x = self.min_x - self.m_w // 2
        if self.m_y + self.m_h // 2 > self.max_y:
            self.m_y = self.max_y - self.m_h // 2
        elif self.m_y + self.m_h // 2 < self.min_y:
            self.m_y = self.min_y - self.m_h // 2
        self.m_x2 = self.m_x + self.m_w
        self.m_y2 = self.m_y + self.m_h

    def _do_follow_player(self) -> None:
        if self.f_oldloc != (self.plr_x, self.plr_y):
            self.m_x = self._ftom(self._mtof(self.plr_x)) - self.m_w // 2
            self.m_y = self._ftom(self._mtof(self.plr_y)) - self.m_h // 2
            self.m_x2 = self.m_x + self.m_w
            self.m_y2 = self.m_y + self.m_h
            self.f_oldloc = (self.plr_x, self.plr_y)

    # -- clipping (AM_clipMline, Cohen-Sutherland) --

    def clip_mline(
        self, ax: int, ay: int, bx: int, by: int
    ) -> tuple[int, int, int, int] | None:
        """Clip a map-space line to the window; return fb coords or None."""
        LEFT, RIGHT, BOTTOM, TOP = 1, 2, 4, 8
        f_w, f_h = self.f_w, self.f_h

        # Trivial rejects in map coords (mirrors the C fast path, including
        # its quirk of testing y first, then x, with early outs).
        out1 = out2 = 0
        if ay > self.m_y2:
            out1 = TOP
        elif ay < self.m_y:
            out1 = BOTTOM
        if by > self.m_y2:
            out2 = TOP
        elif by < self.m_y:
            out2 = BOTTOM
        if out1 & out2:
            return None
        if ax < self.m_x:
            out1 |= LEFT
        elif ax > self.m_x2:
            out1 |= RIGHT
        if bx < self.m_x:
            out2 |= LEFT
        elif bx > self.m_x2:
            out2 |= RIGHT
        if out1 & out2:
            return None

        fax, fay = self._cxmtof(ax), self._cymtof(ay)
        fbx, fby = self._cxmtof(bx), self._cymtof(by)

        def outcode_fb(mx: int, my: int) -> int:
            oc = 0
            if my < 0:
                oc |= TOP
            elif my >= f_h:
                oc |= BOTTOM
            if mx < 0:
                oc |= LEFT
            elif mx >= f_w:
                oc |= RIGHT
            return oc

        out1 = outcode_fb(fax, fay)
        out2 = outcode_fb(fbx, fby)
        if out1 & out2:
            return None

        a = [fax, fay]
        b = [fbx, fby]
        while out1 | out2:
            outside = out1 if out1 else out2
            if outside & TOP:
                dy = a[1] - b[1]
                dx = b[0] - a[0]
                tmpx = a[0] + _cdiv(dx * a[1], dy)
                tmp = [tmpx, 0]
            elif outside & BOTTOM:
                dy = a[1] - b[1]
                dx = b[0] - a[0]
                tmpx = a[0] + _cdiv(dx * (a[1] - f_h), dy)
                tmp = [tmpx, f_h - 1]
            elif outside & RIGHT:
                dy = b[1] - a[1]
                dx = b[0] - a[0]
                tmpy = a[1] + _cdiv(dy * (f_w - 1 - a[0]), dx)
                tmp = [f_w - 1, tmpy]
            else:  # LEFT
                dy = b[1] - a[1]
                dx = b[0] - a[0]
                tmpy = a[1] + _cdiv(dy * (-a[0]), dx)
                tmp = [0, tmpy]
            if outside == out1:
                a = tmp
                out1 = outcode_fb(a[0], a[1])
            else:
                b = tmp
                out2 = outcode_fb(b[0], b[1])
            if out1 & out2:
                return None
        return (a[0], a[1], b[0], b[1])

    # -- drawing (AM_Drawer and friends) --

    def draw(self, surface: pygame.Surface) -> None:
        surface.fill(self.palette[BACKGROUND])
        if self.grid:
            self._draw_grid(surface, GRIDCOLORS)
        self._draw_walls(surface)
        self._draw_player(surface)
        if self.cheating == 2:
            self._draw_things(surface)
        surface.set_at((self.f_w // 2, self.f_h // 2), self.palette[XHAIRCOLORS])
        self._draw_marks(surface)

    def _draw_mline(
        self,
        surface: pygame.Surface,
        ax: int,
        ay: int,
        bx: int,
        by: int,
        color: int,
    ) -> None:
        clipped = self.clip_mline(ax, ay, bx, by)
        if clipped is not None:
            x1, y1, x2, y2 = clipped
            pygame.draw.line(surface, self.palette[color], (x1, y1), (x2, y2))

    def _draw_grid(self, surface: pygame.Surface, color: int) -> None:
        step = MAPBLOCKUNITS << FRACBITS
        bm = self.map.blockmap
        start = self.m_x
        if _cmod(start - bm.orgx, step):
            start += step - _cmod(start - bm.orgx, step)
        end = self.m_x + self.m_w
        x = start
        while x < end:
            self._draw_mline(surface, x, self.m_y, x, self.m_y + self.m_h, color)
            x += step
        start = self.m_y
        if _cmod(start - bm.orgy, step):
            start += step - _cmod(start - bm.orgy, step)
        end = self.m_y + self.m_h
        y = start
        while y < end:
            self._draw_mline(surface, self.m_x, y, self.m_x + self.m_w, y, color)
            y += step

    def _draw_walls(self, surface: pygame.Surface) -> None:
        for li in self.map.lines:
            if self.cheating or (li.flags & ML_MAPPED):
                if (li.flags & ML_DONTDRAW) and not self.cheating:
                    continue
                assert li.v1 is not None and li.v2 is not None
                ax, ay, bx, by = li.v1.x, li.v1.y, li.v2.x, li.v2.y
                if li.backsector is None:
                    self._draw_mline(surface, ax, ay, bx, by, WALLCOLORS)
                else:
                    assert li.frontsector is not None
                    if li.special == TELEPORT_SPECIAL:
                        self._draw_mline(
                            surface, ax, ay, bx, by, WALLCOLORS + WALLRANGE // 2
                        )
                    elif li.flags & ML_SECRET:
                        if self.cheating:
                            self._draw_mline(
                                surface, ax, ay, bx, by, SECRETWALLCOLORS
                            )
                        else:
                            self._draw_mline(surface, ax, ay, bx, by, WALLCOLORS)
                    elif (
                        li.backsector.floorheight != li.frontsector.floorheight
                    ):
                        self._draw_mline(
                            surface, ax, ay, bx, by, FDWALLCOLORS
                        )
                    elif (
                        li.backsector.ceilingheight
                        != li.frontsector.ceilingheight
                    ):
                        self._draw_mline(
                            surface, ax, ay, bx, by, CDWALLCOLORS
                        )
                    elif self.cheating:
                        self._draw_mline(surface, ax, ay, bx, by, TSWALLCOLORS)
            # NOTE: the pw_allmap (computer map) branch is skipped: there is
            # no player inventory yet.

    @staticmethod
    def _rotate(x: int, y: int, angle: int) -> tuple[int, int]:
        """AM_rotate: 2D rotation with the finesine/finecosine tables."""
        idx = (angle & 0xFFFFFFFF) >> 19  # ANGLETOFINESHIFT
        cosv = tables.finecosine(idx)
        sinv = tables.finesine[idx]
        tmpx = fixed_mul(x, cosv) - fixed_mul(y, sinv)
        newy = fixed_mul(x, sinv) + fixed_mul(y, cosv)
        return tmpx, newy

    def _draw_line_character(
        self,
        surface: pygame.Surface,
        glyph: list[tuple[tuple[int, int], tuple[int, int]]],
        scale: int,
        angle: int,
        color: int,
        x: int,
        y: int,
    ) -> None:
        for (lax, lay), (lbx, lby) in glyph:
            if scale:
                lax = fixed_mul(scale, lax)
                lay = fixed_mul(scale, lay)
            if angle:
                lax, lay = self._rotate(lax, lay, angle)
            lax += x
            lay += y
            if scale:
                lbx = fixed_mul(scale, lbx)
                lby = fixed_mul(scale, lby)
            if angle:
                lbx, lby = self._rotate(lbx, lby, angle)
            lbx += x
            lby += y
            self._draw_mline(surface, lax, lay, lbx, lby, color)

    def _draw_player(self, surface: pygame.Surface) -> None:
        # Single-player path of AM_drawPlayers (no netgame yet).
        self._draw_line_character(
            surface, PLAYER_ARROW, 0, self.plr_angle, WHITE, self.plr_x, self.plr_y
        )

    def _draw_things(self, surface: pygame.Surface) -> None:
        for t in self.map.things:
            self._draw_line_character(
                surface,
                THIN_TRIANGLE_GUY,
                16 << FRACBITS,
                thing_degrees_to_bam(t.angle),
                THINGCOLORS,
                t.x << FRACBITS,
                t.y << FRACBITS,
            )

    def _draw_marks(self, surface: pygame.Surface) -> None:
        # Patch drawing (V_DrawPatch of AMMNUMn) needs the sprite drawer;
        # draw small square outlines instead.
        rgb = self.palette[CDWALLCOLORS]
        for mx, my in self.markpoints:
            if mx == -1:
                continue
            fx = self._cxmtof(mx)
            fy = self._cymtof(my)
            if 0 <= fx < self.f_w - 5 and 0 <= fy < self.f_h - 6:
                pygame.draw.rect(surface, rgb, (fx, fy, 5, 6), 1)


def load_playpal_from_wad(wad) -> list[tuple[int, int, int]]:
    """Load the base PLAYPAL palette from an open WadFile."""
    return load_playpal(wad.read_lump("PLAYPAL"))
