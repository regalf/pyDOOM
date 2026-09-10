"""Tests for wipe.py: the melt converges on the new frame, in time."""

import numpy as np

from pydoom.wipe import H, W, MeltWipe


def test_melt_converges_to_new():
    old = np.zeros((H, W), dtype=np.uint8)
    new = np.full((H, W), 7, dtype=np.uint8)
    wipe = MeltWipe(seed=1)
    wipe.start(old, new)
    frames = 0
    last = old
    while True:
        cur = wipe.tick()
        if cur is None:
            break
        last = cur
        frames += 1
        assert frames < 500
    assert frames > 5  # NOTE: an actual animation, not a cut
    assert (last == new).all()


def test_melt_reveals_top_down():
    old = np.zeros((H, W), dtype=np.uint8)
    new = np.full((H, W), 7, dtype=np.uint8)
    wipe = MeltWipe(seed=2)
    wipe.start(old, new)
    mid = None
    for _ in range(30):  # NOTE: staggered heads need time to start
        mid = wipe.tick()
    assert mid is not None
    assert (mid[0] == 7).all()  # NOTE: top rows melt first
    assert (mid[-1] == 0).any()  # NOTE: something still hangs below
