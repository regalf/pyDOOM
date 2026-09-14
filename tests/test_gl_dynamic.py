"""Dynamic-sector sync tests (milestone H, dynamic-sectors slice).

The GL world bakes wall quads + plane tris once per map, but doors,
plats, light flicker and switch swaps mutate the sim live. The diff
classifier (headless) plus the GEO/LIGHT re-upload paths (real
context, skipped headless) must track the software raster: a moving
door and a flickering light render like software after a viewer-style
sync, with zero GL work on steady frames.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.dynamic import (
    CLEAN,
    GEO,
    LIGHT,
    DynamicState,
    sector_light_bases,
    switch_pair_texnums,
)
from pydoom.glrender.preprocess import (
    build_planes,
    build_walls,
    emit_planes,
    leaf_sector_fans,
)
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


def load_e1m1():
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    return wad, texman, game_map, sky


def _mini_map():
    """Tiny sim-free map stand-in (no wad: take() only needs the
    sector/side lists plus empty BSP fields)."""
    from types import SimpleNamespace

    from pydoom.mapdata import Sector, Side
    sec = Sector(floorheight=0, ceilingheight=128 << 16,
                 lightlevel=160)
    side = Side(textureoffset=0, rowoffset=0, toptexture=1,
                midtexture=2, bottomtexture=3, sector=sec)
    return SimpleNamespace(sectors=[sec], sides=[side],
                           vertexes=[], nodes=[], subsectors=[])


def test_dynamic_module_needs_no_gl():
    """Diff/classify imports no GL bindings (order-independent: only
    new modules appearing during take/diff are checked)."""
    before = set(sys.modules)
    game_map = _mini_map()
    dyn = DynamicState.take(game_map)
    assert dyn.diff(game_map) == CLEAN
    new_gl = {m.split(".")[0] for m in set(sys.modules) - before}
    assert not (new_gl & {"OpenGL", "OpenGL_accelerate", "moderngl"})


def test_snapshot_starts_clean_and_resticks():
    """Fresh snapshot diffs CLEAN; a drift reports once, then the
    re-snap makes the next frame CLEAN again (level-movers pace)."""
    game_map = _mini_map()
    dyn = DynamicState.take(game_map)
    assert dyn.diff(game_map) == CLEAN
    game_map.sectors[0].lightlevel = 0
    assert dyn.diff(game_map) == LIGHT
    assert dyn.diff(game_map) == CLEAN


def test_diff_classifies_light_and_geo():
    """Only lightlevels -> LIGHT (texture-only refresh); any height,
    pic or sidedef texnum move -> GEO (VBO rebuild)."""
    game_map = _mini_map()
    dyn = DynamicState.take(game_map)
    game_map.sectors[0].lightlevel = 200
    assert dyn.diff(game_map) == LIGHT
    game_map.sectors[0].ceilingheight -= 32 << 16
    assert dyn.diff(game_map) == GEO
    game_map.sectors[0].floorheight += 8 << 16
    assert dyn.diff(game_map) == GEO
    game_map.sectors[0].floorpic = 5
    assert dyn.diff(game_map) == GEO
    game_map.sectors[0].ceilingpic = 6
    assert dyn.diff(game_map) == GEO
    game_map.sides[0].toptexture = 7
    assert dyn.diff(game_map) == GEO
    game_map.sides[0].midtexture = 8
    assert dyn.diff(game_map) == GEO
    game_map.sides[0].bottomtexture = 9
    assert dyn.diff(game_map) == GEO
    assert dyn.diff(game_map) == CLEAN


def test_sector_light_bases_rule():
    """Base lightnum is lightlevel>>4 clamped (the old baked
    PlaneTri.light rule, now a texture row)."""
    assert sector_light_bases(_mini_map()) == [10]
    game_map = _mini_map()
    game_map.sectors[0].lightlevel = 1000
    assert sector_light_bases(game_map) == [15]
    game_map.sectors[0].lightlevel = -40
    assert sector_light_bases(game_map) == [0]


@requires_wad
def test_emit_planes_matches_build():
    """Cached-fan re-emission is bit-identical to a full rebuild on
    an unmutated map (same order, same floats)."""
    wad, texman, game_map, sky = load_e1m1()
    del wad, texman
    fans = leaf_sector_fans(game_map)
    assert fans  # NOTE: topology cached once per map
    full = build_planes(game_map, sky).tris
    fast = emit_planes(fans, game_map, sky).tris
    key = lambda t: (t.sector, t.surface, t.flat, t.x1, t.y1,
                     t.x2, t.y2, t.x3, t.y3, t.z)
    assert [key(t) for t in fast] == [key(t) for t in full]


@requires_wad
def test_switch_pairs_cover_flips():
    """Every SW1*/SW2* sidedef flip target is either already built
    (used by some quad) or prebuilt via switch_pair_texnums, so a
    GEO refresh never meets a texture-less batch."""
    wad, texman, game_map, sky = load_e1m1()
    del wad
    pairs = switch_pair_texnums(game_map, texman)
    assert pairs  # NOTE: E1M1 has switch textures
    ntex = len(texman.textures)
    for texnum in pairs:
        assert 0 < texnum < ntex
        assert texman.textures[texnum].name.startswith(("SW1", "SW2"))
    used = set(wall_texnums_used(build_walls(game_map, texman, sky)))
    for side in game_map.sides:
        for texnum in (side.toptexture, side.midtexture,
                       side.bottomtexture):
            if not 0 < texnum < ntex:
                continue
            name = texman.textures[texnum].name
            other_name = None
            if name.startswith("SW1"):
                other_name = "SW2" + name[3:]
            elif name.startswith("SW2"):
                other_name = "SW1" + name[3:]
            if other_name is None:
                continue
            other = texman.check_texture_num_for_name(other_name)
            if other > 0:
                assert other in used | pairs, (name, other_name)


def _software_view(renderer, game_map, x, y, angle):
    """Software frame + sky/masked exclusion mask (draw-test pin)."""
    import numpy as np

    from pydoom.renderer import Renderer
    excl = np.zeros((200, 320), bool)
    o_sky, o_mpost = (Renderer._draw_sky_plane,
                      Renderer._draw_masked_posts)

    def rec_sky(self, pl):
        for xx in range(pl.minx, pl.maxx + 1):
            yl, yh = pl.top[xx + 1], pl.bottom[xx + 1]
            if yh >= yl:
                excl[max(yl, 0):yh + 1, xx] = True
        return o_sky(self, pl)

    def rec_mp(self, xx, posts, *a):
        excl[:, xx] = True  # NOTE: whole column (overdraws later)
        return o_mpost(self, xx, posts, *a)

    Renderer._draw_sky_plane = rec_sky
    Renderer._draw_masked_posts = rec_mp
    try:
        fb = renderer.render_view(game_map, x, y, angle, mobjs=[])
        viewz = renderer.viewz
    finally:
        Renderer._draw_sky_plane = o_sky
        Renderer._draw_masked_posts = o_mpost
    return fb, excl, viewz


def _metrics(rgb, ref, excl):
    import numpy as np
    diff = np.abs(rgb.astype(int) - ref.astype(int)).max(axis=2)
    masked = np.where(excl, 0, diff)
    n = int((~excl).sum())
    assert n > 60000  # NOTE: views stay exclusion-light
    return ((masked == 0).sum() / n, masked.sum() / n)


@requires_wad
def test_door_movement_parity():
    """A ceiling move (door-stroke-like GEO change) re-renders like
    software after a viewer-style GEO sync (frozen quads would fail
    parity: software alone moves ~40k px on this view). The move
    keeps the camera inside the room (72-8=64 > viewz 41)."""
    import numpy as np

    from pydoom.glrender.light import colormap_lut
    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    if _open_window() is None:
        return
    try:
        wad = WadFile(WAD_PATH)
        texman = TextureManager(wad)
        game_map = Map.from_wad(wad, "E1M1")
        texman.resolve_map(game_map)
        sky = texman.flat_num_for_name("F_SKY1")
        renderer = Renderer(wad, texman)
        dyn = DynamicState.take(game_map)
        walls = build_walls(game_map, texman, sky)
        planes = emit_planes(dyn.fans, game_map, sky)
        wtex = build_wall_textures(texman,
                                   wall_texnums_used(walls))
        ftex = build_flat_textures(texman, flatnums_used(planes))
        cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
        pal = bytes(wad.read_lump("PLAYPAL"))
        lut = np.array(load_playpal(
            wad.read_lump("PLAYPAL")), dtype=np.uint8)
        res = GlResources.create(
            walls, planes, wtex, ftex, cmap, pal,
            sector_lights=sector_light_bases(game_map))
        assert res is not None
        from pydoom.glrender.draw import FrameRenderer
        fr = FrameRenderer(res, 320, 200)
        try:
            start = next(t for t in game_map.things if t.type == 1)
            x, y, angle = (start.x << 16, start.y << 16, 0x0)
            fb0, excl, viewz = _software_view(renderer, game_map,
                                              x, y, angle)
            fr.render(x, y, viewz, angle)
            exact, mean = _metrics(fr.readback(), lut[fb0], excl)
            assert exact > 0.45 and mean < 16.0, (exact, mean)
            sec = renderer.sector_at(game_map, x, y).sector
            assert sec is not None
            sec.ceilingheight -= 8 << 16  # NOTE: GEO move, camera
            # stays inside (real doors move neighboring sectors;
            # same rebuild path)
            assert dyn.diff(game_map) == GEO
            fb1, excl1, _vz = _software_view(renderer, game_map,
                                             x, y, angle)
            moved = int((fb1 != fb0).sum())
            assert moved > 20000, moved  # NOTE: software animates
            walls2 = build_walls(game_map, texman, sky)
            planes2 = emit_planes(dyn.fans, game_map, sky)
            missing = (set(wall_texnums_used(walls2))
                       - set(res.wall_textures))
            if missing:
                res.upload_wall_textures(
                    build_wall_textures(texman, missing))
            res.reupload_walls(walls2)
            res.reupload_planes(planes2, res.flat_layers)
            res.upload_sector_lights(sector_light_bases(game_map))
            fr.render(x, y, viewz, angle)
            rgb1 = fr.readback()
            exact, mean = _metrics(rgb1, lut[fb1], excl1)
            assert exact > 0.45, (exact, mean)
            assert mean < 16.0, (exact, mean)
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_light_refresh_parity():
    """A sector lightlevel swing re-renders like software after a
    LIGHT-only sync (sector texture re-upload, VBOs untouched)."""
    import numpy as np

    from pydoom.glrender.light import colormap_lut
    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    if _open_window() is None:
        return
    try:
        wad = WadFile(WAD_PATH)
        texman = TextureManager(wad)
        game_map = Map.from_wad(wad, "E1M1")
        texman.resolve_map(game_map)
        sky = texman.flat_num_for_name("F_SKY1")
        renderer = Renderer(wad, texman)
        dyn = DynamicState.take(game_map)
        walls = build_walls(game_map, texman, sky)
        planes = emit_planes(dyn.fans, game_map, sky)
        wtex = build_wall_textures(texman,
                                   wall_texnums_used(walls))
        ftex = build_flat_textures(texman, flatnums_used(planes))
        cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
        pal = bytes(wad.read_lump("PLAYPAL"))
        lut = np.array(load_playpal(
            wad.read_lump("PLAYPAL")), dtype=np.uint8)
        res = GlResources.create(
            walls, planes, wtex, ftex, cmap, pal,
            sector_lights=sector_light_bases(game_map))
        assert res is not None
        from pydoom.glrender.draw import FrameRenderer
        fr = FrameRenderer(res, 320, 200)
        try:
            start = next(t for t in game_map.things if t.type == 1)
            x, y, angle = (start.x << 16, start.y << 16, 0x0)
            sec = renderer.sector_at(game_map, x, y).sector
            assert sec is not None
            sec.lightlevel = (0 if sec.lightlevel > 64 else 255)
            assert dyn.diff(game_map) == LIGHT
            fb1, excl1, viewz = _software_view(renderer, game_map,
                                               x, y, angle)
            res.upload_sector_lights(sector_light_bases(game_map))
            fr.render(x, y, viewz, angle)
            exact, mean = _metrics(fr.readback(), lut[fb1], excl1)
            assert exact > 0.45, (exact, mean)
            assert mean < 16.0, (exact, mean)
            assert dyn.diff(game_map) == CLEAN  # NOTE: steady: idle
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()
