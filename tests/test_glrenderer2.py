"""Tests for glrenderer2: facade cache, profiler, v2-vs-v1 parity."""

import os

import pytest

from pydoom.glrenderer2 import gl
from pydoom.glrenderer2.profile import Profiler

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


class FakeGL:
    """Records backend calls (no context needed)."""

    def __init__(self):
        self.calls = []

    def __getattr__(self, name):
        def rec(*args):
            self.calls.append((name, args))
        return rec


@pytest.fixture()
def fake():
    gl.cache_reset()
    gl._real = FakeGL()
    yield gl._real
    gl._real = None
    gl.cache_reset()


def test_facade_skips_redundant_state(fake):
    gl.glUseProgram(3)
    gl.glUseProgram(3)
    gl.glActiveTexture(100)
    gl.glActiveTexture(100)
    gl.glBindTexture(200, 7)  # unit 100
    gl.glBindTexture(200, 7)
    gl.glActiveTexture(101)
    gl.glBindTexture(200, 7)  # other unit: rebinds
    gl.glBindVertexArray(9)
    gl.glBindVertexArray(9)
    gl.glEnable(300)
    gl.glEnable(300)
    gl.glDisable(301)  # already off: skipped
    gl.glDepthMask(False)
    gl.glDepthMask(False)
    gl.glBlendFunc(1, 2)
    gl.glBlendFunc(1, 2)
    got = [c for c, _a in fake.calls]
    assert got.count("glUseProgram") == 1
    assert got.count("glActiveTexture") == 2
    assert got.count("glBindTexture") == 2
    assert got.count("glBindVertexArray") == 1
    assert got.count("glEnable") == 1
    assert "glDisable" not in got
    assert got.count("glDepthMask") == 1
    assert got.count("glBlendFunc") == 1
    assert gl.stats()["skipped"] > 0


def test_facade_uniforms_scoped_by_program(fake):
    gl.glUseProgram(1)
    gl.glUniform1i(10, 5)
    gl.glUniform1i(10, 5)  # same prog/loc/value: skipped
    gl.glUniform1f(11, 0.5)
    gl.glUseProgram(2)
    gl.glUniform1i(10, 5)  # other program: re-uploads
    import numpy as np
    mat = np.eye(4, dtype=np.float32)
    gl.glUniformMatrix4fv(12, 1, True, mat)
    gl.glUniformMatrix4fv(12, 1, True, mat.copy())  # same bytes: skipped
    got = [c for c, _a in fake.calls]
    assert got.count("glUniform1i") == 2
    assert got.count("glUniform1f") == 1
    assert got.count("glUniformMatrix4fv") == 1


def test_facade_cache_reset(fake):
    gl.glUseProgram(3)
    gl.cache_reset()
    gl.glUseProgram(3)  # forgotten: re-issued
    assert [c for c, _a in fake.calls].count("glUseProgram") == 2


def test_profiler_scopes_and_report():
    prof = Profiler()
    assert prof.report() == "profile: no samples"
    with prof.scope("walls"):
        pass
    with prof.scope("walls"):
        pass
    prof.frame()
    prof.frame()
    out = prof.report()
    assert "profile: 2 frames" in out
    assert "walls" in out
    assert prof.total["walls"][1] == 2
    prof.reset()
    assert prof.report() == "profile: no samples"


def _open_window():
    """320x200 GL window (or skip: headless CI, GL<3 never enter)."""
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
def e1m1():
    from pydoom.glrender.light import colormap_lut
    from pydoom.glrender.preprocess import build_planes, build_walls
    from pydoom.glrender.textures import (
        build_flat_textures,
        build_wall_textures,
        flatnums_used,
        wall_texnums_used,
    )
    from pydoom.mapdata import Map
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    walls = build_walls(game_map, texman, sky)
    planes = build_planes(game_map, sky)
    wtex = build_wall_textures(texman, wall_texnums_used(walls))
    ftex = build_flat_textures(texman, flatnums_used(planes))
    cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
    pal = bytes(wad.read_lump("PLAYPAL"))
    start = next(t for t in game_map.things if t.type == 1)
    return {"walls": walls, "planes": planes, "wtex": wtex,
            "ftex": ftex, "cmap": cmap, "pal": pal, "start": start,
            "game_map": game_map}


