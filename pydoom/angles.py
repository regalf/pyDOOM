"""Angle math from the renderer (r_main.c, r_bsp.c).

Ports R_PointToAngle, R_PointToAngle2, R_PointOnSide and R_PointOnSegSide.

Notes on fidelity:

* The C code returns ``angle_t`` (unsigned 32-bit) and relies on unsigned
  wraparound (e.g. ``-tantoangle[...]``). Every result here is masked with
  ``& 0xFFFFFFFF`` to match.
* ``SlopeDiv`` receives non-negative values after the octant flips, so the
  C unsigned conversion is a no-op; ``tables.slope_div`` masks anyway.
* The sign-bit fast path in R_PointOnSide/R_PointOnSegSide operates on
  32-bit two's complement words. Python ints are unbounded, so each
  operand is masked to 32 bits before xoring.
* R_PointToAngle2 assigned to the ``viewx``/``viewy`` globals and then
  called R_PointToAngle. Here the viewpoint is an explicit parameter
  (no renderer globals exist yet).
"""

from __future__ import annotations

from pydoom import tables
from pydoom.fixed import ANG90, ANG180, ANG270, FRACBITS, fixed_mul
from pydoom.mapdata import Node, Seg

__all__ = [
    "point_to_angle",
    "point_to_angle2",
    "point_on_side",
    "point_on_seg_side",
    "thing_degrees_to_bam",
]

_U32 = 0xFFFFFFFF
_SIGNBIT = 0x80000000


def point_to_angle(x: int, y: int, viewx: int = 0, viewy: int = 0) -> int:
    """Global BAM angle of point (x, y) as seen from (viewx, viewy)."""
    x -= viewx
    y -= viewy

    if x == 0 and y == 0:
        return 0

    if x >= 0:
        if y >= 0:
            if x > y:
                # octant 0
                return tables.tantoangle[tables.slope_div(y, x)]
            # octant 1
            return (ANG90 - 1 - tables.tantoangle[tables.slope_div(x, y)]) & _U32
        y = -y
        if x > y:
            # octant 8
            return (-tables.tantoangle[tables.slope_div(y, x)]) & _U32
        # octant 7
        return (ANG270 + tables.tantoangle[tables.slope_div(x, y)]) & _U32
    x = -x
    if y >= 0:
        if x > y:
            # octant 3
            return (ANG180 - 1 - tables.tantoangle[tables.slope_div(y, x)]) & _U32
        # octant 2
        return (ANG90 + tables.tantoangle[tables.slope_div(x, y)]) & _U32
    y = -y
    if x > y:
        # octant 4
        return (ANG180 + tables.tantoangle[tables.slope_div(y, x)]) & _U32
    # octant 5
    return (ANG270 - 1 - tables.tantoangle[tables.slope_div(x, y)]) & _U32


def point_to_angle2(x1: int, y1: int, x2: int, y2: int) -> int:
    """BAM angle of (x2, y2) as seen from (x1, y1)."""
    return point_to_angle(x2, y2, x1, y1)


def thing_degrees_to_bam(degrees: int) -> int:
    """Map THINGS angle (0=east, 90=north, ...) to a BAM angle_t."""
    return ((degrees % 360) * 0x100000000) // 360


def point_on_side(x: int, y: int, node: Node) -> int:
    """Side of a BSP partition line a point is on: 0 (front) or 1 (back)."""
    if node.dx == 0:
        if x <= node.x:
            return int(node.dy > 0)
        return int(node.dy < 0)
    if node.dy == 0:
        if y <= node.y:
            return int(node.dx < 0)
        return int(node.dx > 0)

    dx = x - node.x
    dy = y - node.y

    # Sign-bit fast path on 32-bit two's complement words.
    if (
        (node.dy & _U32) ^ (node.dx & _U32) ^ (dx & _U32) ^ (dy & _U32)
    ) & _SIGNBIT:
        if ((node.dy & _U32) ^ (dx & _U32)) & _SIGNBIT:
            # (left is negative)
            return 1
        return 0

    left = fixed_mul(node.dy >> FRACBITS, dx)
    right = fixed_mul(dy, node.dx >> FRACBITS)

    if right < left:
        # front side
        return 0
    # back side
    return 1


def point_on_seg_side(x: int, y: int, seg: Seg) -> int:
    """Side of a seg's line a point is on: 0 (front) or 1 (back)."""
    assert seg.v1 is not None and seg.v2 is not None
    lx = seg.v1.x
    ly = seg.v1.y
    ldx = seg.v2.x - lx
    ldy = seg.v2.y - ly

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

    if ((ldy & _U32) ^ (ldx & _U32) ^ (dx & _U32) ^ (dy & _U32)) & _SIGNBIT:
        if ((ldy & _U32) ^ (dx & _U32)) & _SIGNBIT:
            # (left is negative)
            return 1
        return 0

    left = fixed_mul(ldy >> FRACBITS, dx)
    right = fixed_mul(dy, ldx >> FRACBITS)

    if right < left:
        # front side
        return 0
    # back side
    return 1
