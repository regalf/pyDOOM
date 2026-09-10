"""Tests for interm.py: patches, count-up, percents, skip."""

import os

import pytest

from pydoom.interm import COUNT_TICS, HOLD_TICS, Intermission, level_patch

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def tally():
    return Intermission("E1M1", "E1M2", 12, 30, 5, 10, 1, 3, 35 * 75,
                        35 * 75)


def test_level_patch_names():
    assert level_patch("E1M1") == "WILV00"
    assert level_patch("E1M9") == "WILV08"


def test_count_up_clamps_and_finishes():
    im = tally()
    assert im.shown() == (0, 0, 0)
    assert not im.finished_tally()
    for _ in range(COUNT_TICS + HOLD_TICS):
        im.tick()
    assert im.shown() == (12, 5, 1)
    assert im.finished_tally()


def test_keypress_skips_then_finishes():
    im = tally()
    im.keypress()
    assert im.shown() == (12, 5, 1)  # NOTE: sweep jumped to targets
    assert not im.finished_tally()
    im.keypress()
    assert im.finished_tally()


def test_percent_empty_total_reads_full():
    im = tally()
    assert im.percent(0, 0) == 100
    assert im.percent(12, 30) == 40


@requires_wad
def test_draw_tally_paints():
    import numpy as np
    from pydoom.menu import Menu, Settings
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    m = Menu(wad, Settings())
    im = tally()
    for _ in range(COUNT_TICS):
        im.tick()
    fb = np.zeros((200, 320), dtype=np.uint8)
    im.draw(fb, m)
    assert (fb != 0).sum() > 3000  # NOTE: name, rows, time, par, enter
    im2 = Intermission("E1M8", None, 0, 0, 0, 0, 0, 0, 0, 0)
    fb2 = np.zeros((200, 320), dtype=np.uint8)
    im2.draw(fb2, m)  # NOTE: no entering line past the finale
    assert (fb2 != 0).sum() > 1000