def _render_both(e1m1, angle, fullbright=False):
    """(v1 rgb, v2 rgb, v2 frame) for one E1M1 view."""
    from pydoom.glrender.draw import FrameRenderer
    from pydoom.glrender.upload import GlResources
    from pydoom.glrenderer2.renderer import FrameRenderer2
    res = GlResources.create(e1m1["walls"], e1m1["planes"],
                             e1m1["wtex"], e1m1["ftex"],
                             e1m1["cmap"], e1m1["pal"])
    assert res is not None
    try:
        start = e1m1["start"]
        x, y = start.x << 16, start.y << 16
        viewz = 41 << 16
        f1 = FrameRenderer(res, 320, 200)
        try:
            f1.render(x, y, viewz, angle, fullbright=fullbright)
            rgb1 = f1.readback()
        finally:
            f1.close()
        f2 = FrameRenderer2(res, 320, 200)
        try:
            f2.render(x, y, viewz, angle, fullbright=fullbright)
            rgb2 = f2.readback()
        finally:
            f2.close()
            res.delete()
        return rgb1, rgb2, f2
    except Exception:
        res.delete()
        raise


@requires_wad
def test_v2_parity_vs_v1(e1m1):
    _open_window()
    import pygame
    try:
        for angle in (0, 0x20000000, 0x80000000):
            rgb1, rgb2, _f2 = _render_both(e1m1, angle)
            assert (rgb1 == rgb2).all()  # NOTE: same programs, same pixels
        rgb1, rgb2, _f2 = _render_both(e1m1, 0, fullbright=True)
        assert (rgb1 == rgb2).all()
    finally:
        pygame.quit()


@requires_wad
def test_v2_records_scopes_and_skips(e1m1):
    _open_window()
    import pygame
    try:
        _rgb1, _rgb2, f2 = _render_both(e1m1, 0)
        assert {"walls", "planes"} <= set(f2.prof.total)
        assert gl.stats()["skipped"] > 0  # NOTE: the cache earns its keep
        assert "walls" in f2.prof.report()
    finally:
        pygame.quit()


def test_facade_uniform_reissues_after_change(fake):
    gl.glUseProgram(1)
    gl.glUniform1f(10, 1.0)
    gl.glUniform1f(10, 2.0)
    # NOTE: seen before, but GL holds 2.0: must re-issue (seen-set
    # caching dropped these pixels on wall batches).
    gl.glUniform1f(10, 1.0)
    got = [a for c, a in fake.calls if c == "glUniform1f"]
    assert got == [(10, 1.0), (10, 2.0), (10, 1.0)]


def test_fence_helpers_none_safe(fake):
    assert gl.place_fence() is None  # NOTE: FakeGL records, returns None
    assert gl.wait_fence(None) is True
    gl.delete_fence(None)  # NOTE: no-op, never raises


def test_fence_wait_semantics():
    import types
    from pydoom.glrenderer2 import gl as gmod
    gmod.cache_reset()
    calls = []

    class SigGL:
        GL_SYNC_GPU_COMMANDS_COMPLETE = 1
        GL_SYNC_FLUSH_COMMANDS_BIT = 2
        GL_ALREADY_SIGNALED = 10
        GL_CONDITION_SATISFIED = 11
        GL_TIMEOUT_EXPIRED = 12

        def __init__(self, verdict):
            self.verdict = verdict

        def glFenceSync(self, *args):
            calls.append(("fence", args))
            return "SYNC"

        def glClientWaitSync(self, *args):
            calls.append(("wait", args))
            return self.verdict

        def glDeleteSync(self, *args):
            calls.append(("delete", args))

    gmod._real = SigGL(10)
    try:
        assert gmod.place_fence() == "SYNC"
        assert gmod.wait_fence("SYNC") is True
    finally:
        gmod._real = None
        gmod.cache_reset()
    gmod._real = SigGL(12)
    try:
        assert gmod.wait_fence("SYNC") is False  # NOTE: timeout counts on
    finally:
        gmod._real = None
        gmod.cache_reset()


def test_profiler_counters_report_and_reset():
    prof = Profiler()
    prof.count("fence_stall")
    prof.count("fence_stall")
    with prof.scope("walls"):
        pass
    prof.frame()
    out = prof.report()
    assert "fence_stall" in out and "2" in out
    prof.reset()
    assert prof.counters == {}
    assert prof.report() == "profile: no samples"


