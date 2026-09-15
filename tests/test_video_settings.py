"""Tests for video settings: pure helpers, cfg round-trip, menu choices."""

import sys

from pydoom import menu
from pydoom.menu import (
    DISPLAY_MODES,
    FPS_LIMITS,
    GL_RESOLUTIONS,
    SW_SCALES,
    Menu,
    Settings,
    letterbox,
    parse_resolution,
    settings_load,
    settings_save,
    sw_window_size,
)


def test_parse_resolution_ok():
    assert parse_resolution("960x600") == (960, 600)
    assert parse_resolution("1920X1200") == (1920, 1200)
    assert parse_resolution("640x400") == (640, 400)


def test_parse_resolution_bad():
    assert parse_resolution("banana") is None
    assert parse_resolution("960") is None
    assert parse_resolution("0x600") is None
    assert parse_resolution("-1x600") is None
    assert parse_resolution("99999x600") is None
    assert parse_resolution("") is None


def test_sw_window_size():
    assert sw_window_size(1) == (320, 200)
    assert sw_window_size(2) == (640, 400)
    assert sw_window_size(3) == (960, 600)
    assert sw_window_size(99) == (960, 600)  # NOTE: falls back to 3


def test_letterbox_exact_sizes():
    assert letterbox(320, 200) == (320, 200, 0, 0)
    assert letterbox(640, 400) == (640, 400, 0, 0)
    assert letterbox(960, 600) == (960, 600, 0, 0)


def test_letterbox_desktop_bars():
    # NOTE: integer scale, centered, never stretched.
    assert letterbox(1920, 1080) == (1600, 1000, 160, 40)
    assert letterbox(1366, 768) == (960, 600, 203, 84)
    # NOTE: degenerate tiny window still picks scale 1 (windowed
    # sizes never go below 320x200 in practice).
    assert letterbox(100, 100) == (320, 200, -110, -50)


def test_cfg_round_trip(tmp_path):
    s = Settings(gl_resolution="1280x800", fps_limit=144, vsync=True,
                 display_mode="borderless", sw_scale=2, show_fps=True)
    path = str(tmp_path / "v.cfg")
    settings_save(path, s)
    back = Settings()
    settings_load(path, back)
    assert back.gl_resolution == "1280x800"
    assert back.fps_limit == 144
    assert back.vsync is True
    assert back.display_mode in ("borderless", "windowed")
    assert back.sw_scale == 2
    assert back.show_fps is True


def test_cfg_validation_never_crashes(tmp_path):
    path = tmp_path / "bad.cfg"
    path.write_text("gl_resolution HUGE\n"
                    "fps_limit 1000\n"
                    "vsync 2\n"
                    "display_mode cinema\n"
                    "sw_scale 9\n"
                    "show_fps maybe\n"
                    "video_api vulkan\n")
    back = Settings()
    settings_load(str(path), back)  # NOTE: tolerates everything
    assert back.gl_resolution == "960x600"
    assert back.fps_limit == 60
    assert back.sw_scale == 3
    assert back.video_api == "software"
    assert back.display_mode == "windowed"


def test_display_modes_platform():
    if sys.platform == "win32":
        assert "fullscreen" in DISPLAY_MODES
    else:
        # NOTE: exclusive fullscreen is Windows-only; borderless
        # covers fullscreen duty everywhere else.
        assert "fullscreen" not in DISPLAY_MODES
    assert "windowed" in DISPLAY_MODES
    assert "borderless" in DISPLAY_MODES


def _video_menu():
    m = Menu(None, Settings())
    m.current = "video"
    return m


def test_choice_cycle_emits_event_and_wraps():
    m = _video_menu()
    assert m.key("right") == [("video_changed",)]
    assert m.settings.video_api == "opengl"
    assert m.key("right") == [("video_changed",)]
    assert m.settings.video_api == "software"  # NOTE: wrapped
    assert m.key("left") == [("video_changed",)]
    assert m.settings.video_api == "opengl"


def test_choice_enter_advances():
    m = _video_menu()
    assert m.key("enter") == [("video_changed",)]
    assert m.settings.video_api == "opengl"


def test_all_choice_rows_cycle():
    m = _video_menu()
    items = m.menus["video"].items
    for i, item in enumerate(items):
        m.menus["video"].last_on = i
        before = (m.settings.video_api, m.settings.gl_resolution,
                  m.settings.sw_scale, m.settings.fps_limit,
                  m.settings.vsync, m.settings.display_mode,
                  m.settings.show_fps)
        assert m.key("right") == [("video_changed",)], item.action
        after = (m.settings.video_api, m.settings.gl_resolution,
                 m.settings.sw_scale, m.settings.fps_limit,
                 m.settings.vsync, m.settings.display_mode,
                 m.settings.show_fps)
        assert before != after, item.action
    s = m.settings
    assert s.gl_resolution in [r.lower() for r in GL_RESOLUTIONS]
    assert s.fps_limit in FPS_LIMITS
    assert s.sw_scale in SW_SCALES
    assert s.display_mode in DISPLAY_MODES


def test_options_has_video_row():
    m = Menu(None, Settings())
    m.current = "options"
    acts = [it.action for it in m.menus["options"].items]
    assert "video" in acts
    m.menus["options"].last_on = acts.index("video")
    assert m.key("enter") == []
    assert m.current == "video"
    assert m.key("esc") == []
    assert m.current == "options"
