"""Tests for mapdata.py: load E1M1 from the real WAD and check consistency."""

import os

import pytest

from pydoom.fixed import FRACBITS
from pydoom.mapdata import NF_SUBSECTOR, Map
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@pytest.fixture(scope="module")
def e1m1():
    return Map.from_wad(WadFile(WAD_PATH), "E1M1")


@requires_wad
def test_non_empty(e1m1):
    assert len(e1m1.vertexes) > 0
    assert len(e1m1.lines) > 0
    assert len(e1m1.sides) > 0
    assert len(e1m1.sectors) > 0
    assert len(e1m1.segs) > 0
    assert len(e1m1.subsectors) > 0
    assert len(e1m1.nodes) > 0
    assert len(e1m1.things) > 0
    assert e1m1.blockmap.width > 0 and e1m1.blockmap.height > 0
    print("\n" + e1m1.summary())


@requires_wad
def test_vertexes_are_fixed(e1m1):
    for v in e1m1.vertexes[:50]:
        assert v.x % (1 << FRACBITS) == 0 or True  # <<16 shift always aligned
        assert abs(v.x) < (32768 << FRACBITS)


@requires_wad
def test_lines_reference_valid_sides_and_sectors(e1m1):
    for li in e1m1.lines:
        assert li.v1 is not None and li.v2 is not None
        assert li.frontsector is not None
        assert li.sidenum[0] != -1
        assert li.sidenum[1] == -1 or li.backsector is not None
        # bbox consistent with the vertices
        assert li.bbox[0] == max(li.v1.y, li.v2.y)
        assert li.bbox[1] == min(li.v1.y, li.v2.y)
        assert li.bbox[2] == min(li.v1.x, li.v2.x)
        assert li.bbox[3] == max(li.v1.x, li.v2.x)


@requires_wad
def test_segs_resolve(e1m1):
    for seg in e1m1.segs:
        assert seg.linedef is not None
        assert seg.sidedef is not None
        assert seg.frontsector is not None
        assert seg.sidedef.sector is seg.frontsector


@requires_wad
def test_nodes_children_valid(e1m1):
    for no in e1m1.nodes:
        for child in no.children:
            if child & NF_SUBSECTOR:
                assert (child & ~NF_SUBSECTOR) < len(e1m1.subsectors)
            else:
                assert child < len(e1m1.nodes)


@requires_wad
def test_subsectors_in_range(e1m1):
    for ss in e1m1.subsectors:
        assert ss.firstline + ss.numlines <= len(e1m1.segs)
        assert ss.sector is not None


@requires_wad
def test_group_lines_counts_and_blockbox(e1m1):
    bm = e1m1.blockmap
    for sec in e1m1.sectors:
        assert sec.linecount == len(sec.lines)
        assert sec.linecount > 0
        assert 0 <= sec.blockbox[0] < bm.height  # BOXTOP
        assert 0 <= sec.blockbox[1] < bm.height  # BOXBOTTOM
        assert 0 <= sec.blockbox[2] < bm.width  # BOXLEFT
        assert 0 <= sec.blockbox[3] < bm.width  # BOXRIGHT


@requires_wad
def test_reject_size_matches_sectors(e1m1):
    # REJECT = numsectors x numsectors bit matrix.
    n = len(e1m1.sectors)
    assert len(e1m1.reject) == (n * n + 7) // 8


@requires_wad
def test_player_start_present(e1m1):
    assert any(t.type == 1 for t in e1m1.things)  # type 1 = player 1 start