def test_profiler_scope_callbacks():
    seen = []
    prof = Profiler(enter=lambda n: seen.append(("in", n)),
                    exit=lambda n: seen.append(("out", n)))
    with prof.scope("walls"):
        pass
    assert seen == [("in", "walls"), ("out", "walls")]
    with pytest.raises(RuntimeError):
        with prof.scope("boom"):
            raise RuntimeError("x")
    assert seen[-2:] == [("in", "boom"), ("out", "boom")]
    assert "boom" in prof.total  # NOTE: timing recorded anyway


def test_profiler_bad_callbacks_never_break():
    def _boom(_name):
        raise RuntimeError("x")
    prof = Profiler(enter=_boom, exit=_boom)
    with prof.scope("walls"):
        pass
    assert "walls" in prof.total


def test_debug_group_helpers(fake):
    from pydoom.glrenderer2 import gl as gmod
    gmod.push_group("walls")
    gmod.pop_group()
    push = [a for c, a in fake.calls if c == "glPushDebugGroup"]
    assert push and push[0][1:] == (0, -1, "walls")
    assert any(c == "glPopDebugGroup" for c, _a in fake.calls)


def test_debug_group_failure_is_silent():
    from pydoom.glrenderer2 import gl as gmod
    gmod.cache_reset()

    class NoDebug:
        def __getattr__(self, name):
            raise AttributeError(name)

    gmod._real = NoDebug()
    try:
        gmod.push_group("walls")  # NOTE: no KHR_debug: no raise
        gmod.pop_group()
    finally:
        gmod._real = None
        gmod.cache_reset()


def test_lights_pack_caps_and_orders():
    from pydoom.glrenderer2 import lights as L
    assert L.MAX_LIGHTS == 8
    m = L.muzzle_light(1.0, 2.0, 41.0)
    assert m[:4] == (1.0, 2.0, 41.0, 144.0) and m[4:] == (1.0, 0.75, 0.45, 1.0)
    assert L.missile_light(0, 0, 0, "plasma")[4:7] == (0.35, 0.6, 1.0)
    assert L.missile_light(0, 0, 0, "bfg")[3] == 144.0
    assert L.missile_light(0, 0, 0, "weird")[4:7] == (1.0, 0.7, 0.35)
    entries = [m] + [L.missile_light(float(i), 0, 0) for i in range(10)]
    packed = L.pack_lights(entries)
    assert len(packed) == 8 and packed[0] == m  # NOTE: muzzle first


@requires_wad
def test_v2_dynlights_brighten_and_default_off(e1m1):
    """One muzzle-like light brightens nearby walls; default (no
    set_lights call) matches v1 exactly (parity tests pin that)."""
    _open_window()
    import pygame
    try:
        from pydoom.glrender.draw import FrameRenderer
        from pydoom.glrender.upload import GlResources
        from pydoom.glrenderer2 import lights as L
        from pydoom.glrenderer2.renderer import FrameRenderer2
        res = GlResources.create(e1m1["walls"], e1m1["planes"],
                                 e1m1["wtex"], e1m1["ftex"],
                                 e1m1["cmap"], e1m1["pal"])
        assert res is not None
        try:
            start = e1m1["start"]
            x, y = start.x << 16, start.y << 16
            viewz = 41 << 16
            f1 = FrameRenderer(res, 320, 200)
            try:
                f1.render(x, y, viewz, 0)
                ref = f1.readback()
            finally:
                f1.close()
            f2 = FrameRenderer2(res, 320, 200)
            try:
                f2.render(x, y, viewz, 0)
                dark = f2.readback()
                assert (dark == ref).all()  # NOTE: uDynNum 0 default
                f2.set_lights([L.muzzle_light(x / 65536.0,
                                              y / 65536.0, 41.0)])
                f2.render(x, y, viewz, 0)
                lit = f2.readback()
                assert not (lit == dark).all()  # NOTE: flash lights walls
                assert (lit.astype(int) >= dark.astype(int)).all()
            finally:
                f2.close()
                res.delete()
        except Exception:
            res.delete()
            raise
    finally:
        pygame.quit()


def test_facade_sampler_cached_per_unit(fake):
    gl.glBindSampler(0, 9)
    gl.glBindSampler(0, 9)  # NOTE: same unit/sampler: skipped
    gl.glBindSampler(1, 9)  # NOTE: other unit: rebinds
    gl.glBindSampler(0, 0)  # NOTE: unbind back to texture params
    got = [a for c, a in fake.calls if c == "glBindSampler"]
    assert got == [(0, 9), (1, 9), (0, 0)]


