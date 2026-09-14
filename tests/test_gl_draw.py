"""Parity tests for glrender/draw.py (milestone H, phase 2).

GL walls+planes vs the software framebuffer on fixed E1M1 views, in
three layers (fullbright isolates texture mapping, lighting adds the
dot-formula light rows, extralight the muzzle-flash uniform). Sky and
masked pixels are excluded (Phase 3: no sky surface or masked pass
yet); everything else must match within tolerance.

Thresholds come from measured data (start room x 4 yaws): mapping
bugs collapse exact to <0.1 / mean >40 (flipU, du8 probes), while the
correct renderer scores exact >0.55 / mean <14, so exact > 0.45 and
mean < 16 have wide margins on both sides and stay driver-safe.
Residual dust is sub-texel rounding plus vanilla's linear scale
stepping vs true perspective (documented, shared by every GL port).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.draw import FrameRenderer
from pydoom.glrender.light import colormap_lut, palette_lut
from pydoom.glrender.preprocess import build_planes, build_walls
from pydoom.glrender.textures import (
    build_flat_textures,
    build_wall_textures,
    flatnums_used,
    wall_texnums_used,
)
from pydoom.glrender.upload import GlResources
from pydoom.mapdata import Map

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

VIEWS = (0x00000000, 0x40000000, 0x80000000, 0xC0000000)


def _open_window():
    """320x200 GL window for 1:1 software pixels (or skip: headless
    CI and GL<3 never enter)."""
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
    pal = palette_lut(bytes(wad.read_lump("PLAYPAL")))
    start = next(t for t in game_map.things if t.type == 1)
    return {"wad": wad, "game_map": game_map, "walls": walls,
            "planes": planes, "wtex": wtex, "ftex": ftex,
            "cmap": cmap, "pal": pal, "start": start}


def _software_view(e1m1, angle, fullbright=False, extra_light=0):
    """Software frame + sky/masked exclusion mask (Phase 3 pixels)."""
    import numpy as np

    from pydoom.renderer import Renderer
    wad, game_map, start = (e1m1["wad"], e1m1["game_map"],
                            e1m1["start"])
    from pydoom.textures import TextureManager
    renderer = Renderer(wad, TextureManager(wad))
    excl = np.zeros((200, 320), bool)
    o_sky, o_mpost = (Renderer._draw_sky_plane,
                      Renderer._draw_masked_posts)

    def rec_sky(self, pl):
        for x in range(pl.minx, pl.maxx + 1):
            yl, yh = pl.top[x + 1], pl.bottom[x + 1]
            if yh >= yl:
                excl[max(yl, 0):yh + 1, x] = True
        return o_sky(self, pl)

    def rec_mp(self, x, posts, *a):
        excl[:, x] = True  # NOTE: whole column (overdraws later)
        return o_mpost(self, x, posts, *a)

    Renderer._draw_sky_plane = rec_sky
    Renderer._draw_masked_posts = rec_mp
    try:
        fb = renderer.render_view(game_map, start.x << 16,
                                  start.y << 16, angle, mobjs=[],
                                  extra_light=extra_light,
                                  fullbright=fullbright)
        viewz = renderer.viewz
    finally:
        Renderer._draw_sky_plane = o_sky
        Renderer._draw_masked_posts = o_mpost
    return fb, excl, viewz


def _gl_view(e1m1, angle, viewz, fullbright=False, extra_light=0,
             idx_view=False, monkeypatch=None):
    """GL RGB frame (or texel-index frame with patched shaders)."""
    import numpy as np
    if idx_view:
        from pydoom.glrender import shaders
        old_wall = shaders.WALL_FRAG
        old_plane = shaders.PLANE_FRAG
        shaders.WALL_FRAG = old_wall.replace(
            "oColor = vec4(texelFetch(uPalette, ivec2(lit, 0), 0).rgb,"
            " 1.0);",
            "oColor = vec4(vec3(float(lit) / 255.0), 1.0);")
        shaders.PLANE_FRAG = old_plane.replace(
            "oColor = vec4(texelFetch(uPalette, ivec2(lit, 0), 0).rgb,"
            " 1.0);",
            "oColor = vec4(vec3(float(lit) / 255.0), 1.0);")
    try:
        res = GlResources.create(e1m1["walls"], e1m1["planes"],
                                 e1m1["wtex"], e1m1["ftex"],
                                 e1m1["cmap"], e1m1["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            start = e1m1["start"]
            fr.render(start.x << 16, start.y << 16, viewz, angle,
                      extra_light=extra_light, fullbright=fullbright)
            out = fr.readback()
        finally:
            fr.close()
            res.delete()
    finally:
        if idx_view:
            shaders.WALL_FRAG = old_wall
            shaders.PLANE_FRAG = old_plane
    if idx_view:
        return np.round(
            out[:, :, 0].astype(np.float64)).astype(np.int32)
    return out


def _metrics(rgb, ref, excl):
    import numpy as np
    diff = np.abs(rgb.astype(int) - ref.astype(int)).max(axis=2)
    masked = np.where(excl, 0, diff)
    n = int((~excl).sum())
    assert n > 60000  # NOTE: views stay exclusion-light
    return ((masked == 0).sum() / n, masked.sum() / n)


@requires_wad
def test_shaders_compile(e1m1):
    if _open_window() is None:
        return
    try:
        res = GlResources.create(e1m1["walls"], e1m1["planes"],
                                 e1m1["wtex"], e1m1["ftex"],
                                 e1m1["cmap"], e1m1["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        fr.close()
        res.delete()
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_parity_fullbright(e1m1):
    """Texture mapping only (lighting bypassed both sides)."""
    import numpy as np

    from pydoom.palette import load_playpal
    if _open_window() is None:
        return
    try:
        lut = np.array(load_playpal(
            e1m1["wad"].read_lump("PLAYPAL")), dtype=np.uint8)
        for angle in VIEWS:
            fb, excl, viewz = _software_view(e1m1, angle,
                                             fullbright=True)
            rgb = _gl_view(e1m1, angle, viewz, fullbright=True)
            exact, mean = _metrics(rgb, lut[fb], excl)
            assert exact > 0.45, (angle, exact)
            assert mean < 16.0, (angle, mean)
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_parity_fullbright_texel_neighbors(e1m1):
    """Mapping precision: GL texels land within 1px of software."""
    import numpy as np
    if _open_window() is None:
        return
    try:
        for angle in VIEWS:
            fb, excl, viewz = _software_view(e1m1, angle,
                                             fullbright=True)
            got = _gl_view(e1m1, angle, viewz, fullbright=True,
                           idx_view=True)
            want = fb.astype(np.int32)
            pad = np.pad(want, 1, mode="edge")
            near = np.zeros_like(want, bool)
            for dy in range(3):
                for dx in range(3):
                    near |= got == pad[dy:dy + 200, dx:dx + 320]
            near[excl] = True
            assert near.mean() > 0.85, (angle, near.mean())
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_parity_lighting(e1m1):
    """Dot-formula light rows vs software scalelight/zlight."""
    import numpy as np

    from pydoom.palette import load_playpal
    if _open_window() is None:
        return
    try:
        lut = np.array(load_playpal(
            e1m1["wad"].read_lump("PLAYPAL")), dtype=np.uint8)
        for angle in VIEWS:
            fb, excl, viewz = _software_view(e1m1, angle)
            rgb = _gl_view(e1m1, angle, viewz)
            exact, mean = _metrics(rgb, lut[fb], excl)
            assert exact > 0.45, (angle, exact)
            assert mean < 16.0, (angle, mean)
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_parity_extralight(e1m1):
    """Muzzle-flash uniform shifts rows like A_Light1/2 (and the
    uniform provably does something wherever software differs)."""
    import numpy as np

    from pydoom.palette import load_playpal
    if _open_window() is None:
        return
    try:
        lut = np.array(load_playpal(
            e1m1["wad"].read_lump("PLAYPAL")), dtype=np.uint8)
        for angle in VIEWS:
            fb, excl, viewz = _software_view(e1m1, angle,
                                             extra_light=2)
            rgb = _gl_view(e1m1, angle, viewz, extra_light=2)
            exact, mean = _metrics(rgb, lut[fb], excl)
            assert exact > 0.45, (angle, exact)
            assert mean < 16.0, (angle, mean)
            fb0, _, _ = _software_view(e1m1, angle)
            sw_diff = (lut[fb] != lut[fb0]).any(axis=2) & ~excl
            # NOTE: silhouette edges (linearization moves them ~1px)
            # may flip the winning surface: only flat-interior diffs
            # prove the uniform (E1M1 west view differs on one edge
            # column alone, with matching texels each side).
            flat = np.ones_like(sw_diff, bool)
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx or dy:
                        flat &= (np.roll(np.roll(fb, dy, 0), dx, 1)
                                 == fb)
                        flat &= (np.roll(np.roll(fb0, dy, 0), dx, 1)
                                 == fb0)
            if (sw_diff & flat).any():
                rgb0 = _gl_view(e1m1, angle, viewz)
                assert (rgb != rgb0).any(), angle  # NOTE: uniform live
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_render_deterministic(e1m1):
    import numpy as np
    if _open_window() is None:
        return
    try:
        _fb, _excl, viewz = _software_view(e1m1, VIEWS[0],
                                           fullbright=True)
        first = _gl_view(e1m1, VIEWS[0], viewz, fullbright=True)
        second = _gl_view(e1m1, VIEWS[0], viewz, fullbright=True)
        assert np.array_equal(first, second)
    finally:
        import pygame
        pygame.quit()
