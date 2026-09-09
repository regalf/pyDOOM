"""Tests for visplanes, spans and sky (r_plane.c / r_sky.c ports)."""

import os

import numpy as np
import pytest

from pydoom.automap import thing_degrees_to_bam
from pydoom.mapdata import Map
from pydoom.renderer import Renderer
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

_U32 = 0xFFFFFFFF


@pytest.fixture(scope="module")
def setup():
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    start = next(t for t in game_map.things if t.type == 1)
    return renderer, texman, game_map, start


@requires_wad
def test_planes_cover_frame(setup):
    renderer, _, game_map, start = setup
    fb = renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    drawn = [pl for pl in renderer.visplanes if pl.minx <= pl.maxx]
    assert len(drawn) > 0
    assert int(np.count_nonzero(fb)) > 50000
    # Floor band shows flat texture variety, not a solid color.
    assert len(np.unique(fb[120:199, :])) > 10
    # Ceiling band is drawn too (not left black).
    assert len(np.unique(fb[0:60, :])) > 3


@requires_wad
def test_planes_deterministic(setup):
    renderer, _, game_map, start = setup
    args = (game_map, start.x << 16, start.y << 16,
            thing_degrees_to_bam(start.angle))
    first = renderer.render_view(*args)
    nplanes = len(renderer.visplanes)
    second = renderer.render_view(*args)
    assert np.array_equal(first, second)
    assert len(renderer.visplanes) == nplanes


@requires_wad
def test_sky_texel_lands(setup):
    # Outdoor yard view: independently recompute one sky pixel.
    renderer, texman, game_map, _ = setup
    fb = renderer.render_view(
        game_map, 1901 << 16, -3228 << 16, thing_degrees_to_bam(180)
    )
    sky = [
        pl for pl in renderer.visplanes
        if pl.picnum == renderer.skyflatnum and pl.minx <= pl.maxx
    ]
    assert sky
    pl = sky[0]
    x0 = next(
        x for x in range(pl.minx, pl.maxx + 1)
        if pl.top[x + 1] <= pl.bottom[x + 1]
    )
    y0 = pl.top[x0 + 1]
    angle = ((renderer.viewangle + renderer.xtoviewangle[x0]) & _U32) >> 22
    column = texman.get_column(renderer.skytexture, angle)[0]
    frac = 100 * 65536 + (y0 - 100) * 65536  # SKYTEXTUREMID, iscale
    want = renderer.colormaps[column[(frac >> 16) % len(column)]]
    assert fb[y0, x0] == want
