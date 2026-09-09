"""Tests for angles.py against r_main.c semantics."""

import math

from pydoom.angles import (
    point_on_side,
    point_on_seg_side,
    point_to_angle,
    point_to_angle2,
)
from pydoom.fixed import ANG90, ANG180, ANG270, FRACUNIT
from pydoom.mapdata import Line, Node, Seg, Vertex


def test_cardinal_directions():
    # Vanilla quirks preserved: due north/west are ANGxx-1, not ANGxx.
    assert point_to_angle(FRACUNIT, 0) == 0  # east
    assert point_to_angle(0, FRACUNIT) == ANG90 - 1  # north
    assert point_to_angle(-FRACUNIT, 0) == ANG180 - 1  # west
    assert point_to_angle(0, -FRACUNIT) == ANG270  # south
    assert point_to_angle(0, 0) == 0


def test_matches_atan2_on_sweep():
    # tantoangle granularity is ~0.02 degrees; allow one fine step.
    tol = 0x100000000 / 8192 * 2
    for deg in range(0, 360, 7):
        r = math.radians(deg)
        x = int(math.cos(r) * 1000 * FRACUNIT)
        y = int(math.sin(r) * 1000 * FRACUNIT)
        got = point_to_angle(x, y)
        want = int((deg / 360) * 0x100000000) & 0xFFFFFFFF
        assert abs((got - want + 2**31) % 2**32 - 2**31) < tol, deg


def test_point_to_angle2():
    assert point_to_angle2(0, 0, FRACUNIT, 0) == 0
    assert point_to_angle2(FRACUNIT, 0, 0, 0) == ANG180 - 1


def test_point_on_side_axis_aligned():
    # Vertical partition x=0 facing north (dy>0): west is side 1.
    node = Node(x=0, y=0, dx=0, dy=FRACUNIT)
    assert point_on_side(-FRACUNIT, 0, node) == 1
    assert point_on_side(FRACUNIT, 0, node) == 0
    # Horizontal partition y=0 facing east (dx>0): front is the south side.
    node = Node(x=0, y=0, dx=FRACUNIT, dy=0)
    assert point_on_side(0, -FRACUNIT, node) == 0
    assert point_on_side(0, FRACUNIT, node) == 1


def test_point_on_side_diagonal_agrees_with_cross_product():
    # Partition along y=x; above the line (north-west) is the back side.
    node = Node(x=0, y=0, dx=FRACUNIT, dy=FRACUNIT)
    assert point_on_side(-FRACUNIT, FRACUNIT, node) == 1
    assert point_on_side(FRACUNIT, -FRACUNIT, node) == 0
    # Far points exercise the sign-bit fast path, near ones the FixedMul.
    big = 30000 << 16
    assert point_on_side(-big, big, node) == 1
    assert point_on_side(big, -big, node) == 0


def test_point_on_seg_side():
    v1 = Vertex(x=0, y=0)
    v2 = Vertex(x=FRACUNIT, y=0)
    seg = Seg(v1=v1, v2=v2)
    assert point_on_seg_side(0, FRACUNIT, seg) == 1
    assert point_on_seg_side(0, -FRACUNIT, seg) == 0
    line = Line(v1=v1, v2=v2)
    seg.linedef = line
