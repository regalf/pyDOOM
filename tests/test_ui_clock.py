"""UI-tic clock: menu/inter animations run at 35Hz whatever the fps."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from tools.doom_view import TICRATE, step_ui_clock

from pydoom.menu import Menu, Settings


def _run_frames(dt: float, total: float) -> int:
    """Total UI tics over total seconds at fixed frame dt."""
    acc, ticks, t = 0.0, 0, 0.0
    while t < total:
        acc, n = step_ui_clock(acc, dt)
        ticks += n
        t += dt
    return ticks


def test_same_ticks_across_framerates():
    # NOTE: 2 wall-clock seconds at 30fps vs 240fps agree (±1 phase).
    slow = _run_frames(1.0 / 30, 2.0)
    fast = _run_frames(1.0 / 240, 2.0)
    assert (slow, fast) == (70, 70) or abs(slow - fast) <= 1
    assert abs(slow - 2 * TICRATE) <= 1


def test_sub_step_frames_accumulate():
    acc, n = step_ui_clock(0.0, 1.0 / 240)
    assert n == 0  # NOTE: nothing fires before 1/35s accrues
    acc, total = acc, 0
    for _ in range(7):
        acc, n = step_ui_clock(acc, 1.0 / 240)
        total += n
    assert total == 1


def test_menu_skull_needs_eight_ui_tics():
    m = Menu(None, Settings())
    assert m.which_skull == 0
    for _ in range(7):
        m.tick()
    assert m.which_skull == 0
    m.tick()
    assert m.which_skull == 1  # NOTE: flips every 8 ticks (M_Ticker)


def test_step_wipe_clock_runs_then_lands():
    import numpy as np
    from tools.doom_view import step_wipe_clock
    from pydoom.wipe import H, W, MeltWipe
    wipe = MeltWipe()
    wipe.start(np.zeros((H, W), dtype=np.uint8),
               np.full((H, W), 7, dtype=np.uint8))
    acc, seen, landed = 0.0, 0, False
    for _ in range(600):  # NOTE: 1/60 frames, melt needs ~1.2s
        frame, acc, landed = step_wipe_clock(wipe, acc, 1.0 / 60)
        if landed:
            break
        seen += 1
        assert frame.shape == (H, W)
    assert landed and seen > 10


def test_step_wipe_clock_broken_frame_lands():
    import numpy as np
    from tools.doom_view import step_wipe_clock
    from pydoom.wipe import H, W

    class Broken:
        def __init__(self):
            self.steps = None

        def tick(self, steps):
            self.steps = steps
            return np.zeros((10, 10), dtype=np.uint8)

        frame = None

    broken = Broken()
    frame, _acc, landed = step_wipe_clock(broken, 0.0, 1.0)
    assert broken.steps == 35  # NOTE: ~1s worth of steps in one call
    assert landed and frame is None  # NOTE: never presented
