"""Present-path tests (milestone H, phase 4).

Overlay art must never emit the reserved transparent index 255
(headless); the GL present pipeline (world + overlay + version +
automap) must match the software presentation within tolerance
(real context, skipped headless).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.draw import FrameRenderer
from pydoom.glrender.light import colormap_lut
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


def test_overlay_art_never_emits_255():
    """Index 255 stays transparent: no overlay draw may emit it."""
    import numpy as np

    from pydoom.menu import Menu, Settings
    from pydoom.player import PlayerState
    from pydoom.renderer import Renderer
    from pydoom.statusbar import draw_status_bar
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    menu = Menu(wad, Settings(), 2, 0)
    ps = PlayerState()
    ps.ammo = [50, 8, 0, 0]
    ps.armorpoints = 100
    ps.armortype = 1
    ps.keys = 1 | 16
    ps.weapons |= 1 << 2

    def fresh():
        return np.zeros((200, 320), dtype=np.uint8)

    states = {}
    fb = fresh()
    draw_status_bar(renderer, fb, ps, 100)
    states["statusbar"] = fb
    fb = fresh()
    menu.draw_text(fb, "YOU GOT THE SHOTGUN!", 8, 8)
    states["text"] = fb
    fb = fresh()
    menu.draw(fb)
    states["menu"] = fb
    fb = fresh()
    menu.draw_title(fb)
    states["title"] = fb
    fb = fresh()
    menu.mode = "readthis"
    menu.draw(fb)
    states["readthis"] = fb
    fb = fresh()
    menu.mode = "confirm"
    menu.confirm_text = "are you sure?"
    menu.draw(fb)
    states["confirm"] = fb
    fb = fresh()
    menu.mode = "message"
    menu.message_text = "hello"
    menu.draw(fb)
    states["message"] = fb
    fb = fresh()
    menu.mode = "menu"
    menu.current = "options"
    menu.draw(fb)
    states["options"] = fb
    menu.current = "sound"
    fb = fresh()
    menu.draw(fb)
    states["sound"] = fb
    fb = fresh()
    pw = menu._patch_w("M_PAUSE")
    menu._blit("M_PAUSE", fb, (320 - pw) // 2, 4)
    states["pause"] = fb
    for name, frame in states.items():
        assert (frame == 255).sum() == 0, name
        assert (frame != 0).sum() > 50, name  # NOTE: drew something


@pytest.fixture(scope="module")
def e1m1present():
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
    return {"wad": wad, "texman": texman, "game_map": game_map,
            "walls": walls, "planes": planes, "wtex": wtex,
            "ftex": ftex, "cmap": cmap, "pal": pal}


def _overlay_level(e1m1present):
    """255-fill overlay fb with status bar + message (viewer-like)."""
    import numpy as np

    from pydoom.menu import Menu, Settings
    from pydoom.player import PlayerState
    from pydoom.renderer import Renderer
    from pydoom.statusbar import draw_status_bar
    wad, texman = (e1m1present["wad"], e1m1present["texman"])
    renderer = Renderer(wad, texman)
    menu = Menu(wad, Settings(), 2, 0)
    ps = PlayerState()
    ps.ammo = [50, 8, 0, 0]
    ps.armorpoints = 100
    ps.weapons |= 1 << 2
    fb = np.full((200, 320), 255, dtype=np.uint8)
    draw_status_bar(renderer, fb, ps, 100)
    menu.draw_text(fb, "YOU GOT THE SHOTGUN!", 0, 0)
    return fb


@requires_wad
def test_present_level_parity(e1m1present):
    """World + overlay composite vs software presentation."""
    import numpy as np

    from pydoom.palette import load_playpal_index
    from pydoom.renderer import Renderer
    if _open_window() is None:
        return
    try:
        wad, texman, game_map = (e1m1present["wad"],
                                 e1m1present["texman"],
                                 e1m1present["game_map"])
        renderer = Renderer(wad, texman)
        start = next(t for t in game_map.things if t.type == 1)
        x, y, angle = start.x << 16, start.y << 16, 0x0
        res = GlResources.create(
            e1m1present["walls"], e1m1present["planes"],
            e1m1present["wtex"], e1m1present["ftex"],
            e1m1present["cmap"], e1m1present["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            playpal = bytes(wad.read_lump("PLAYPAL"))
            for pal_idx in (0, 5):
                lut = np.array(load_playpal_index(playpal, pal_idx),
                               dtype=np.uint8)
                fb_view = renderer.render_view(game_map, x, y, angle,
                                               mobjs=[])
                overlay = _overlay_level(e1m1present)
                expected = np.where(
                    overlay[:, :, None] == 255, lut[fb_view],
                    lut[overlay])
                fr.render(x, y, 41 << 16, angle, pal_index=pal_idx)
                fr.blit_world()
                fr.present_overlay(overlay, pal_idx)
                rgb = fr.readback_window()
                diff = np.abs(rgb.astype(int)
                              - expected.astype(int)).max(axis=2)
                assert (diff == 0).mean() > 0.45, pal_idx
                assert diff.mean() < 16.0, pal_idx
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_present_menu_parity(e1m1present):
    """Sparse menu art floats over the live GL world."""
    import numpy as np

    from pydoom.menu import Menu, Settings
    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    if _open_window() is None:
        return
    try:
        wad, texman, game_map = (e1m1present["wad"],
                                 e1m1present["texman"],
                                 e1m1present["game_map"])
        renderer = Renderer(wad, texman)
        menu = Menu(wad, Settings(), 2, 0)
        start = next(t for t in game_map.things if t.type == 1)
        x, y, angle = start.x << 16, start.y << 16, 0x0
        res = GlResources.create(
            e1m1present["walls"], e1m1present["planes"],
            e1m1present["wtex"], e1m1present["ftex"],
            e1m1present["cmap"], e1m1present["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            lut = np.array(load_playpal(
                wad.read_lump("PLAYPAL")), dtype=np.uint8)
            fb_view = renderer.render_view(game_map, x, y, angle,
                                           mobjs=[])
            overlay = np.full((200, 320), 255, dtype=np.uint8)
            menu.draw(overlay)
            expected = np.where(overlay[:, :, None] == 255,
                                lut[fb_view], lut[overlay])
            fr.render(x, y, 41 << 16, angle)
            fr.blit_world()
            fr.present_overlay(overlay, 0)
            rgb = fr.readback_window()
            diff = np.abs(rgb.astype(int)
                          - expected.astype(int)).max(axis=2)
            assert (diff == 0).mean() > 0.45
            assert diff.mean() < 16.0
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_gun_under_statusbar_layering(e1m1present):
    """Gun quad draws before the overlay (the bar covers its base,
    like the software blit order)."""
    import numpy as np

    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    if _open_window() is None:
        return
    try:
        wad, texman, game_map = (e1m1present["wad"],
                                 e1m1present["texman"],
                                 e1m1present["game_map"])
        renderer = Renderer(wad, texman)
        start = next(t for t in game_map.things if t.type == 1)
        x, y, angle = start.x << 16, start.y << 16, 0x0
        res = GlResources.create(
            e1m1present["walls"], e1m1present["planes"],
            e1m1present["wtex"], e1m1present["ftex"],
            e1m1present["cmap"], e1m1present["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            lut = np.array(load_playpal(
                wad.read_lump("PLAYPAL")), dtype=np.uint8)
            fb_view = renderer.render_view(game_map, x, y, angle,
                                           mobjs=[])
            renderer.draw_psprite(fb_view, "PISG", 0, 0, "A")
            overlay = np.full((200, 320), 255, dtype=np.uint8)
            from pydoom.menu import Menu, Settings
            from pydoom.player import PlayerState
            from pydoom.statusbar import draw_status_bar
            menu = Menu(wad, Settings(), 2, 0)
            ps = PlayerState()
            ps.ammo = [50, 8, 0, 0]
            ps.armorpoints = 100
            ps.weapons |= 1 << 2
            draw_status_bar(renderer, overlay, ps, 100)
            menu.draw_text(overlay, "YOU GOT THE SHOTGUN!", 0, 0)
            expected = np.where(overlay[:, :, None] == 255,
                                lut[fb_view], lut[overlay])
            spr = renderer.sprite_num_for_base("PISG", "A")
            patch = texman.get_sprite_patch(spr)
            # NOTE: sprite set lacks gun lumps here; upload one shot.
            from pydoom.glrender.textures import build_sprite_textures
            one = build_sprite_textures(texman, [spr])
            from OpenGL import GL
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            w, h = one.sizes[0]
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RG8, w, h,
                            0, GL.GL_RG, GL.GL_UNSIGNED_BYTE,
                            one.blobs[0])
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            fr.render(x, y, 41 << 16, angle,
                      psprites=[(tex, w, h, patch.leftoffset,
                                 patch.topoffset, 0, 0)])
            fr.blit_world()
            fr.present_overlay(overlay, 0)
            rgb = fr.readback_window()
            diff = np.abs(rgb.astype(int)
                          - expected.astype(int)).max(axis=2)
            bar = np.zeros((200, 320), bool)
            bar[168:] = True
            assert (diff[bar] == 0).mean() > 0.90  # NOTE: bar opaque
            assert (diff[~bar] == 0).mean() > 0.45
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()
    """Build-tag quad matches an equivalent CPU composite."""
    import numpy as np
    import pygame
    if _open_window() is None:
        return
    try:
        font = pygame.font.SysFont(None, 18)
        img = font.render("v0.10", True, (255, 255, 255))
        w, h = img.get_width(), img.get_height()
        arr = np.frombuffer(pygame.image.tobytes(img, "RGBA"),
                            dtype=np.uint8).reshape(h, w, 4).copy()
        # NOTE: software surface-alpha-96 blit math, byte for byte.
        src_a = (arr[:, :, 3].astype(np.uint16) * 96 // 255)
        res = GlResources.create(
            e1m1present["walls"], e1m1present["planes"],
            e1m1present["wtex"], e1m1present["ftex"],
            e1m1present["cmap"], e1m1present["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            arr[:, :, 3] = src_a.astype(np.uint8)
            tid = fr.upload_text(arr.tobytes(), w, h)
            fr.clear_window()
            fr.draw_text_quad(tid, 320 - 8 - w, 8, w, h)
            rgb = fr.readback_window()
            dst = np.zeros((h, w, 3), dtype=np.float64)
            src = arr[:, :, :3].astype(np.float64)
            a = (src_a.astype(np.float64) / 255.0)[:, :, None]
            expected = np.round(src * a + dst * (1.0 - a)).astype(
                np.uint8)
            got = rgb[8:8 + h, 320 - 8 - w:320 - 8]
            assert got.shape == expected.shape
            assert np.abs(got.astype(int)
                          - expected.astype(int)).mean() < 2.0
            assert (got.sum(axis=2) > 0).sum() > 50  # NOTE: drew text
        finally:
            fr.close()
            res.delete()
    finally:
        pygame.quit()


@requires_wad
def test_version_quad(e1m1present):
    """Build-tag quad matches an equivalent CPU composite."""
    import numpy as np
    import pygame
    if _open_window() is None:
        return
    try:
        font = pygame.font.SysFont(None, 18)
        img = font.render("v0.10", True, (255, 255, 255))
        w, h = img.get_width(), img.get_height()
        arr = np.frombuffer(pygame.image.tobytes(img, "RGBA"),
                            dtype=np.uint8).reshape(h, w, 4).copy()
        arr[:, :, 3] = (arr[:, :, 3].astype(np.uint16)
                        * 96 // 255).astype(np.uint8)
        res = GlResources.create(
            e1m1present["walls"], e1m1present["planes"],
            e1m1present["wtex"], e1m1present["ftex"],
            e1m1present["cmap"], e1m1present["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            tid = fr.upload_text(arr.tobytes(), w, h)
            fr.clear_window()
            fr.draw_text_quad(tid, 320 - 8 - w, 8, w, h)
            rgb = fr.readback_window()
            dst = np.zeros((h, w, 3), dtype=np.float64)
            src = arr[:, :, :3].astype(np.float64)
            a = (arr[:, :, 3].astype(np.float64) / 255.0)[:, :, None]
            expected = np.round(src * a + dst * (1.0 - a)).astype(
                np.uint8)
            got = rgb[8:8 + h, 320 - 8 - w:320 - 8]
            assert got.shape == expected.shape
            assert np.abs(got.astype(int)
                          - expected.astype(int)).mean() < 2.0
            assert (got.sum(axis=2) > 0).sum() > 50  # NOTE: drew text
        finally:
            fr.close()
            res.delete()
    finally:
        pygame.quit()


@requires_wad
def test_automap_parity(e1m1present):
    """Collected segments rasterize like the software draw."""
    import numpy as np
    import pygame

    from pydoom.automap import Automap
    from pydoom.palette import load_playpal
    if _open_window() is None:
        return
    try:
        wad, game_map = (e1m1present["wad"],
                         e1m1present["game_map"])
        lut = np.array(load_playpal(wad.read_lump("PLAYPAL")),
                       dtype=np.uint8)
        amap = Automap(game_map, 320, 200, lut.tolist())
        start = next(t for t in game_map.things if t.type == 1)
        amap.plr_x, amap.plr_y = start.x << 16, start.y << 16
        amap.plr_angle = 0x40000000
        segs = amap.collect_segments(None)
        assert len(segs) > 100
        surf = pygame.Surface((320, 200))
        amap.draw(surf, None)
        ref = np.transpose(np.array(pygame.surfarray.pixels3d(surf),
                                    dtype=np.uint8), (1, 0, 2)).copy()
        res = GlResources.create(
            e1m1present["walls"], e1m1present["planes"],
            e1m1present["wtex"], e1m1present["ftex"],
            e1m1present["cmap"], e1m1present["pal"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            fr.clear_window()
            fr.draw_automap(segs)
            rgb = fr.readback_window()
        finally:
            fr.close()
            res.delete()
        gl_line = rgb.sum(axis=2) > 0
        sw_line = ref.sum(axis=2) > 0

        def dilate(mask):
            out = mask.copy()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    if dx or dy:
                        out |= np.roll(np.roll(mask, dy, 0), dx, 1)
            return out

        assert ((gl_line & dilate(sw_line)).sum()
                / max(gl_line.sum(), 1) > 0.95)
        assert ((sw_line & dilate(gl_line)).sum()
                / max(sw_line.sum(), 1) > 0.95)
    finally:
        pygame.quit()
