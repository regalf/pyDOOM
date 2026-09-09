"""Tests for fastdraw.py: kernels match the pure-Python reference."""

import numpy as np
import pytest

from pydoom import fastdraw
from pydoom.fastdraw import _py_column, _py_fuzz, _py_span

needs_numba = pytest.mark.skipif(
    not fastdraw.HAVE_NUMBA, reason="numba not installed"
)


def _arrays():
    rng = np.random.default_rng(1234)
    fb = rng.integers(0, 256, size=(200, 320)).astype(np.uint8)
    src = rng.integers(0, 256, size=128).astype(np.uint8)
    cmap = rng.integers(0, 256, size=256).astype(np.uint8)
    flat = rng.integers(0, 256, size=4096).astype(np.uint8)
    return fb, src, cmap, flat


@needs_numba
def test_column_matches_reference():
    fb, src, cmap, _ = _arrays()
    a, b = fb.copy(), fb.copy()
    _py_column(a, 7, 10, 50, -123456, 66535, bytes(src), bytes(cmap), 128)
    fastdraw.draw_column(b, 7, 10, 50, -123456, 66535, src, cmap, 128)
    assert np.array_equal(a, b)


@needs_numba
def test_column_negative_frac_wraps_like_python_mod():
    fb, src, cmap, _ = _arrays()
    a, b = fb.copy(), fb.copy()
    _py_column(a, 3, 0, 199, -(2**40), 2**32, bytes(src), bytes(cmap), 100)
    fastdraw.draw_column(b, 3, 0, 199, -(2**40), 2**32, src, cmap, 100)
    assert np.array_equal(a, b)


@needs_numba
def test_span_matches_reference():
    fb, _, cmap, flat = _arrays()
    a, b = fb.copy(), fb.copy()
    _py_span(a, 100, 5, 300, -2**35, 2**36, 123456, -789012,
             bytes(flat), bytes(cmap))
    fastdraw.draw_span(b, 100, 5, 300, -2**35, 2**36, 123456, -789012,
                       flat, cmap)
    assert np.array_equal(a, b)


@needs_numba
def test_fuzz_matches_reference():
    fb, _, _, _ = _arrays()
    fb[:, :] = np.arange(200 * 320, dtype=np.uint8).reshape(200, 320)
    a, b = fb.copy(), fb.copy()
    cmap6 = np.arange(256, dtype=np.uint8)
    offsets = np.array([1, -1] * 25, dtype=np.int64)
    pa = _py_fuzz(a, 10, 1, 197, bytes(cmap6), [1, -1] * 25, 7)
    pb = fastdraw.draw_fuzz(b, 10, 1, 197, cmap6, offsets, 7)
    assert np.array_equal(a, b)
    assert pa == pb == (7 + 198) % 50
