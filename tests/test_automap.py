"""Tests for automap.py: C-semantics helpers, clipping, and E1M1 rendering."""

import os

import pytest

pygame = pytest.importorskip("pygame")

from pydoom.automap import (
    Automap,
    _cdiv,
    _cmod,
    load_playpal_from_wad,
    thing_degrees_to_bam,
)
from pydoom.fixed import FRACBITS, FRACUNIT
from pydoom.mapdata import Line, Map, MapThing, Vertex
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

PALETTE = [(i, i, i) for i in range(256)]  # synthetic grayscale for unit tests


def make_square_map() -> Map:
    s = 64 << FRACBITS
    v = [
        Vertex(x=-s, y=-s),
        Vertex(x=s, y=-s),
        Vertex(x=s, y=s),
        Vertex(x=-s, y=s),
    ]
    m = Map(marker="TEST", vertexes=v, things=[MapThing(0, 0, 90, 1, 0)])
    m.lines = [
        Line(v1=v[0], v2=v[1]),
        Line(v1=v[1], v2=v[2]),
        Line(v1=v[2], v2=v[3]),
        Line(v1=v[3], v2=v[0]),
    ]
    return m


def count_non_bg(surface: pygame.Surface, bg: tuple[int, int, int]) -> int:
    data = pygame.image.tobytes(surface, "RGB")
    b0, b1, b2 = bg
    n = 0
    for i in range(0, len(data), 3):
        if data[i] != b0 or data[i + 1] != b1 or data[i + 2] != b2:
            n += 1
    return n


def test_c_div_truncates_toward_zero():
    assert _cdiv(-7, 2) == -3  # C: -3, Python //: -4
    assert _cdiv(7, -2) == -3
    assert _cdiv(7, 2) == 3
    assert _cmod(-7, 2) == -1  # C: -1, Python %: 1
    assert _cmod(7, 2) == 1


def test_degrees_to_bam():
    assert thing_degrees_to_bam(90) == 0x40000000
    assert thing_degrees_to_bam(0) == 0
    assert thing_degrees_to_bam(180) == 0x80000000


def test_clip_inside_and_outside():
    am = Automap(make_square_map(), 320, 200, PALETTE)
    s = 64 << FRACBITS
    inside = am.clip_mline(-s, -s, s, s)
    assert inside is not None
    x1, y1, x2, y2 = inside
    assert 0 <= x1 < 320 and 0 <= y1 < 200
    assert 0 <= x2 < 320 and 0 <= y2 < 200
    far = 10000 << FRACBITS
    assert am.clip_mline(far, far, far + s, far + s) is None


def test_zoom_and_pan_ticker():
    am = Automap(make_square_map(), 320, 200, PALETTE)
    before = am.scale_mtof
    am.zoom_hold(zoom_in=True)
    am.ticker()
    am.zoom_release()
    assert am.scale_mtof > before
    am.toggle_follow()
    assert not am.followplayer
    am.key_down_pan(1, 0)
    am.ticker()
    am.key_up_pan()
    assert am.m_paninc == [0, 0]


def test_marks():
    am = Automap(make_square_map(), 320, 200, PALETTE)
    n = am.add_mark()
    assert am.markpoints[n] != (-1, -1)
    am.clear_marks()
    assert all(p == (-1, -1) for p in am.markpoints)


def test_draw_square_map():
    am = Automap(make_square_map(), 320, 200, PALETTE)
    surf = pygame.Surface((320, 200))
    am.draw(surf)
    assert count_non_bg(surf, PALETTE[0]) > 100


@requires_wad
def test_draw_e1m1():
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    palette = load_playpal_from_wad(wad)
    am = Automap(game_map, 640, 400, palette)
    surf = pygame.Surface((640, 400))
    am.draw(surf)
    base = count_non_bg(surf, palette[0])
    assert base > 5000
    # Deterministic: drawing twice gives identical pixels.
    surf2 = pygame.Surface((640, 400))
    am.draw(surf2)
    assert (
        pygame.image.tobytes(surf, "RGB")
        == pygame.image.tobytes(surf2, "RGB")
    )
    # Grid adds visible pixels.
    am.toggle_grid()
    am.draw(surf2)
    assert count_non_bg(surf2, palette[0]) > base
    # Things mode (cheat 2) renders without crashing.
    am.cycle_cheat()
    assert am.cheating == 2
    am.draw(surf2)


def test_draw_live_things():
    from types import SimpleNamespace
    am = Automap(make_square_map(), 320, 200, PALETTE)
    surf = pygame.Surface((320, 200))
    am.draw(surf)
    base = count_non_bg(surf, PALETTE[0])
    live = [SimpleNamespace(x=0, y=0, angle=0, dead=False)]
    surf2 = pygame.Surface((320, 200))
    am.draw(surf2, live)
    assert count_non_bg(surf2, PALETTE[0]) > base  # NOTE: dot added
    ghost = [SimpleNamespace(x=0, y=0, angle=0, dead=True)]
    surf3 = pygame.Surface((320, 200))
    am.draw(surf3, ghost)
    assert count_non_bg(surf3, PALETTE[0]) == base  # NOTE: corpses skip