@requires_wad
def test_v2_linear_differs_but_renders(e1m1):
    """Linear walls/flats smooth texels: different pixels than NEAREST,
    same scene, no crash (sprites/sky stay NEAREST at v0)."""
    _open_window()
    import pygame
    try:
        from pydoom.glrender.upload import GlResources
        from pydoom.glrenderer2.renderer import FrameRenderer2
        res = GlResources.create(e1m1["walls"], e1m1["planes"],
                                 e1m1["wtex"], e1m1["ftex"],
                                 e1m1["cmap"], e1m1["pal"])
        assert res is not None
        try:
            start = e1m1["start"]
            x, y = start.x << 16, start.y << 16
            viewz = 41 << 16
            f_near = FrameRenderer2(res, 320, 200)
            try:
                f_near.render(x, y, viewz, 0)
                rgb_near = f_near.readback()
            finally:
                f_near.close()
            f_lin = FrameRenderer2(res, 320, 200, linear=True)
            try:
                f_lin.render(x, y, viewz, 0)
                rgb_lin = f_lin.readback()
            finally:
                f_lin.close()
                res.delete()
            assert not (rgb_lin == rgb_near).all()
        except Exception:
            res.delete()
            raise
    finally:
        pygame.quit()


@requires_wad
def test_v2_brightmaps_lift_lamp_pixels(e1m1):
    """Bright indices take the brightest ramp: with forced-dark sector
    lights aimed at a lamp wall, the flag lifts pixels; off matches
    v1 exactly."""
    _open_window()
    import pygame
    try:
        from pydoom.glrender.draw import FrameRenderer
        from pydoom.glrender.upload import GlResources
        from pydoom.glrenderer2.renderer import FrameRenderer2
        res = GlResources.create(e1m1["walls"], e1m1["planes"],
                                 e1m1["wtex"], e1m1["ftex"],
                                 e1m1["cmap"], e1m1["pal"],
                                 sector_lights=[0] * len(
                                     e1m1["game_map"].sectors))
        assert res is not None
        try:
            # NOTE: lamp wall (bright texels), seen from the south.
            x, y = 1632 << 16, -2100 << 16
            f1 = FrameRenderer(res, 320, 200)
            try:
                f1.render(x, y, 41 << 16, 0xC0000000)
                ref = f1.readback()
            finally:
                f1.close()
            f2 = FrameRenderer2(res, 320, 200)
            try:
                f2.set_brightmaps(False)
                f2.render(x, y, 41 << 16, 0xC0000000)
                dark = f2.readback()
                assert (dark == ref).all()  # NOTE: flag off = vanilla
                f2.set_brightmaps(True)
                f2.render(x, y, 41 << 16, 0xC0000000)
                lit = f2.readback()
                d = lit.astype(int) - dark.astype(int)
                assert int((d != 0).any(axis=2).sum()) > 0
                assert int(d.max()) > 0  # NOTE: lamps lift, never dim
            finally:
                f2.close()
        finally:
            res.delete()
    finally:
        pygame.quit()


@requires_wad
def test_v2_bloom_lifts_bright_pixels(e1m1):
    """Fullbright view: bloom adds blurred brights back over the
    world; off matches the default frame exactly."""
    _open_window()
    import pygame
    try:
        from pydoom.glrender.upload import GlResources
        from pydoom.glrenderer2.renderer import FrameRenderer2
        res = GlResources.create(e1m1["walls"], e1m1["planes"],
                                 e1m1["wtex"], e1m1["ftex"],
                                 e1m1["cmap"], e1m1["pal"])
        assert res is not None
        try:
            start = e1m1["start"]
            x, y = start.x << 16, start.y << 16
            viewz = 41 << 16
            f2 = FrameRenderer2(res, 320, 200, bloom=True)
            try:
                f2.render(x, y, viewz, 0, fullbright=True)
                lit = f2.readback()
                assert "bloom" in f2.prof.total
            finally:
                f2.close()
            f1 = FrameRenderer2(res, 320, 200)
            try:
                f1.render(x, y, viewz, 0, fullbright=True)
                plain = f1.readback()
            finally:
                f1.close()
                res.delete()
            d = lit.astype(int) - plain.astype(int)
            assert int((d != 0).any(axis=2).sum()) > 0
            assert int(d.min()) >= 0  # NOTE: additive only, never dims
        except Exception:
            res.delete()
            raise
    finally:
        pygame.quit()
