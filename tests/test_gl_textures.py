"""Tests for glrender/textures.py (milestone H, phase 1, textures slice).

Wall blobs must carry the exact bytes-and-mask the software drawer
samples (get_column), flats the verbatim lump bytes; the manifest
covers every texture the geometry references.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.preprocess import build_planes, build_walls
from pydoom.glrender.textures import (
    all_flatnums,
    build_flat_textures,
    build_wall_textures,
    flatnums_used,
    wall_texnums_used,
)
from pydoom.mapdata import Map
from pydoom.textures import FLAT_SIZE

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")
REG_WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "doom.wad")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)
requires_reg = pytest.mark.skipif(
    not os.path.exists(REG_WAD_PATH), reason="doom.wad not found"
)


def load_e1m1():
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    walls = build_walls(game_map, texman, sky)
    planes = build_planes(game_map, sky)
    return texman, walls, planes


def test_build_needs_no_gl():
    """Texture assembly imports no GL bindings (order-independent)."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    before = set(sys.modules)
    build_wall_textures(texman, [1])
    # NOTE: flatnum 0 is the empty F1_START marker in DOOM1.WAD (real
    # sectors never reference it); assemble a real flat instead.
    build_flat_textures(texman,
                        [texman.flat_num_for_name("FLOOR4_8")])
    new_gl = {m.split(".")[0] for m in set(sys.modules) - before}
    assert not (new_gl & {"OpenGL", "OpenGL_accelerate", "moderngl"})


@requires_wad
def test_wall_blobs_match_software_columns():
    """Every byte and mask bit equals what get_column hands the
    software column drawer (R channel = index, G channel = 0/255)."""
    import numpy as np
    texman, walls, _planes = load_e1m1()
    texnums = wall_texnums_used(walls)
    assert texnums and 0 not in texnums  # NOTE: "-" never stored
    sets = build_wall_textures(texman, texnums)
    assert sets.order == sorted(texnums)
    for texnum, blob, (w, h), wrap in zip(sets.order, sets.blobs,
                                          sets.sizes, sets.wraps):
        tex = texman.textures[texnum]
        assert (w, h) == (tex.width, tex.height)
        assert wrap == tex.widthmask + 1  # NOTE: the col&mask rule
        arr = np.frombuffer(blob, dtype=np.uint8).reshape(h, w, 2)
        for x in range(w):
            pixels, mask = texman.get_column(texnum, x)
            assert arr[:, x, 0].tobytes() == pixels
            assert arr[:, x, 1].tobytes() == (
                np.frombuffer(mask, dtype=np.uint8) * 255).tobytes()


@requires_wad
def test_wall_manifest_covers_quads():
    texman, walls, _planes = load_e1m1()
    sets = build_wall_textures(texman, wall_texnums_used(walls))
    assert {q.texnum for q in walls.quads} <= set(sets.order)
    assert [sets.index_of[t] for t in sets.order] == list(
        range(len(sets.order)))


@requires_wad
def test_opaque_and_masked_channels():
    """STARTAN3 is fully opaque; E1M1's masked tracks keep real holes
    (BRNBIGC columns are mostly gap)."""
    import numpy as np
    texman, walls, _planes = load_e1m1()
    sets = build_wall_textures(texman, wall_texnums_used(walls))
    opaque = sets.blobs[sets.index_of[
        texman.texture_num_for_name("STARTAN3")]]
    arr = np.frombuffer(opaque, dtype=np.uint8)
    assert (arr[1::2] == 255).all()
    masked_names = {texman.textures[q.texnum].name for q in walls.quads
                    if q.tier == "masked"}
    assert "BRNBIGC" in masked_names
    holed = sets.blobs[sets.index_of[
        texman.texture_num_for_name("BRNBIGC")]]
    g = np.frombuffer(holed, dtype=np.uint8)[1::2]
    assert (g == 0).any() and (g == 255).any()


@requires_wad
def test_flat_blob_is_verbatim_lump_bytes():
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    flat = texman.flat_num_for_name("FLOOR4_8")
    sets = build_flat_textures(texman, [flat])
    assert sets.order == [flat] and sets.index_of == {flat: 0}
    assert sets.blob == texman.get_flat(flat)
    assert len(sets.blob) == FLAT_SIZE


@requires_wad
def test_flat_manifest_covers_tris():
    texman, _walls, planes = load_e1m1()
    assert -1 in {t.flat for t in planes.tris}  # NOTE: sky tagged
    used = flatnums_used(planes)
    assert -1 not in used
    sets = build_flat_textures(texman, used)
    assert {t.flat for t in planes.tris if t.flat >= 0} <= set(
        sets.order)
    assert len(sets.blob) == len(used) * FLAT_SIZE


@requires_wad
def test_all_flatnums_skips_empty_markers():
    """all_flatnums covers every used flat but no zero-length marker
    lump (dynamic sectors prebuild these: donut swaps resolve)."""
    texman, _walls, planes = load_e1m1()
    allf = all_flatnums(texman)
    assert set(flatnums_used(planes)) <= set(allf)
    sets = build_flat_textures(texman, allf)  # NOTE: must not assert
    assert len(sets.blob) == len(allf) * FLAT_SIZE
    assert len(allf) > len(flatnums_used(planes))  # NOTE: spares exist


@requires_wad
def test_e1m1_golden_texture_counts():
    """Regression tripwire: E1M1 references 32 wall textures and
    21 flats."""
    _texman, walls, planes = load_e1m1()
    assert len(wall_texnums_used(walls)) == 32
    assert len(flatnums_used(planes)) == 21


@requires_wad
def test_all_shareware_maps_assemble():
    """Every E1 map's referenced textures assemble (no missing
    patches/columns anywhere in the shareware set)."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    for marker in wad.list_maps():
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        sky = texman.flat_num_for_name("F_SKY1")
        walls = build_walls(game_map, texman, sky)
        planes = build_planes(game_map, sky)
        build_wall_textures(texman, wall_texnums_used(walls))
        build_flat_textures(texman, flatnums_used(planes))


@requires_reg
def test_registered_maps_assemble():
    """Same gate over E1-E3 (local doom.wad, skipped in CI)."""
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(REG_WAD_PATH)
    texman = TextureManager(wad)
    for marker in wad.list_maps():
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        sky = texman.flat_num_for_name("F_SKY1")
        walls = build_walls(game_map, texman, sky)
        planes = build_planes(game_map, sky)
        build_wall_textures(texman, wall_texnums_used(walls))
        build_flat_textures(texman, flatnums_used(planes))
