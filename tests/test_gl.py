"""Tests for milestone H phase 0: video api setting + GL fallback.

resolve_api() is pure logic (no pygame/GL context), so every fallback
rule is pinned headless-safe. try_init() is only exercised on the
software path (never opens a GL window in tests).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.state import (
    OPENGL,
    OPENGLV1,
    OPENGLV2,
    SOFTWARE,
    resolve_api,
)

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def test_software_request_stays_software():
    eff, _ = resolve_api("software", have_gl=True)
    assert eff == SOFTWARE


def test_unknown_request_stays_software():
    eff, _ = resolve_api("vulkan", have_gl=True)
    assert eff == SOFTWARE


def test_opengl_needs_pyopengl():
    eff, why = resolve_api("opengl", sdl_video="x11",
                           have_gl=False)
    assert eff == SOFTWARE and "PyOpenGL" in why


def test_opengl_falls_back_on_dummy_video():
    eff, why = resolve_api("opengl", sdl_video="dummy",
                           have_gl=True)
    assert eff == SOFTWARE and "dummy" in why


def test_opengl_falls_back_on_frames_smoke():
    eff, why = resolve_api("opengl", sdl_video="x11", frames_opt=30,
                           have_gl=True)
    assert eff == SOFTWARE and "frames" in why


def test_opengl_falls_back_on_timedemo():
    eff, _ = resolve_api("opengl", sdl_video="x11", timedemo=True,
                         have_gl=True)
    assert eff == SOFTWARE


def test_opengl_passes_when_available():
    eff, _ = resolve_api("opengl", sdl_video="x11",
                         have_gl=True)
    assert eff == OPENGLV1  # NOTE: legacy name means v1
    eff, _ = resolve_api("openglv2", sdl_video="x11",
                         have_gl=True)
    assert eff == OPENGLV2
    assert OPENGL != OPENGLV1  # NOTE: alias constant kept for compat


def test_video_api_config_round_trip(tmp_path):
    from pydoom.menu import Settings, settings_load, settings_save
    path = str(tmp_path / "pydoom.cfg")
    s = Settings()
    s.video_api = "opengl"
    settings_save(path, s)
    back = Settings()
    settings_load(path, back)
    assert back.video_api == "openglv1"  # NOTE: legacy alias upgrades


def test_video_api_rejects_garbage(tmp_path):
    from pydoom.menu import Settings, settings_load
    path = str(tmp_path / "pydoom.cfg")
    with open(path, "w") as f:
        f.write("video_api vulkan\n")
    back = Settings()
    settings_load(path, back)  # NOTE: keeps default, never raises
    assert back.video_api == "software"


def test_viewer_args_carry_gl_only():
    from pyDOOM import build_viewer_args
    assert "--video-api=openglv1" in build_viewer_args(
        "DOOM1.WAD", "E1M1", "normal", video_api="openglv1")
    assert "--video-api=openglv2" in build_viewer_args(
        "DOOM1.WAD", "E1M1", "normal", video_api="openglv2")
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


def test_have_gl_probe_false_when_blocked():
    """Blocked OpenGL import reads as missing (fallback reason)."""
    from pydoom.glrender import state
    saved = {k: v for k, v in sys.modules.items()
             if k == "OpenGL" or k.startswith("OpenGL.")}
    sys.modules["OpenGL"] = None
    try:
        assert state._have_gl() is False
        eff, why = resolve_api("opengl", sdl_video="x11",
                               have_gl=state._have_gl())
        assert eff == SOFTWARE and "PyOpenGL" in why
    finally:
        del sys.modules["OpenGL"]
        sys.modules.update(saved)


@requires_wad
def test_opengl_flag_dummy_frames_smoke_stays_software():
    """--video-api=opengl under dummy SDL: software path, exit 0."""
    import subprocess
    root = os.path.join(os.path.dirname(__file__), "..")
    env = dict(os.environ, SDL_VIDEODRIVER="dummy",
               SDL_AUDIODRIVER="dummy")
    cmd = [sys.executable, "tools/doom_view.py", "E1M1",
           "--video-api=opengl", "--frames=30"]
    out = subprocess.run(cmd, capture_output=True, text=True,
                         cwd=root, env=env, timeout=300, check=False)
    assert out.returncode == 0, out.stderr[-2000:]
    assert "video: software" in out.stdout
    assert "smoke:" in out.stdout


def _open_window():
    import pygame
    pygame.init()
    try:
        pygame.display.set_mode((320, 200),
                                pygame.OPENGL | pygame.DOUBLEBUF)
    except Exception:  # noqa: BLE001 - any display failure skips
        pygame.quit()
        pytest.skip("no GL context")
        return None
    try:
        from OpenGL import GL
        ver = GL.glGetString(GL.GL_VERSION)
        if not ver or int(ver.split(b".")[0]) < 3:
            raise ValueError("need GL 3+")
    except Exception:  # noqa: BLE001 - any GL failure skips
        pygame.quit()
        pytest.skip("no GL 3+ context")
        return None
    return True


@pytest.fixture(scope="module")
def gate_data():
    """Static E1M1 scene (no sim): geometry + deterministic mobjs."""
    from types import SimpleNamespace

    from pydoom.glrender import dynamic as gldyn
    from pydoom.glrender import light as gllight
    from pydoom.glrender import preprocess as glpre
    from pydoom.glrender import sprites as glsprites
    from pydoom.glrender import textures as gltex
    from pydoom.info import spawn_visual
    from pydoom.mapdata import Map
    from pydoom.renderer import (
        SKIP_THING_TYPES,
        Renderer,
        init_sprite_defs,
    )
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    skyflat = texman.flat_num_for_name("F_SKY1")
    renderer = Renderer(wad, texman)
    dyn = gldyn.DynamicState.take(game_map)
    walls = glpre.build_walls(game_map, texman, skyflat)
    planes = glpre.emit_planes(dyn.fans, game_map, skyflat)
    wtex = gltex.build_wall_textures(
        texman, set(gltex.wall_texnums_used(walls))
        | {renderer.skytexture})
    ftex = gltex.build_flat_textures(texman, gltex.all_flatnums(texman))
    stex = gltex.build_sprite_textures(texman, range(texman.numsprites))
    mobjs = []
    for thing in game_map.things:
        if thing.type in SKIP_THING_TYPES:
            continue
        visual = spawn_visual(thing.type)
        if visual is None:
            continue
        sprite, frame, flags = visual
        sub = renderer.sector_at(game_map, thing.x << 16,
                                 thing.y << 16)
        if sub.sector is None:
            continue
        mobjs.append(SimpleNamespace(
            dead=False, state=1, flags=flags, sprite=sprite,
            frame=frame, x=thing.x << 16, y=thing.y << 16,
            z=sub.sector.floorheight,
            angle=((thing.angle % 360) * 0x100000000) // 360,
            sector=sub.sector))
    feed = glsprites.SpriteFeed(sprites=init_sprite_defs(
        wad, texman.firstsprite, texman.lastsprite))
    return {"wad": wad, "texman": texman, "game_map": game_map,
            "renderer": renderer, "walls": walls, "planes": planes,
            "wtex": wtex, "ftex": ftex, "stex": stex,
            "cmap": gllight.colormap_lut(
                bytes(wad.cache_lump("COLORMAP"))),
            "pal": bytes(wad.read_lump("PLAYPAL")),
            "mobjs": mobjs, "feed": feed}


# NOTE: (name, x, y, yawdeg, min_exact, max_mean). Measured 2026-09
# (Mesa llvmpipe-class GL, static mobjs): start .668/5.20, pool
# .630/7.92, outdoor-sky .563/8.57, north-room .278/22.55 (known
# weak: fence + dark-quantization, see DIVERGENCES.md), corridor
# .429/14.05, garden .555/9.20. Floors guard global brightness
# shifts; ceilings catch tier/sky regressions.
GATE_VIEWS = [
    ("start", 1056, -3616, 90, 0.60, 7.5),
    ("pool", 1328, -3291, 336, 0.55, 10.5),
    ("outdoor-sky", 2000, -3291, 330, 0.49, 11.0),
    ("north-room", 1169, -2274, 247, 0.20, 26.0),
    ("corridor", 1056, -3000, 270, 0.35, 17.0),
    ("garden", 1500, -3291, 336, 0.48, 12.0),
]


@requires_wad
def test_parity_gate_e1m1_walk(gate_data):
    """Phase 5 gate: fixed E1M1 scenes, SW vs GL within tolerance."""
    import numpy as np
    import pygame

    from pydoom.glrender.draw import FrameRenderer
    from pydoom.glrender.dynamic import sector_light_bases
    from pydoom.glrender.upload import GlResources
    from pydoom.palette import load_playpal
    if _open_window() is None:
        return
    try:
        wad, texman, game_map = (gate_data["wad"],
                                 gate_data["texman"],
                                 gate_data["game_map"])
        renderer, mobjs, feed = (gate_data["renderer"],
                                 gate_data["mobjs"],
                                 gate_data["feed"])
        lut = np.array(load_playpal(wad.read_lump("PLAYPAL")),
                       dtype=np.uint8)
        res = GlResources.create(
            gate_data["walls"], gate_data["planes"],
            gate_data["wtex"], gate_data["ftex"], gate_data["cmap"],
            gate_data["pal"], sprite_tex=gate_data["stex"],
            sector_lights=sector_light_bases(game_map))
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            skyarg = (res.wall_textures[renderer.skytexture],
                      res.wall_info[renderer.skytexture][1])
            failures = []
            for name, cx, cy, deg, want_exact, want_mean in GATE_VIEWS:
                ang = (deg * 0x100000000 // 360) & 0xFFFFFFFF
                fb = renderer.render_view(game_map, cx << 16,
                                          cy << 16, ang, mobjs=mobjs)
                sw = lut[fb]
                bbs = feed.project(mobjs, cx << 16, cy << 16, ang,
                                   texman)
                fr.render(cx << 16, cy << 16, renderer.viewz, ang,
                          sky=skyarg, sprites=bbs)
                gl = fr.readback()
                d = np.abs(gl.astype(int) - sw.astype(int)).max(
                    axis=2)
                exact, mean = float((d == 0).mean()), float(d.mean())
                if exact < want_exact or mean > want_mean:
                    failures.append(
                        f"{name}: exact={exact:.3f} "
                        f"(want>={want_exact}) mean={mean:.2f} "
                        f"(want<={want_mean})")
            assert not failures, "; ".join(failures)
        finally:
            fr.close()
            res.delete()
    finally:
        pygame.quit()
