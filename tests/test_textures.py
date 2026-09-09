"""Tests for textures.py against the real WAD."""

import os

import pytest

from pydoom.mapdata import Map
from pydoom.textures import TextureError, TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@pytest.fixture(scope="module")
def texman():
    return TextureManager(WadFile(WAD_PATH))


@requires_wad
def test_pnames_and_texture_counts(texman):
    assert len(texman.patch_lumps) > 100
    assert len(texman.textures) > 100  # TEXTURE1 only in this WAD
    assert all(t.width > 0 and t.height > 0 for t in texman.textures)


@requires_wad
def test_widthmask_is_power_of_two_minus_one(texman):
    for t in texman.textures:
        assert t.widthmask & (t.widthmask + 1) == 0
        assert t.widthmask < t.width or t.width == 1


@requires_wad
def test_name_lookup(texman):
    assert texman.check_texture_num_for_name("-") == 0  # NoTexture marker
    assert texman.check_texture_num_for_name("startan3") >= 0  # case-insensitive
    assert texman.check_texture_num_for_name("NOPE_XYZ") == -1
    with pytest.raises(TextureError):
        texman.texture_num_for_name("NOPE_XYZ")
    with pytest.raises(TextureError):
        texman.flat_num_for_name("NOPE_XYZ")
    assert texman.flat_num_for_name("FLOOR4_8") >= 0


@requires_wad
def test_column_decode(texman):
    tnum = texman.texture_num_for_name("STARTAN3")
    tex = texman.textures[tnum]
    for x in (0, tex.width // 2, tex.width - 1):
        pixels, mask = texman.get_column(tnum, x)
        assert len(pixels) == tex.height
        assert len(mask) == tex.height
        assert set(mask) <= {0, 1}
    # Widthmask wrapping.
    assert texman.get_column(tnum, tex.width) == texman.get_column(tnum, 0)


@requires_wad
def test_flats_are_64x64(texman):
    assert texman.numflats > 0
    flat = texman.get_flat(texman.flat_num_for_name("FLOOR4_8"))
    assert len(flat) == 64 * 64


@requires_wad
def test_resolve_e1m1(texman):
    game_map = Map.from_wad(texman.wad, "E1M1")
    texman.resolve_map(game_map)
    for side in game_map.sides:
        assert side.toptexture >= 0
        assert side.midtexture >= 0
        assert side.bottomtexture >= 0
    for sector in game_map.sectors:
        assert 0 <= sector.floorpic < texman.numflats
        assert 0 <= sector.ceilingpic < texman.numflats


@requires_wad
def test_sprites_present(texman):
    assert texman.numsprites > 0
    patch = texman.get_sprite_patch(0)
    assert patch.width > 0 and patch.height > 0
