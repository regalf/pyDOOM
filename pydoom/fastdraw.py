"""Numba-accelerated pixel kernels with pure-Python fallback.

The renderer calls these for every wall column, floor span, masked post
and fuzz column. Signatures use raw ints and NumPy uint8 arrays so both
implementations are interchangeable. When numba is missing, the fallback
runs (slower, identical pixels).

Fixed-point values always fit in int64 here: frac stays within a few
billions (screen rows x iscale), well below 2**62.
"""

from __future__ import annotations

import numpy as np

__all__ = ["HAVE_NUMBA", "draw_column", "draw_span", "draw_fuzz"]

try:
    import numba

    HAVE_NUMBA = True
except ImportError:  # pragma: no cover - fallback path
    HAVE_NUMBA = False


def _floormod(v: int, m: int) -> int:
    """Python-style floored modulo (kept for the fallback path docs).

    NOTE: numba's % already follows Python floor semantics, so the
    kernels below use plain % — no manual fixup needed.
    """
    r = v % m
    return r + m if r < 0 else r


def _py_column(fb, x, y0, count, frac, iscale, src, cmap, texh):
    for i in range(count + 1):
        texel = src[(frac >> 16) % texh]
        fb[y0 + i, x] = cmap[texel]
        frac += iscale


def _py_span(fb, y, x1, x2, xfrac, yfrac, xstep, ystep, flat, cmap):
    for x in range(x1, x2 + 1):
        spot = ((yfrac >> 10) & 0xFC0) + ((xfrac >> 16) & 63)
        fb[y, x] = cmap[flat[spot]]
        xfrac += xstep
        yfrac += ystep


def _py_fuzz(fb, x, y0, count, cmap6, offsets, pos):
    for i in range(count + 1):
        y = y0 + i
        fb[y, x] = cmap6[fb[y + offsets[pos], x]]
        pos = (pos + 1) % len(offsets)
    return pos


if HAVE_NUMBA:

    @numba.njit(cache=True)
    def _nb_column(fb, x, y0, count, frac, iscale, src, cmap, texh):
        # NOTE: numba >> on int64 is arithmetic and % is floored,
        # exactly like the Python reference above.
        for i in range(count + 1):
            texel = src[(frac >> np.int64(16)) % texh]
            fb[y0 + i, x] = cmap[texel]
            frac += iscale

    @numba.njit(cache=True)
    def _nb_span(fb, y, x1, x2, xfrac, yfrac, xstep, ystep, flat, cmap):
        for x in range(x1, x2 + 1):
            spot = ((yfrac >> np.int64(10)) & np.int64(0xFC0)) + (
                (xfrac >> np.int64(16)) & np.int64(63)
            )
            fb[y, x] = cmap[flat[spot]]
            xfrac += xstep
            yfrac += ystep

    @numba.njit(cache=True)
    def _nb_fuzz(fb, x, y0, count, cmap6, offsets, pos):
        n = len(offsets)
        for i in range(count + 1):
            y = y0 + i
            fb[y, x] = cmap6[fb[y + offsets[pos], x]]
            pos = (pos + 1) % n
        return pos

    draw_column = _nb_column
    draw_span = _nb_span
    draw_fuzz = _nb_fuzz
else:  # pragma: no cover - fallback path
    draw_column = _py_column
    draw_span = _py_span
    draw_fuzz = _py_fuzz
