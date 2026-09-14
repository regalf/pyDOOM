"""Tests for glrender/sprites.py + sprite/masked draw (phase 3a/b).

The CPU feed must select the same patch/flip/light as the software
projector; GL sprites + masked mids must match the software frame
within tolerance (fuzz spectres excluded: MF_SHADOW needs the
backdrop target, next slice; E1M1 has none).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.draw import FrameRenderer
from pydoom.glrender.dynamic import sector_light_bases
from pydoom.glrender.light import colormap_lut
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

VIEWS = (0x00000000, 0x40000000, 0x80000000)


def load_e1m1():
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    return wad, texman, game_map, sky


def thing_mobjs(game_map, renderer):
    """Live-mobj stand-ins for map things (mirrors _project_things
    skips: unspawnable types and typeless visuals never project)."""
    from types import SimpleNamespace

    from pydoom.info import spawn_visual
    from pydoom.renderer import SKIP_THING_TYPES
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
    return mobjs


def make_feed(wad, texman):
    from pydoom.renderer import init_sprite_defs
    return SpriteFeed(sprites=init_sprite_defs(
        wad, texman.firstsprite, texman.lastsprite))


@requires_wad
def test_feed_matches_software_vissprites():
    """Same set/patch/flip/light as renderer.project_mobjs path."""
    from pydoom.renderer import Renderer
    wad, texman, game_map, _sky = load_e1m1()
    renderer = Renderer(wad, texman)
    feed = make_feed(wad, texman)
    mobjs = thing_mobjs(game_map, renderer)
    start = next(t for t in game_map.things if t.type == 1)
    for angle in VIEWS:
        renderer.render_view(game_map, start.x << 16,
                             start.y << 16, angle, mobjs=None)
        sw = [(v["patch"], v["xiscale"] < 0, v["colormap"],
               v["scale"]) for v in renderer.vissprites
              if v["colormap"] is not None]
        got = feed.project(mobjs, start.x << 16, start.y << 16,
                           angle, texman)
        # NOTE: fuzz spectres compare in the fuzz tests (backdrop
        # target); here both sides drop them.
        mine = [(b.lump, b.flip, b.colormap, b.depth) for b in got
                if not b.fuzz]
        assert len(mine) == len(sw), (angle, len(mine), len(sw))
        assert sorted(mine) == sorted(sw), angle


@requires_wad
def test_sprite_textures_match_patch_columns():
    """Sprite blobs carry the exact patch bytes-and-mask."""
    import numpy as np

    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    sets = build_sprite_textures(texman, range(texman.numsprites))
    assert len(sets.order) == texman.numsprites
    for spritenum, blob, (w, h) in zip(sets.order, sets.blobs,
                                       sets.sizes):
        patch = texman.get_sprite_patch(spritenum)
        assert (w, h) == (patch.width, patch.height)
        arr = np.frombuffer(blob, dtype=np.uint8).reshape(h, w, 2)
        for x in range(w):
            pixels, mask = patch.column_pixels(x)
            assert arr[:, x, 0].tobytes() == pixels
            assert arr[:, x, 1].tobytes() == (
                np.frombuffer(mask, dtype=np.uint8) * 255).tobytes()


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
def e1m1gl():
    wad, texman, game_map, sky = load_e1m1()
    walls = build_walls(game_map, texman, sky)
    planes = build_planes(game_map, sky)
    wtex = build_wall_textures(texman, wall_texnums_used(walls))
    ftex = build_flat_textures(texman, flatnums_used(planes))
    stex = build_sprite_textures(texman, range(texman.numsprites))
    cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
    pal = bytes(wad.read_lump("PLAYPAL"))
    return {"wad": wad, "texman": texman, "game_map": game_map,
            "walls": walls, "planes": planes, "wtex": wtex,
            "ftex": ftex, "stex": stex, "cmap": cmap, "pal": pal}


@requires_wad
def test_sprite_draw_parity(e1m1gl):
    """GL walls+planes+masked+sprites vs software with map things."""
    import numpy as np

    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    if _open_window() is None:
        return
    try:
        wad, texman, game_map = (e1m1gl["wad"], e1m1gl["texman"],
                                 e1m1gl["game_map"])
        renderer = Renderer(wad, texman)
        feed = make_feed(wad, texman)
        mobjs = thing_mobjs(game_map, renderer)
        res = GlResources.create(
            e1m1gl["walls"], e1m1gl["planes"], e1m1gl["wtex"],
            e1m1gl["ftex"], e1m1gl["cmap"], e1m1gl["pal"],
            sprite_tex=e1m1gl["stex"],
            sector_lights=sector_light_bases(game_map))
        assert res is not None
        assert len(res.sprite_textures) == texman.numsprites
        fr = FrameRenderer(res, 320, 200)
        try:
            lut = np.array(load_playpal(
                wad.read_lump("PLAYPAL")), dtype=np.uint8)
            start = next(t for t in game_map.things if t.type == 1)
            o_sky = Renderer._draw_sky_plane
            saw_sprites = False
            for angle in VIEWS:
                excl = np.zeros((200, 320), bool)

                def rec_sky(self, pl, _excl=excl):
                    for x in range(pl.minx, pl.maxx + 1):
                        yl, yh = pl.top[x + 1], pl.bottom[x + 1]
                        if yh >= yl:
                            _excl[max(yl, 0):yh + 1, x] = True
                    return o_sky(self, pl)

                Renderer._draw_sky_plane = rec_sky
                try:
                    fb = renderer.render_view(
                        game_map, start.x << 16, start.y << 16,
                        angle, mobjs=None)
                    viewz = renderer.viewz
                finally:
                    Renderer._draw_sky_plane = o_sky
                # NOTE: fuzz spectres (colormap None) stay software-only
                # this slice; mask their screen columns either side.
                for vis in renderer.vissprites:
                    if vis["colormap"] is None:
                        excl[:, vis["x1"]:vis["x2"] + 1] = True
                bbs = feed.project(mobjs, start.x << 16,
                                   start.y << 16, angle, texman)
                fr.render(start.x << 16, start.y << 16, viewz,
                          angle, sprites=bbs)
                rgb = fr.readback()
                if bbs:
                    # NOTE: sprites must rasterize (a no-op sprite
                    # pass would still pass parity on walls alone).
                    saw_sprites = True
                    fr.render(start.x << 16, start.y << 16, viewz,
                              angle)
                    assert (rgb != fr.readback()).any(), angle
                diff = np.abs(rgb.astype(int)
                              - lut[fb].astype(int)).max(axis=2)
                masked = np.where(excl, 0, diff)
                n = int((~excl).sum())
                assert n > 60000, angle
                assert (masked == 0).sum() / n > 0.45, angle
                assert masked.sum() / n < 16.0, angle
            assert saw_sprites  # NOTE: views must contain monsters
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_masked_synthetic_window():
    """Two-sided window with a holed midtexture over black: bars
    match, holes show through on both sides."""
    import numpy as np

    from pydoom.mapdata import Line, Sector, Seg, Side, Subsector, Vertex
    from pydoom.renderer import Renderer
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    mid = texman.texture_num_for_name("BRNBIGC")
    sky = texman.flat_num_for_name("F_SKY1")
    front = Sector(floorheight=0, ceilingheight=128 << 16,
                   floorpic=0, ceilingpic=1, lightlevel=160)
    back = Sector(floorheight=0, ceilingheight=128 << 16,
                  floorpic=0, ceilingpic=1, lightlevel=160)
    side = Side(textureoffset=0, rowoffset=0, toptexture=0,
                midtexture=mid, bottomtexture=0, sector=front)
    v1, v2 = Vertex(x=0, y=0), Vertex(x=128 << 16, y=0)
    line = Line(v1=v1, v2=v2, flags=4, frontsector=front,
                backsector=back)
    seg = Seg(v1=v1, v2=v2, angle=0, offset=0, linedef=line,
              sidenum=0, sidedef=side, frontsector=front,
              backsector=back)
    game_map = Map(marker="MINI", vertexes=[v1, v2],
                   sectors=[front, back], sides=[side],
                   lines=[line], segs=[seg],
                   subsectors=[Subsector(numlines=1, firstline=0,
                                         sector=front)])
    walls = build_walls(game_map, texman, sky)
    assert [q.tier for q in walls.quads] == ["masked"]
    if _open_window() is None:
        return
    try:
        from pydoom.glrender.preprocess import build_planes
        from pydoom.glrender.textures import build_wall_textures
        planes = build_planes(game_map, sky)
        assert planes.tris == []  # NOTE: no nodes, no planes
        wtex = build_wall_textures(texman,
                                   wall_texnums_used(walls))
        ftex = build_flat_textures(texman, [])
        cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
        pal = bytes(wad.read_lump("PLAYPAL"))
        res = GlResources.create(walls, planes, wtex, ftex, cmap,
                                 pal,
                                 sector_lights=sector_light_bases(
                                     game_map))
        assert res is not None
        fr = FrameRenderer(res, 320, 200)
        try:
            from pydoom.palette import load_playpal
            renderer = Renderer(wad, texman)
            viewz, angle = 64 << 16, 0x40000000
            fb = renderer.render_view(game_map, 64 << 16,
                                      -200 << 16, angle, viewz=viewz,
                                      mobjs=[])
            fr.render(64 << 16, -200 << 16, viewz, angle)
            rgb = fr.readback()
            lut = np.array(load_playpal(
                wad.read_lump("PLAYPAL")), dtype=np.uint8)
            diff = np.abs(rgb.astype(int)
                          - lut[fb].astype(int)).max(axis=2)
            # NOTE: bars cover the middle band; margins stay black
            # on both sides (no planes here by construction). The
            # close wall (128 wide at 200 units) shows the documented
            # linearization band mid-span (true perspective vs the
            # software's linear scale stepping), so this threshold is
            # about bars-vs-holes coverage, not texel parity.
            band = diff[40:160, :]
            assert (rgb[40:160].sum(axis=2) > 0).sum() > 100
            assert (band == 0).mean() > 0.70
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()
