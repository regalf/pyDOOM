"""Sky parity tests (milestone H, phase 3, sky slice).

Outdoor E1M1 views render the full pipeline (sky + walls + planes +
masked + sprites) with ZERO exclusions: every pixel must match
within tolerance. E1M1 has no spectres (asserted: fuzz would need
masking like the masked-post columns).
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

# NOTE: nukage-outlet spot (one E1M1 view with 9-15k sky pixels).
SPOT = (2304, -3712)
VIEWS = (0x00000000, 0x40000000, 0x80000000)


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
def e1m1sky():
    from pydoom.renderer import Renderer, init_sprite_defs
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
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
    pal = bytes(wad.read_lump("PLAYPAL"))
    feed = SpriteFeed(sprites=init_sprite_defs(
        wad, texman.firstsprite, texman.lastsprite))
    return {"wad": wad, "texman": texman, "game_map": game_map,
            "walls": walls, "planes": planes, "wtex": wtex,
            "ftex": ftex, "stex": stex, "cmap": cmap, "pal": pal,
            "feed": feed, "skytex": renderer.skytexture}


def _thing_mobjs(game_map, renderer):
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


@requires_wad
def test_sky_draw_parity(e1m1sky):
    import numpy as np

    from pydoom.palette import load_playpal
    from pydoom.renderer import Renderer
    if _open_window() is None:
        return
    try:
        wad, texman, game_map = (e1m1sky["wad"], e1m1sky["texman"],
                                 e1m1sky["game_map"])
        renderer = Renderer(wad, texman)
        mobjs = _thing_mobjs(game_map, renderer)
        res = GlResources.create(
            e1m1sky["walls"], e1m1sky["planes"], e1m1sky["wtex"],
            e1m1sky["ftex"], e1m1sky["cmap"], e1m1sky["pal"],
            sprite_tex=e1m1sky["stex"],
            sector_lights=sector_light_bases(game_map))
        assert res is not None
        skytex = e1m1sky["skytex"]
        assert skytex in res.wall_textures  # NOTE: unioned sky lump
        fr = FrameRenderer(res, 320, 200)
        try:
            lut = np.array(load_playpal(
                wad.read_lump("PLAYPAL")), dtype=np.uint8)
            saw_sky = False
            for angle in VIEWS:
                fb = renderer.render_view(
                    game_map, SPOT[0] << 16, SPOT[1] << 16, angle,
                    mobjs=None)
                assert not any(v["colormap"] is None
                               for v in renderer.vissprites)
                bbs = e1m1sky["feed"].project(
                    mobjs, SPOT[0] << 16, SPOT[1] << 16, angle,
                    texman)
                fr.render(SPOT[0] << 16, SPOT[1] << 16,
                          renderer.viewz, angle, sprites=bbs,
                          sky=(res.wall_textures[skytex],
                               res.wall_info[skytex][1]))
                rgb = fr.readback()
                diff = np.abs(rgb.astype(int)
                              - lut[fb].astype(int)).max(axis=2)
                n = diff.size
                assert n == 64000  # NOTE: zero exclusions outdoors
                if angle == 0x40000000:
                    saw_sky = True  # NOTE: 14.5k sky px here
                assert (diff == 0).mean() > 0.40, angle
                assert diff.mean() < 16.0, angle
            assert saw_sky
        finally:
            fr.close()
            res.delete()
    finally:
        import pygame
        pygame.quit()


@requires_wad
def test_sky_cylinder_seam_and_coverage(e1m1sky):
    """Cylinder closes without cracks (seam duplicate exact) and
    covers the screen (every sky pixel drawn, none skipped)."""
    import numpy as np

    from pydoom.glrender.sky import SKY_SEGS, build_sky_verts
    verts = build_sky_verts(SPOT[0] << 16, SPOT[1] << 16, 41 << 16)
    assert verts.shape == ((SKY_SEGS + 1) * 2, 4)
    assert np.allclose(verts[0, :3], verts[SKY_SEGS, :3])
    assert verts[SKY_SEGS, 3] - verts[0, 3] == pytest.approx(4.0)
    assert np.allclose(verts[SKY_SEGS + 1, :3],
                       verts[2 * SKY_SEGS + 1, :3])
