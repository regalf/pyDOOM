"""Tests for menu.py: navigation, sliders, confirms, events."""

import os

import pytest

from pydoom.menu import SKILLS, Menu, Settings

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def fresh(**kw):
    return Menu(None, Settings(), **kw)


def test_move_wraps_and_skips_nothing():
    m = fresh()
    assert m.menus["main"].last_on == 0
    m.key("up")
    assert m.menus["main"].last_on == 5  # NOTE: wraps to quit
    m.key("down")
    assert m.menus["main"].last_on == 0


def test_enter_submenu_and_esc_back():
    m = fresh()
    assert m.key("enter") == []  # new game -> episode
    assert m.current == "episode"
    assert m.key("esc") == []
    assert m.current == "main"


def test_esc_on_main_closes():
    assert fresh().key("esc") == ["close"]


def test_shortcut_jumps_and_activates():
    m = fresh()
    assert m.key("q") == []  # quit asks first
    assert m.mode == "confirm"
    assert m.key("n") == []
    assert m.mode == "menu"


def test_quit_confirm_yes_no():
    m = fresh()
    m.key("q")
    assert m.key("y") == ["quit"]
    m.key("q")
    assert m.key("esc") == []
    assert m.mode == "menu"


def test_skill_select_emits_new_game():
    m = fresh()
    m.key("enter")  # main -> episode
    m.key("enter")  # ep1 -> skill (preselected hurt me)
    assert m.current == "skill"
    assert m.key("enter") == [("new_game", 0, "normal")]


def test_nightmare_verifies():
    m = fresh()
    m.key("enter")
    m.key("enter")
    m.key("down")  # ultra
    m.key("down")  # nightmare
    assert m.key("enter") == []  # asks first
    assert m.mode == "confirm"
    assert m.key("y") == [("new_game", 0, "nightmare")]


def test_shareware_episode_scolds():
    m = fresh()
    m.key("enter")
    m.key("down")  # ep2
    assert m.key("enter") == []
    assert m.mode == "message"  # NOTE: SWSTRING, then Read This!
    assert m.key("enter") == []
    assert m.mode == "readthis"


def test_readthis_pages_then_closes():
    m = fresh()
    m.menus["main"].last_on = 4
    assert m.key("enter") == []
    assert m.mode == "readthis"
    assert m.key("enter") == []
    assert m.readpage == 1
    assert m.key("enter") == ["close"]


def test_sliders_clamp():
    m = fresh()
    for _ in range(20):
        m.slider_adjust("sfx", 1)
    assert m.settings.sfx_vol == 15
    for _ in range(20):
        m.slider_adjust("sens", -1)
    assert m.settings.mouse_sens == 0


def test_messages_toggle():
    m = fresh()
    m.current = "options"
    assert m.settings.messages is True
    m.key("enter")
    assert m.settings.messages is False


def test_skill_preselect_from_cli():
    assert fresh(skill_index=4).menus["skill"].last_on == 4
    assert fresh(skill_index=99).menus["skill"].last_on == 4
    assert fresh(skill_index=-3).menus["skill"].last_on == 0


@requires_wad
def test_draw_main_menu_paints_skull_and_title():
    import numpy as np
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    m = Menu(wad, Settings())
    fb = np.zeros((200, 320), dtype=np.uint8)
    m.draw(fb)
    assert (fb != 0).sum() > 1000  # NOTE: title + items + skull
    before = (fb != 0).sum()
    m.key("down")
    m.draw(np.zeros((200, 320), dtype=np.uint8))
    out = np.zeros((200, 320), dtype=np.uint8)
    m.draw(out)
    assert (out != 0).sum() == before  # NOTE: same pixels, skull moved


@requires_wad
def test_draw_readthis_covers_screen():
    import numpy as np
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    m = Menu(wad, Settings())
    m.mode = "readthis"
    m.readpage = 0
    fb = np.zeros((200, 320), dtype=np.uint8)
    m.draw(fb)
    assert (fb != 0).sum() > 20000  # NOTE: fullscreen HELP1 art


def test_event_protocol_matches_viewer():
    """Every event key() can emit is one the viewer applies: close,
    quit, or (new_game, episode, skill)."""
    seen = set()

    def collect(evts):
        for e in evts:
            seen.add(e[0] if isinstance(e, tuple) else e)

    collect(fresh().key("esc"))  # close
    m = fresh()
    m.key("enter")  # main -> episode
    m.key("enter")  # ep1 -> skill
    collect(m.key("enter"))  # new_game normal
    m = fresh()
    m.key("enter")
    m.key("enter")
    m.key("down")
    m.key("down")  # nightmare
    m.key("enter")  # asks
    collect(m.key("y"))  # new_game nightmare
    m = fresh()
    m.key("q")  # quit asks
    collect(m.key("y"))  # quit
    assert seen == {"close", "quit", "new_game"}
