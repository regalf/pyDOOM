"""Tests for wipe.py: the melt converges on the new frame, in time."""

import numpy as np

from pydoom.wipe import H, W, MeltWipe


def test_melt_converges_to_new():
    old = np.zeros((H, W), dtype=np.uint8)
    new = np.full((H, W), 7, dtype=np.uint8)
    wipe = MeltWipe()
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
    assert 20 < frames < 80  # NOTE: ~42 tics, about a second at 35Hz
    assert (last == new).all()


def test_melt_reveals_top_down():
    old = np.zeros((H, W), dtype=np.uint8)
    new = np.full((H, W), 7, dtype=np.uint8)
    wipe = MeltWipe()
    wipe.start(old, new)
    mid = None
    for _ in range(30):  # NOTE: lagging heads need time to emerge
        mid = wipe.tick()
    assert mid is not None
    assert (mid[0] == 7).all()  # NOTE: top rows melt first
    assert (mid[-1] == 0).any()  # NOTE: something still hangs below


def test_melt_front_is_one_wavy_curtain():
    old = np.zeros((H, W), dtype=np.uint8)
    new = np.full((H, W), 7, dtype=np.uint8)
    wipe = MeltWipe()
    wipe.start(old, new)
    heads = -wipe.y.copy()  # NOTE: all heads start above, in [-15, 0]
    assert heads.min() >= 0 and heads.max() <= 15
    assert np.abs(np.diff(wipe.y)).max() <= 1  # NOTE: random-walk front


def test_melt_slides_old_down_below_front():
    old = np.arange(H, dtype=np.uint8).repeat(W).reshape(H, W)
    new = np.full((H, W), 7, dtype=np.uint8)
    wipe = MeltWipe()
    wipe.start(old, new)
    first = wipe.tick()
    assert first is not None
    col = int(np.argmax(wipe.y))  # deepest melted column
    y = int(wipe.y[col])
    assert y > 0
    assert (first[:y, col] == 7).all()  # NOTE: new frame above
    below = first[y:, col]
    assert (below == np.arange(H - y, dtype=np.uint8)).all()


def test_wipe_idle_frames_hold_last_step():
    """Zero-step frames (60fps vs 35Hz melt) re-show the last melt
    frame instead of flashing the end scene (flicker)."""
    old = np.zeros((H, W), dtype=np.uint8)
    new = np.full((H, W), 7, dtype=np.uint8)
    wipe = MeltWipe()
    wipe.start(old, new)
    shown = []
    for i in range(120):  # NOTE: driver pattern: step, hold, step...
        cur = wipe.tick(1) if i % 2 == 0 else wipe.frame
        if cur is None:
            break
        shown.append(int((cur == 7).sum()))
    assert shown  # melted something
    assert all(b >= a for a, b in zip(shown, shown[1:]))  # never jumps back
    assert shown[-1] == H * W  # ends on the new frame
