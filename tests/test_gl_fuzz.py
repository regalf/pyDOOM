"""Fuzz parity tests (milestone H, phase 3, fuzz slice).

Spectres shade from the copied index backdrop through colormap row
6 (software FUZZOFFSETS cycling approximated by frame + row: the
shimmer pattern differs by design, so fuzz pixels assert color-set
membership while non-fuzz pixels hold the standard gate). E1M5 maze
views force grazing-incidence lighting on both sides, hence the
documented 0.40 floor (mapping stays isolated by the fullbright
draw tests).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.draw import FrameRenderer
from pydoom.glrender.light import colormap_lut, palette_lut
from pydoom.glrender.preprocess import build_planes, build_walls
from pydoom.glrender.sprites import SpriteFeed
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

# NOTE: E1M5 spectre views (fuzz pixels drawn by software).
VIEWS = ((128, 960, 0x00000000), (-192, 2048, 0x80000000))


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
def e1m5fuzz():
    from types import SimpleNamespace

    from pydoom.info import spawn_visual
    from pydoom.renderer import (
        SKIP_THING_TYPES,
        Renderer,
        init_sprite_defs,
    )
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M5")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    walls = build_walls(game_map, texman, sky)
    planes = build_planes(game_map, sky)
    renderer = Renderer(wad, texman)
    wtex = build_wall_textures(
        texman, set(wall_texnums_used(walls)) | {renderer.skytexture})
    ftex = build_flat_textures(texman, flatnums_used(planes))
    stex = build_sprite_textures(texman, range(texman.numsprites))
    cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
    pal = palette_lut(bytes(wad.read_lump("PLAYPAL")))
    feed = SpriteFeed(sprites=init_sprite_defs(
        wad, texman.firstsprite, texman.lastsprite))
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
        assert sub.sector is not None
        mobjs.append(SimpleNamespace(
            dead=False, state=1, flags=flags, sprite=sprite,
            frame=frame, x=thing.x << 16, y=thing.y << 16,
            z=sub.sector.floorheight,
            angle=((thing.angle % 360) * 0x100000000) // 360,
            sector=sub.sector))
    return {"wad": wad, "texman": texman, "game_map": game_map,
            "walls": walls, "planes": planes, "wtex": wtex,
            "ftex": ftex, "stex": stex, "cmap": cmap, "pal": pal,
            "feed": feed, "mobjs": mobjs,
            "skytex": renderer.skytexture}


def _render_both(e1m5fuzz, tx, ty, angle, frame_no):
    """Software frame + exact fuzz mask, GL frame at frame_no."""
    import numpy as np
    import pygame

    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    if _open_window() is None:
        return None
    try:
        wad, texman, game_map = (e1m5fuzz["wad"], e1m5fuzz["texman"],
                                 e1m5fuzz["game_map"])
        renderer = Renderer(wad, texman)
        fuzzmask = np.zeros((200, 320), bool)
        o_fuzz = Renderer._draw_fuzz_column

        def rec_fuzz(self, fx, yl, yh):
            fuzzmask[max(yl, 0):yh + 1, fx] = True
            return o_fuzz(self, fx, yl, yh)

        Renderer._draw_fuzz_column = rec_fuzz
        try:
            fb = renderer.render_view(game_map, tx << 16, ty << 16,
                                      angle, mobjs=None)
            viewz = renderer.viewz
        finally:
            Renderer._draw_fuzz_column = o_fuzz
        res = GlResources.create(
            e1m5fuzz["walls"], e1m5fuzz["planes"], e1m5fuzz["wtex"],
            e1m5fuzz["ftex"], e1m5fuzz["cmap"], e1m5fuzz["pal"],
            sprite_tex=e1m5fuzz["stex"])
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            bbs = e1m5fuzz["feed"].project(
                e1m5fuzz["mobjs"], tx << 16, ty << 16, angle,
                texman)
            skytex = e1m5fuzz["skytex"]
            fr.render(tx << 16, ty << 16, viewz, angle,
                      sprites=bbs,
                      sky=(res.wall_textures[skytex],
                           res.wall_info[skytex][1]),
                      frame_no=frame_no)
            rgb = fr.readback()
        finally:
            fr.close()
            res.delete()
        lut = np.array(load_playpal(wad.read_lump("PLAYPAL")),
                       dtype=np.uint8)
        return rgb, lut[fb], fuzzmask
    finally:
        pygame.quit()


@requires_wad
def test_fuzz_nonfuzz_gate(e1m5fuzz):
    import numpy as np
    for tx, ty, angle in VIEWS:
        rgb, ref, fuzzmask = _render_both(e1m5fuzz, tx, ty, angle, 7)
        assert fuzzmask.sum() > 500, (tx, ty, angle)  # NOTE: shimmer
        diff = np.abs(rgb.astype(int) - ref.astype(int)).max(axis=2)
        masked = np.where(fuzzmask, 0, diff)
        n = int((~fuzzmask).sum())
        assert (masked == 0).sum() / n > 0.40, (tx, ty, angle)
        assert masked.sum() / n < 16.0, (tx, ty, angle)


@requires_wad
def test_fuzz_colors_from_row6(e1m5fuzz):
    """Eroded shimmer pixels come from the colormap-6 palette set
    (backdrop index through row 6, like _draw_fuzz_column)."""
    import numpy as np

    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    from pydoom.palette import load_playpal
    lut = np.array(load_playpal(wad.read_lump("PLAYPAL")),
                   dtype=np.uint8)
    raw_cmap = bytes(wad.cache_lump("COLORMAP"))
    row6colors = {tuple(lut[raw_cmap[6 * 256 + i]])
                  for i in range(256)}
    tx, ty, angle = VIEWS[1]
    rgb, _ref, fuzzmask = _render_both(e1m5fuzz, tx, ty, angle, 7)
    inner = fuzzmask.copy()
    for dy in (-2, 0, 2):
        for dx in (-2, 0, 2):
            if dx or dy:
                inner &= np.roll(np.roll(fuzzmask, dy, 0), dx, 1)
    assert inner.sum() > 500  # NOTE: solid shimmer interior
    colors = set(map(tuple, rgb[inner].reshape(-1, 3).tolist()))
    assert colors <= row6colors, colors - row6colors


@requires_wad
def test_fuzz_deterministic_and_live(e1m5fuzz):
    """Same frame_no repeats identically; advancing it shimmers."""
    import numpy as np
    tx, ty, angle = VIEWS[1]
    rgb7, _ref, _mask = _render_both(e1m5fuzz, tx, ty, angle, 7)
    rgb7b, _, _ = _render_both(e1m5fuzz, tx, ty, angle, 7)
    assert np.array_equal(rgb7, rgb7b)
    rgb32, _, _ = _render_both(e1m5fuzz, tx, ty, angle, 32)
    assert (rgb7 != rgb32).any(axis=2).sum() > 100
