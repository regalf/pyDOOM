"""Tests for fixed.py against the m_fixed.c semantics."""

import math

import pytest

from pydoom.fixed import (
    FRACUNIT,
    MAXINT,
    MININT,
    FixedDiv,
    FixedDiv2,
    FixedDivError,
    FixedMul,
    fixed_to_float,
    float_to_fixed,
)


def test_mul_identity():
    assert FixedMul(3 * FRACUNIT, 2 * FRACUNIT) == 6 * FRACUNIT
    assert FixedMul(-FRACUNIT, FRACUNIT) == -FRACUNIT
    assert FixedMul(0, 123456) == 0


def test_mul_fraction():
    # 1.5 * 2.25 = 3.375
    assert FixedMul(int(1.5 * FRACUNIT), int(2.25 * FRACUNIT)) == int(
        3.375 * FRACUNIT
    )


def test_mul_wrap_32bit():
    # C truncates to 32 bits: replicate with an explicit mask.
    a = b = 0x7FFFFFFF
    assert FixedMul(a, b) == (((a * b) >> 16) & 0xFFFFFFFF) - (
        0x100000000 if (((a * b) >> 16) & 0x80000000) else 0
    )


def test_div_identity():
    assert FixedDiv(FRACUNIT, FRACUNIT) == FRACUNIT
    assert FixedDiv(6 * FRACUNIT, 2 * FRACUNIT) == 3 * FRACUNIT


def test_div_saturates_like_c():
    # (abs(a)>>14) >= abs(b) -> saturation with the sign of a^b.
    assert FixedDiv(1 << 30, 1) == MAXINT
    assert FixedDiv(-(1 << 30), 1) == MININT
    assert FixedDiv(1 << 30, -1) == MININT


def test_div2_raises_on_zero():
    with pytest.raises(FixedDivError):
        FixedDiv2(FRACUNIT, 0)


def test_div2_truncates_toward_zero_like_c_cast():
    # 10/3 = 3.333... -> truncates, does not round.
    got = FixedDiv2(10 * FRACUNIT, 3 * FRACUNIT)
    assert got == math.trunc(10 / 3 * FRACUNIT)


def test_float_roundtrip():
    assert fixed_to_float(float_to_fixed(1.5)) == pytest.approx(1.5)
