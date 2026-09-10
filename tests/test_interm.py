"""Tests for interm.py: staged tally, entering map, vanilla pacing."""

import os

import pytest

from pydoom.interm import Intermission, level_patch, map_index

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def tally():
    return Intermission("E1M1", "E1M2", 12, 30, 5, 10, 1, 3, 35 * 75,
                        35 * 75)


def run(im, n):
    for _ in range(n):
        im.tick()


def test_level_names_and_indices():
    assert level_patch("E1M1") == "WILV00"
    assert level_patch("E1M9") == "WILV08"
    assert map_index("E1M3") == 2


def test_stages_climb_in_order():
    im = tally()
    assert im.shown == [-1, -1, -1]  # NOTE: vanilla starts counts at -1
    run(im, 35)  # NOTE: 1 s pause, then kills start
    assert im.stage == 2
    run(im, 200)
    assert im.shown[0] == 40  # NOTE: 12/30 kills tallied
    assert im.stage >= 4
    run(im, 600)
    assert im.shown[1] == 50 and im.shown[2] == 33
    assert im.shown_time == 75 and im.shown_par == 75
    assert im.stage == 10  # NOTE: waiting for the key


def test_keypress_hurries_then_advances():
    im = tally()
    im.keypress()
    assert im.shown == [40, 50, 33]  # NOTE: jumped straight to finals
    assert im.stage == 10
    assert not im.finished_tally()
    im.keypress()  # NOTE: shotgun cock onto the entering map
    assert im.state == "nextloc"
    assert im.finished_tally() is False
    im.keypress()
    assert im.finished_tally()


def test_nextloc_holds_four_seconds():
    im = tally()
    im.keypress()
    im.keypress()
    assert im.state == "nextloc"
    run(im, 4 * 35 - 1)
    assert not im.finished_tally()
    run(im, 1)
    assert im.finished_tally()


def test_percent_empty_total_reads_full():
    assert Intermission.percent(0, 0) == 100
    assert Intermission.percent(12, 30) == 40


@requires_wad
def test_draw_both_screens_paint():
    import numpy as np
    from pydoom.menu import Menu, Settings
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    m = Menu(wad, Settings())
    im = tally()
    run(im, 400)
    fb = np.zeros((200, 320), dtype=np.uint8)
    im.draw(fb, m)
    assert (fb != 0).sum() > 3000  # NOTE: map, flickers, tally rows
    im.keypress()
    im.keypress()
    fb2 = np.zeros((200, 320), dtype=np.uint8)
    im.draw(fb2, m)
    assert (fb2 != 0).sum() > 3000  # NOTE: splats, arrow, entering
    assert not (fb == fb2).all()  # NOTE: two distinct screens
