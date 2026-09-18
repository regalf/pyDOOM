"""Tests for video settings: pure helpers, cfg round-trip, menu choices."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pygame
from tools.doom_view import desktop_size_on, video_geom

from pydoom import menu
from pydoom.menu import (
    DISPLAY_MODES,
    FPS_LIMITS,
    GL_RESOLUTIONS,
    SW_SCALES,
    Menu,
    Settings,
    display_count,
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
                 display_mode="borderless", display_index=2, sw_scale=2,
                 show_fps=True)
    path = str(tmp_path / "v.cfg")
    settings_save(path, s)
    back = Settings()
    settings_load(path, back)
    assert back.gl_resolution == "1280x800"
    assert back.fps_limit == 144
    assert back.vsync is True
    assert back.display_mode in ("borderless", "windowed")
    assert back.display_index == 2
    assert back.sw_scale == 2
    assert back.show_fps is True


def test_cfg_validation_never_crashes(tmp_path):
    path = tmp_path / "bad.cfg"
    path.write_text("gl_resolution HUGE\n"
                    "fps_limit 1000\n"
                    "vsync 2\n"
                    "display_mode cinema\n"
                    "display_index -5\n"
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
    assert back.display_index == 0


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


def test_choice_stages_without_event_and_wraps():
    # NOTE: rows only stage values now; APPLY commits (video_changed).
    # First row is fps_limit (backend/sizes live in the launcher tab).
    m = _video_menu()
    assert m.key("right") == []
    assert m.settings.fps_limit == 120
    for _ in range(6):
        assert m.key("right") == []
    assert m.settings.fps_limit == 60  # NOTE: wrapped past UNLIMITED
    assert m.key("left") == []
    assert m.settings.fps_limit == 30


def test_choice_enter_stages_without_event():
    m = _video_menu()
    assert m.key("enter") == []
    assert m.settings.fps_limit == 120


def test_apply_emits_event_and_esc_restores():
    m = _video_menu()
    assert m._video_snapshot is None
    m.menus["options"].last_on = 3  # NOTE: enter via options snapshots
    m.current = "options"
    assert m.key("enter") == []
    assert m.current == "video"
    assert m._video_snapshot is not None
    assert m.key("right") == []  # stage fps 120
    assert m.settings.fps_limit == 120
    assert m.key("esc") == []  # NOTE: leave without APPLY restores
    assert m.current == "options"
    assert m.settings.fps_limit == 60
    # NOTE: stage again, then APPLY commits and emits.
    m.current = "video"
    m._video_snapshot = m._staged_video()
    m.key("right")
    items = m.menus["video"].items
    m.menus["video"].last_on = [it.action for it in items].index(
        "apply_video")
    assert m.key("enter") == [("video_changed",)]
    assert m.settings.fps_limit == 120


def test_all_choice_rows_stage():
    m = _video_menu()
    items = m.menus["video"].items
    for i, item in enumerate(items):
        if item.kind != "choice":
            continue
        m.menus["video"].last_on = i
        opts = m._choice_options(item.action)
        before = (m.settings.video_api, m.settings.gl_resolution,
                  m.settings.sw_scale, m.settings.fps_limit,
                  m.settings.vsync, m.settings.display_mode,
                  m.settings.display_index, m.settings.show_fps,
                  m.settings.dynlights, m.settings.texture_filter,
                  m.settings.brightmaps, m.settings.bloom)
        assert m.key("right") == [], item.action
        after = (m.settings.video_api, m.settings.gl_resolution,
                 m.settings.sw_scale, m.settings.fps_limit,
                 m.settings.vsync, m.settings.display_mode,
                 m.settings.display_index, m.settings.show_fps,
                 m.settings.dynlights, m.settings.texture_filter,
                 m.settings.brightmaps, m.settings.bloom)
        if len(opts) > 1:
            assert before != after, item.action
        else:  # NOTE: single-screen headless: SCREEN wraps to itself
            assert before == after, item.action
    s = m.settings
    assert s.gl_resolution in [r.lower() for r in GL_RESOLUTIONS]
    assert s.fps_limit in FPS_LIMITS
    assert s.sw_scale in SW_SCALES
    assert s.display_mode in DISPLAY_MODES
    assert 0 <= s.display_index < display_count()
    assert isinstance(s.dynlights, bool)
    assert s.texture_filter in ("nearest", "linear")
    assert isinstance(s.brightmaps, bool)
    assert isinstance(s.bloom, bool)


def test_screen_row_options_match_desktops():
    m = _video_menu()
    opts = m._choice_options("display_index")
    assert opts == tuple(str(i + 1)
                         for i in range(display_count()))
    assert len(opts) >= 1


def test_video_geom_software_scale():
    s = Settings(display_mode="windowed", sw_scale=1)
    assert video_geom(s, False) == (320, 200, 0, 0)
    s = Settings(display_mode="windowed", sw_scale=3)
    assert video_geom(s, False) == (960, 600, 0, 0)


def test_video_geom_gl_resolution():
    s = Settings(display_mode="windowed", gl_resolution="1280x800")
    assert video_geom(s, True) == (1280, 800, 0, 0)
    s = Settings(display_mode="windowed", gl_resolution="bogus")
    assert video_geom(s, True) == (960, 600, 0, 0)


def test_video_geom_display_index_clamped_headless():
    # NOTE: headless count is 1, so any index lands on screen 0.
    s = Settings(display_mode="windowed", display_index=99)
    assert video_geom(s, False)[3] == 0


def test_video_geom_borderless_uses_desktop():
    s = Settings(display_mode="borderless", display_index=0)
    w, h, flags, disp = video_geom(s, False)
    dw, dh = desktop_size_on(0)
    assert (w, h, flags, disp) == (dw, dh, pygame.NOFRAME, 0)


def test_video_geom_fullscreen_linux_is_windowed():
    # NOTE: exclusive fullscreen is Windows-only; elsewhere the
    # geometry stays a plain window even if forced in.
    if sys.platform == "win32":
        return
    s = Settings(display_mode="fullscreen", gl_resolution="960x600")
    assert video_geom(s, True) == (960, 600, 0, 0)


def _force_dummy_video():
    """Switch SDL to dummy for window-matrix tests (no visible win)."""
    os.environ["SDL_VIDEODRIVER"] = "dummy"
    pygame.display.quit()
    pygame.display.init()


def test_window_matrix_never_raises():
    # NOTE: every mode/api/display combo must return a window (real or
    # fallback), never raise, and restore the SDL position hint.
    from pydoom.glrender import state as glstate
    _force_dummy_video()
    try:
        assert os.environ.get("SDL_VIDEO_WINDOW_POS") is None
        for flags in (0, pygame.NOFRAME, pygame.FULLSCREEN):
            for vsync in (0, 1):
                for disp in (0, 5):
                    scr = glstate.positioned_set_mode(
                        (320, 200), flags, disp, vsync)
                    assert scr is not None
                    if not flags:
                        # NOTE: dummy honors plain sizes; fullscreen
                        # drivers may substitute (dummy gives 1024x768).
                        assert scr.get_size() == (320, 200)
                    for want in ("software", "opengl", "openglv1",
                                   "openglv2"):
                        out = glstate.try_init(
                            320, 200, want, None, False,
                            flags=flags, vsync=vsync, display=disp)
                        assert len(out) == 4
                        assert out[0] is not None
                        # NOTE: dummy video has no GL: always software.
                        assert out[2] == "software"
        assert os.environ.get("SDL_VIDEO_WINDOW_POS") is None
    finally:
        pygame.display.quit()


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
