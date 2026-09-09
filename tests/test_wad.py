"""Tests for wad.py against the real WAD."""

import os

import pytest

from pydoom.wad import MAP_LUMP_ORDER, LumpNotFoundError, WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@pytest.fixture(scope="module")
def wad():
    return WadFile(WAD_PATH)


@requires_wad
def test_header_and_count(wad):
    assert len(wad) > 1000  # registered DOOM: thousands of lumps


@requires_wad
def test_lookup_case_insensitive_backwards(wad):
    assert wad.get_num_for_name("E1M1") == wad.get_num_for_name("e1m1")
    with pytest.raises(LumpNotFoundError):
        wad.get_num_for_name("THIS_LUMP_DOES_NOT_EXIST")
    assert wad.check_num_for_name("THIS_LUMP_DOES_NOT_EXIST") == -1


@requires_wad
def test_map_lump_order(wad):
    idx = wad.map_lump_indices("E1M1")
    assert list(idx) == MAP_LUMP_ORDER[1:]
    base = wad.get_num_for_name("E1M1")
    # Map lumps follow the marker in contiguous order.
    for i, kind in enumerate(MAP_LUMP_ORDER[1:]):
        assert idx[kind] == base + 1 + i
    # The geometry lumps are not empty.
    for kind in ("THINGS", "LINEDEFS", "SIDEDEFS", "VERTEXES", "SEGS",
                 "SSECTORS", "NODES", "SECTORS", "BLOCKMAP"):
        assert wad.lump_length(idx[kind]) > 0, kind


@requires_wad
def test_read_and_cache(wad):
    raw = wad.read_lump("E1M1")
    assert isinstance(raw, bytes)
    assert wad.cache_lump("E1M1") == raw
    assert wad.cache_lump("E1M1") is wad.cache_lump("E1M1")  # cached


@requires_wad
def test_list_maps(wad):
    maps = wad.list_maps()
    assert "E1M1" in maps
    assert len(maps) >= 9  # at least one full episode
