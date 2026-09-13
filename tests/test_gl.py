"""Tests for milestone H phase 0: video api setting + GL fallback.

resolve_api() is pure logic (no pygame/GL context), so every fallback
rule is pinned headless-safe. try_init() is only exercised on the
software path (never opens a GL window in tests).
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.state import OPENGL, SOFTWARE, resolve_api


def test_software_request_stays_software():
    eff, _ = resolve_api("software", have_moderngl=True)
    assert eff == SOFTWARE


def test_unknown_request_stays_software():
    eff, _ = resolve_api("vulkan", have_moderngl=True)
    assert eff == SOFTWARE


def test_opengl_needs_moderngl():
    eff, why = resolve_api("opengl", sdl_video="x11",
                           have_moderngl=False)
    assert eff == SOFTWARE and "moderngl" in why


def test_opengl_falls_back_on_dummy_video():
    eff, why = resolve_api("opengl", sdl_video="dummy",
                           have_moderngl=True)
    assert eff == SOFTWARE and "dummy" in why


def test_opengl_falls_back_on_frames_smoke():
    eff, why = resolve_api("opengl", sdl_video="x11", frames_opt=30,
                           have_moderngl=True)
    assert eff == SOFTWARE and "frames" in why


def test_opengl_falls_back_on_timedemo():
    eff, _ = resolve_api("opengl", sdl_video="x11", timedemo=True,
                         have_moderngl=True)
    assert eff == SOFTWARE


def test_opengl_passes_when_available():
    eff, _ = resolve_api("opengl", sdl_video="x11",
                         have_moderngl=True)
    assert eff == OPENGL


def test_video_api_config_round_trip(tmp_path):
    from pydoom.menu import Settings, settings_load, settings_save
    path = str(tmp_path / "pydoom.cfg")
    s = Settings()
    s.video_api = "opengl"
    settings_save(path, s)
    back = Settings()
    settings_load(path, back)
    assert back.video_api == "opengl"


def test_video_api_rejects_garbage(tmp_path):
    from pydoom.menu import Settings, settings_load
    path = str(tmp_path / "pydoom.cfg")
    with open(path, "w") as f:
        f.write("video_api vulkan\n")
    back = Settings()
    settings_load(path, back)  # NOTE: keeps default, never raises
    assert back.video_api == "software"


def test_viewer_args_carry_opengl_only():
    from pyDOOM import build_viewer_args
    assert "--video-api=opengl" in build_viewer_args(
        "DOOM1.WAD", "E1M1", "normal", video_api="opengl")
    default = build_viewer_args("DOOM1.WAD", "E1M1", "normal")
    assert not any(a.startswith("--video-api") for a in default)


def test_try_init_software_never_touches_gl(monkeypatch):
    """Software path returns a window with ctx None (dummy video)."""
    monkeypatch.setenv("SDL_VIDEODRIVER", "dummy")
    import pygame
    pygame.init()
    try:
        from pydoom.glrender import try_init
        screen, ctx, eff, _ = try_init(320, 200, "software")
        assert eff == SOFTWARE and ctx is None
        assert screen.get_size() == (320, 200)
    finally:
        pygame.quit()
