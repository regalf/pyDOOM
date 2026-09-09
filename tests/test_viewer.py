"""Tests for the interactive viewer camera (tools/doom_view.py)."""

import math
import os
import sys

import pytest

pygame = pytest.importorskip("pygame")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

from doom_view import Camera

from pydoom.mapdata import Map
from pydoom.renderer import Renderer
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def test_bam_conversion():
    assert Camera(0, 0, 0, 0).bam == 0
    assert Camera(0, 0, 90, 0).bam == 0x40000000
    assert Camera(0, 0, 180, 0).bam == 0x80000000
    assert Camera(0, 0, -90, 0).bam == 0xC0000000


def test_move_and_turn():
    cam = Camera(0.0, 0.0, 0.0, 41.0)
    cam.move(10.0, 0.0)
    assert cam.x == pytest.approx(10.0) and cam.y == pytest.approx(0.0)
    cam.turn(math.pi / 2)
    cam.move(10.0, 0.0)
    assert cam.x == pytest.approx(10.0) and cam.y == pytest.approx(10.0)
    cam.move(0.0, 5.0)  # strafe right of north = east
    assert cam.x == pytest.approx(15.0) and cam.y == pytest.approx(10.0)


@requires_wad
def test_sector_at_matches_renderer():
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    start = next(t for t in game_map.things if t.type == 1)
    renderer.render_view(game_map, start.x << 16, start.y << 16, 0)
    a = renderer.sector_at(game_map, start.x << 16, start.y << 16)
    b = renderer.point_in_subsector(start.x << 16, start.y << 16)
    assert a is b
    assert a.sector is not None and a.sector.floorheight == 0
