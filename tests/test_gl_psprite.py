"""Psprite parity tests (milestone H, phase 3, psprite slice).

Weapon sprites overdraw raw indices at the vanilla anchor (no
projection involved), so gun pixels must match nearly exactly;
missing shareware lumps (plasma/BFG) skip on both sides.
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
    build_sprite_textures,
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

GUNS = (("PISG", "A", 0, 0), ("PISG", "B", 3, 5),
        ("SHTG", "A", -4, 12))


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
def e1m1ps():
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
    stex = build_sprite_textures(texman, range(texman.numsprites))
    cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
    pal = palette_lut(bytes(wad.read_lump("PLAYPAL")))
    return {"wad": wad, "texman": texman, "game_map": game_map,
            "walls": walls, "planes": planes, "wtex": wtex,
            "ftex": ftex, "stex": stex, "cmap": cmap, "pal": pal}


@requires_wad
def test_psprite_draw_parity(e1m1ps):
    import numpy as np

    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    if _open_window() is None:
        return
    try:
        wad, texman, game_map = (e1m1ps["wad"], e1m1ps["texman"],
                                 e1m1ps["game_map"])
        renderer = Renderer(wad, texman)
        start = next(t for t in game_map.things if t.type == 1)
        x, y, angle = start.x << 16, start.y << 16, 0x0
        fb_base = renderer.render_view(game_map, x, y, angle,
                                       mobjs=[], fullbright=True)
        res = GlResources.create(
            e1m1ps["walls"], e1m1ps["planes"], e1m1ps["wtex"],
            e1m1ps["ftex"], e1m1ps["cmap"], e1m1ps["pal"],
            sprite_tex=e1m1ps["stex"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            lut = np.array(load_playpal(
                wad.read_lump("PLAYPAL")), dtype=np.uint8)
            for base, frame, bobx, boby in GUNS:
                fb_gun = fb_base.copy()
                if not renderer.draw_psprite(fb_gun, base, bobx,
                                             boby, frame):
                    continue  # NOTE: missing lump skips both sides
                gunmask = fb_gun != fb_base
                assert gunmask.sum() > 500, (base, frame)
                spr = renderer.sprite_num_for_base(base, frame)
                patch = texman.get_sprite_patch(spr)
                ps = [(res.sprite_textures[spr], patch.width,
                       patch.height, patch.leftoffset,
                       patch.topoffset, bobx, boby)]
                fr.render(x, y, 41 << 16, angle, fullbright=True,
                          psprites=ps)
                rgb = fr.readback()
                diff = np.abs(rgb.astype(int)
                              - lut[fb_gun].astype(int)).max(axis=2)
                assert (diff[gunmask] == 0).mean() > 0.99, (
                    base, frame)
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()
