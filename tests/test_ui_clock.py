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
