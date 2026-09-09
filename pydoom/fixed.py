"""16.16 fixed-point arithmetic and BAM angles.

Faithful port of ``m_fixed.h`` / ``m_fixed.c`` (linuxdoom-1.10).

Python ints have arbitrary precision, so every operation that truncates
to 32 bits in C is explicitly masked with :func:`_to_signed32` to
replicate the overflow/wrap behavior of the original ``fixed_t``.
"""

from __future__ import annotations

import math

# m_fixed.h
FRACBITS = 16
FRACUNIT = 1 << FRACBITS

# doomtype.h (on little-endian hosts, which is what we care about)
MAXINT = 0x7FFFFFFF
MININT = -0x80000000

# BAM (Binary Angle Measurement) angle system, uint32 wrapping over 2*pi.
# See DOOM_ENGINE_ANALYSIS.md and doomdef.h.
ANG45 = 0x20000000
ANG90 = 0x40000000
ANG180 = 0x80000000
ANG270 = 0xC0000000
ANG_MAX = 0x100000000  # full turn, for explicit wrapping


class FixedDivError(OverflowError):
    """Equivalent of ``I_Error("FixedDiv: divide by zero")``."""


def _to_signed32(v: int) -> int:
    """Truncate a Python int to ``int32_t`` with wrap, like C assignment."""
    v &= 0xFFFFFFFF
    return v - 0x100000000 if v >= 0x80000000 else v


def _to_unsigned32(v: int) -> int:
    return v & 0xFFFFFFFF


def fixed_mul(a: int, b: int) -> int:
    """``FixedMul``: ``((long long)a * b) >> FRACBITS`` truncated to 32 bits."""
    return _to_signed32((a * b) >> FRACBITS)


def fixed_div2(a: int, b: int) -> int:
    """``FixedDiv2``: division via double, errors on overflow/div-by-zero."""
    if b == 0:
        raise FixedDivError("FixedDiv: divide by zero")
    c = float(a) / float(b) * FRACUNIT
    if c >= 2147483648.0 or c < -2147483648.0:
        raise FixedDivError("FixedDiv: divide by zero")
    # The C cast (fixed_t)c truncates toward zero.
    return _to_signed32(math.trunc(c))


def fixed_div(a: int, b: int) -> int:
    """``FixedDiv``: saturating version (returns MIN/MAXINT on overflow)."""
    if (abs(a) >> 14) >= abs(b):
        return MININT if (a ^ b) < 0 else MAXINT
    return fixed_div2(a, b)


# C-style aliases for porting code from the original.
FixedMul = fixed_mul
FixedDiv = fixed_div
FixedDiv2 = fixed_div2


# --- Conversion helpers (not in the C code, handy for Python) ---

def float_to_fixed(x: float) -> int:
    return _to_signed32(math.trunc(x * FRACUNIT))


def fixed_to_float(x: int) -> float:
    return _to_signed32(x) / FRACUNIT


def int_to_fixed(x: int) -> int:
    return _to_signed32(x << FRACBITS)


def fixed_to_int(x: int) -> int:
    # Arithmetic shift: implementation-defined for negatives in C, but gcc
    # does an arithmetic shift, like Python's >> operator.
    return _to_signed32(x) >> FRACBITS


def bam_wrap(angle: int) -> int:
    """Explicitly wrap a BAM angle to uint32."""
    return _to_unsigned32(angle)


def c_div(a: int, b: int) -> int:
    """C-style integer division (truncates toward zero, unlike //)."""
    q = abs(a) // abs(b)
    return -q if (a < 0) != (b < 0) else q


def c_mod(a: int, b: int) -> int:
    """C-style remainder (sign follows the dividend)."""
    return a - c_div(a, b) * b
