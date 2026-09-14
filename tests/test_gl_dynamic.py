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
    refresh_planes,
    refresh_walls,
    sec_tri_positions,
    seg_quad_positions,
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


def test_diff_records_changed_sets():
    """diff() reports which sectors/sides moved (incremental GEO)."""
    game_map = _mini_map()
    dyn = DynamicState.take(game_map)
    assert dyn.diff(game_map) == CLEAN
    assert dyn.changed_sectors == set()
    assert dyn.changed_sides == set()
    game_map.sectors[0].ceilingheight -= 1
    assert dyn.diff(game_map) == GEO
    assert dyn.changed_sectors == {0}
    assert dyn.changed_sides == set()
    game_map.sides[0].midtexture = 99
    assert dyn.diff(game_map) == GEO
    assert dyn.changed_sectors == set()
    assert dyn.changed_sides == {0}
    assert dyn.diff(game_map) == CLEAN


def _quad_key(q):
    return (q.seg, q.tier, q.texnum, q.z_bottom, q.z_top, q.u1,
            q.u2, q.texbase, q.sector, q.tweak, q.twosided,
            q.nx, q.ny, q.x1, q.y1, q.x2, q.y2)


def _tri_key(t):
    return (t.sector, t.surface, t.flat, t.x1, t.y1, t.x2, t.y2,
            t.x3, t.y3, t.z)


@requires_wad
def test_refresh_walls_matches_full_build():
    """Sliding a ceiling (door travel) refreshes spans in place,
    quad-for-quad identical to a full rebuild, fast path all along."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    dyn = DynamicState.take(game_map)
    walls = build_walls(game_map, texman, sky)
    seg_quadpos = seg_quad_positions(walls)
    sec = game_map.sectors[14]
    for _ in range(5):
        sec.ceilingheight -= 8 << 16  # NOTE: mid-travel, tiers persist
        assert dyn.diff(game_map) == GEO
        stable, texmoved, touched = refresh_walls(
            walls, seg_quadpos, game_map, texman, sky,
            dyn.changed_sectors, dyn.changed_sides)
        assert stable and not texmoved
        assert touched  # NOTE: the mover's segs moved
        full = build_walls(game_map, texman, sky)
        assert [_quad_key(q) for q in walls.quads] == [
            _quad_key(q) for q in full.quads]


@requires_wad
def test_refresh_walls_slow_on_topology_change():
    """Crushing a sector past degenerate kills tiers: refresh says
    slow (single-sided mids of the crushed sector vanish), and the
    slow full rebuild reflects exactly that."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    dyn = DynamicState.take(game_map)
    walls = build_walls(game_map, texman, sky)
    seg_quadpos = seg_quad_positions(walls)
    sec = game_map.sectors[14]
    cand = [si for si, s in enumerate(game_map.segs)
            if s.frontsector is sec and s.backsector is None
            and s.sidedef.midtexture]
    assert cand, "need a crushed single-sided mid to vanish"
    si = cand[0]
    assert [walls.quads[q].tier for q in seg_quadpos[si]] == ["mid"]
    sec.floorheight = sec.ceilingheight + (8 << 16)
    assert dyn.diff(game_map) == GEO
    stable, _, _ = refresh_walls(
        walls, seg_quadpos, game_map, texman, sky,
        dyn.changed_sectors, dyn.changed_sides)
    assert not stable
    slow = build_walls(game_map, texman, sky)
    assert [q.tier for q in slow.quads if q.seg == si] == []


