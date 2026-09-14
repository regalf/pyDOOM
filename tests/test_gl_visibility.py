"""Portal-PVS tests (pvs slice).

The GL mesh draws everything unconditionally; the software BSP never
visits sectors unreachable through an open portal. visibility.py is
the conservative analog: flood from the camera sector through
two-sided lines with a live Z opening.
"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import pytest

from pydoom.glrender import visibility as glvis

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def _fake_map():
    """A-B open, B-C closed (C floor above B ceiling): C unreachable."""
    a = SimpleNamespace(floorheight=0, ceilingheight=128 << 16,
                        lines=[])
    b = SimpleNamespace(floorheight=0, ceilingheight=128 << 16,
                        lines=[])
    c = SimpleNamespace(floorheight=200 << 16, ceilingheight=300 << 16,
                        lines=[])
    ab = SimpleNamespace(frontsector=a, backsector=b)
    bc = SimpleNamespace(frontsector=b, backsector=c)
    a.lines = [ab]
    b.lines = [ab, bc]
    c.lines = [bc]
    game_map = SimpleNamespace(sectors=[a, b, c])
    return game_map


def test_open_portal_reaches_closed_blocks():
    """A sees B through the open line, C stays hidden behind solid."""
    game_map = _fake_map()
    assert glvis.visible_mask(game_map, 0) == [1, 1, 0]
    assert glvis.visible_set(game_map, 0) == {0, 1}
    assert glvis.visible_set(game_map, 2) == {2}


def test_unknown_start_shows_all():
    """Void/noclip edge (bad index) never blacks the frame."""
    game_map = _fake_map()
    assert glvis.visible_mask(game_map, 99) == [1, 1, 1]
    assert glvis.visible_mask(game_map, None) == [1, 1, 1]


@requires_wad
def test_e1m1_start_culls_unreachable():
    """Player start sees itself + neighbors, but not the whole map
    (closed doors block the flood at load)."""
    from pydoom.mapdata import Map
    from pydoom.renderer import Renderer
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    start = next(t for t in game_map.things if t.type == 1)
    sub = renderer.sector_at(game_map, start.x << 16,
                             start.y << 16)
    si = game_map.sectors.index(sub.sector)
    mask = glvis.visible_mask(game_map, si)
    assert len(mask) == len(game_map.sectors)
    assert mask[si] == 1
    assert 0 < sum(mask) < len(mask)  # NOTE: some, not all


@requires_wad
def test_gl_pvs_upload_and_render():
    """PVS mask uploads and renders: hidden sectors discard (sky
    shows through), all-visible restores the world."""
    import numpy as np
    import pygame

    from pydoom.glrender.draw import FrameRenderer
    from pydoom.glrender.dynamic import sector_light_bases
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
    from pydoom.renderer import Renderer
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    pygame.init()
    try:
        pygame.display.set_mode((320, 200),
                                pygame.OPENGL | pygame.DOUBLEBUF)
    except Exception:  # noqa: BLE001 - any display failure skips
        pygame.quit()
        pytest.skip("no GL context")
        return
    try:
        wad = WadFile(WAD_PATH)
        texman = TextureManager(wad)
        game_map = Map.from_wad(wad, "E1M1")
        texman.resolve_map(game_map)
        renderer = Renderer(wad, texman)
        walls = build_walls(game_map, texman, renderer.skyflatnum)
        planes = build_planes(game_map, renderer.skyflatnum)
        wtex = build_wall_textures(
            texman, set(wall_texnums_used(walls))
            | {renderer.skytexture})
        ftex = build_flat_textures(texman, flatnums_used(planes))
        res = GlResources.create(
            walls, planes, wtex, ftex,
            colormap_lut(bytes(wad.cache_lump("COLORMAP"))),
            bytes(wad.read_lump("PLAYPAL")),
            sector_lights=sector_light_bases(game_map))
        assert res is not None
        assert res.visible_tex != 0
        assert res.visible_count == len(game_map.sectors)
        fr = FrameRenderer(res, 320, 200)
        try:
            start = next(t for t in game_map.things if t.type == 1)
            x, y = start.x << 16, start.y << 16
            ang = (start.angle * 0x100000000 // 360) & 0xFFFFFFFF
            sub = renderer.sector_at(game_map, x, y)
            si = game_map.sectors.index(sub.sector)
            # NOTE: software view only for its viewz side effect (the
            # PVS-vs-full no-pop check below is the assertion).
            renderer.render_view(game_map, x, y, ang, mobjs=[])
            sky = (res.wall_textures[renderer.skytexture],
                   res.wall_info[renderer.skytexture][1])
            res.upload_visible([1] * len(game_map.sectors))
            fr.render(x, y, renderer.viewz, ang, sky=sky)
            full = fr.readback()
            mask = glvis.visible_mask(game_map, si)
            res.upload_visible(mask)
            fr.render(x, y, renderer.viewz, ang, sky=sky)
            pvs = fr.readback()
            # NOTE: start area is mostly connected: PVS keeps the
            # frame ~identical (no pop), it only drops unreachable
            # margin (secret-garden outsides from other spots).
            d = np.abs(full.astype(int) - pvs.astype(int)).max(
                axis=2)
            assert (d == 0).mean() > 0.99
            # NOTE: hiding everything leaves sky + void: proves the
            # discard path actually runs in-shader (sky cylinder is
            # global and never culled, walls/planes all drop).
            res.upload_visible([0] * len(game_map.sectors))
            fr.render(x, y, renderer.viewz, ang, sky=sky)
            cut = fr.readback()
            d2 = np.abs(full.astype(int) - cut.astype(int)).max(
                axis=2)
            assert (d2 > 0).mean() > 0.01
            res.upload_visible([1] * len(game_map.sectors))
        finally:
            fr.close()
            res.delete()
    finally:
        pygame.quit()