@requires_wad
def test_refresh_planes_matches_emit():
    """Sliding heights re-emits only moved sectors, tri-for-tri
    identical to a full emission (counts stable by construction)."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    dyn = DynamicState.take(game_map)
    planes = emit_planes(dyn.fans, game_map, sky)
    sec_tripos = sec_tri_positions(planes)
    sec = game_map.sectors[14]
    for _ in range(3):
        sec.ceilingheight -= 8 << 16
        sec.floorheight += 4 << 16
        assert dyn.diff(game_map) == GEO
        touched = refresh_planes(planes, dyn.fans, game_map, sky,
                                 dyn.changed_sectors, sec_tripos)
        assert touched
        full = emit_planes(dyn.fans, game_map, sky).tris
        assert [_tri_key(t) for t in planes.tris] == [
            _tri_key(t) for t in full]


@requires_wad
def test_row_helpers_match_interleave():
    """_wall_rows/_plane_rows equal the full-interleave slices (the
    fast path patches exactly what a rebuild would upload)."""
    import numpy as np

    from pydoom.glrender.upload import GlResources
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    walls = build_walls(game_map, texman, sky)
    inter = GlResources._wall_interleaved(walls)
    for q, quad in enumerate(walls.quads):
        want = GlResources._wall_rows(quad)
        assert np.array_equal(inter[q * 4:q * 4 + 4], want), q
    dyn = DynamicState.take(game_map)
    planes = emit_planes(dyn.fans, game_map, sky)
    layers = dict.fromkeys(
        {t.flat for t in planes.tris if t.flat >= 0}, 3)
    inter, _ = GlResources._plane_interleaved(planes, layers)
    rowpos = GlResources._plane_row_positions(planes, layers)
    for t, tri in enumerate(planes.tris):
        row = rowpos[t]
        if tri.flat < 0:  # NOTE: sky-cut tris own no VBO rows
            assert row is None
            continue
        assert row is not None
        assert np.array_equal(
            inter[row:row + 3],
            GlResources._plane_rows(tri, 3)), t


@requires_wad
def test_fast_geo_parity():
    """Door-stroke GEO change through the FAST path (span refresh +
    VBO row patch, no rebuild) renders like software."""
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
        seg_quadpos = seg_quad_positions(walls)
        sec_tripos = sec_tri_positions(planes)
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
            sec.ceilingheight -= 8 << 16
            assert dyn.diff(game_map) == GEO
            stable, texmoved, touched = refresh_walls(
                walls, seg_quadpos, game_map, texman, sky,
                dyn.changed_sectors, dyn.changed_sides)
            assert stable and not texmoved and touched
            touched_tris = refresh_planes(
                planes, dyn.fans, game_map, sky,
                dyn.changed_sectors, sec_tripos)
            assert touched_tris  # NOTE: mover sector has fan tris
            res.reupload_walls_fast(walls.quads, touched)
            assert res.reupload_planes_fast(
                planes.tris, res.flat_layers, touched_tris)
            res.upload_sector_lights(sector_light_bases(game_map))
            fb1, excl1, viewz = _software_view(renderer, game_map,
                                               x, y, angle)
            fr.render(x, y, viewz, angle)
            exact, mean = _metrics(fr.readback(), lut[fb1], excl1)
            assert exact > 0.45, (exact, mean)
            assert mean < 16.0, (exact, mean)
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_texnum_flip_replans_index():
    """Sidedef texnum swap (switch-style) flags texmoved: spans stay,
    only the IBO needs re-planning, then pixels match software."""
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
        seg_quadpos = seg_quad_positions(walls)
        used = sorted(t for t in wall_texnums_used(walls) if t > 0)
        assert len(used) > 1
        # NOTE: nonzero->nonzero flip (tier sets provably persist:
        # guards key on truthiness, heights untouched).
        pick = next(s for s in game_map.sides if s.midtexture > 0)
        old_mid = pick.midtexture
        new_mid = used[0] if used[0] != old_mid else used[1]
        side_idx = game_map.sides.index(pick)
        pick.midtexture = new_mid
        assert dyn.diff(game_map) == GEO
        assert dyn.changed_sides == {side_idx}
        before = [(q.z_bottom, q.z_top) for q in walls.quads]
        stable, texmoved, touched = refresh_walls(
            walls, seg_quadpos, game_map, texman, sky,
            dyn.changed_sectors, dyn.changed_sides)
        assert stable and texmoved and touched
        assert [(q.z_bottom, q.z_top) for q in walls.quads] == before
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
            # NOTE: resources created post-flip, so only the IBO
            # replan path is exercised (VBO spans identical).
            res.replan_wall_index(walls.quads)
            start = next(t for t in game_map.things if t.type == 1)
            x, y, angle = (start.x << 16, start.y << 16, 0x0)
            fb1, excl1, viewz = _software_view(renderer, game_map,
                                               x, y, angle)
            fr.render(x, y, viewz, angle)
            exact, mean = _metrics(fr.readback(), lut[fb1], excl1)
            assert exact > 0.45, (exact, mean)
            assert mean < 16.0, (exact, mean)
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()
