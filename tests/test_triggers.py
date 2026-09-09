"""Tests for walk-over triggers, floors, plats and lights."""

import math
import os

import pytest

from pydoom.doors import BUTTONTIME, World
from pydoom.fixed import FRACUNIT
from pydoom.mapdata import Map
from pydoom.physics import Mover, Physics, point_on_line_side
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@pytest.fixture(scope="module")
def setup():
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    return wad, texman


def cross_line(ph, world, game_map, line, dist_units=24):
    """Walk across a trigger line like the viewer does."""
    assert line.v1 is not None and line.v2 is not None
    mx = (line.v1.x + line.v2.x) // 2
    my = (line.v1.y + line.v2.y) // 2
    for deg in range(0, 360, 15):
        px = mx + int(math.cos(math.radians(deg)) * dist_units * FRACUNIT)
        py = my + int(math.sin(math.radians(deg)) * dist_units * FRACUNIT)
        mo = Mover(x=px, y=py, z=0)
        res = ph.check_position(mo, px, py)
        if not res.ok:
            continue
        mo.z = mo.floorz = res.floorz
        mo.ceilingz = res.ceilingz
        tx, ty = 2 * mx - px, 2 * my - py
        ok, crossed = ph.try_move(mo, tx, ty)
        if ok and line in crossed:
            msgs = [m for m in
                    (world.cross_special_line(ld, True) for ld in crossed)
                    if m is not None]
            return mo, msgs
    raise AssertionError(f"could not cross line {line.special}")


@requires_wad
def test_walk_over_lift_cycles(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    ph = Physics(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 88)
    mo, msgs = cross_line(ph, world, game_map, line)
    assert msgs == []
    assert len(world.thinkers) == 1
    plat = world.thinkers[0]
    home = plat.sector.floorheight
    assert plat.status == "down"
    for _ in range(1200):
        world.tick()
        if not world.thinkers:
            break
    assert not world.thinkers  # down-wait-up-stay finished
    assert plat.sector.floorheight == home  # back where it started
    assert plat.sector.specialdata is None


@requires_wad
def test_walk_over_turbo_lower(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    ph = Physics(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 36)
    sec = world.find_sectors_from_tag(line.tag)[0]
    want = world.find_highest_floor(sec)
    if want != sec.floorheight:
        want += 8 * FRACUNIT
    mo, msgs = cross_line(ph, world, game_map, line)
    assert msgs == []
    assert line.special == 0  # W1 clears
    for _ in range(600):
        world.tick()
        if not world.thinkers:
            break
    assert sec.floorheight == want


@requires_wad
def test_exit_switch_requests_transition(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 11)
    assert world.use_special_line(line, 0, True) is None
    assert world.exit_kind == "normal"  # S1 exit, like G_ExitLevel
    assert line.special == 0  # one-shot switch spent


@requires_wad
def test_secret_exit_switch_requests_secret(setup):
    from pydoom.flow import next_map
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M3")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 51)
    world.use_special_line(line, 0, True)
    assert world.exit_kind == "secret"  # like G_SecretExitLevel
    assert next_map("E1M3", True) == "E1M9"
    assert next_map("E1M3", False) == "E1M4"
    assert next_map("E1M8", False) is None  # episode complete
    assert next_map("E1M9", False) == "E1M4"  # back from secret


@requires_wad
def test_light_turn_on_sets_tagged_sectors(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M9")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    tagged = [s for s in game_map.sectors if s.tag]
    assert tagged
    sec = tagged[0]
    world.light_turn_on(
        type("FakeLine", (), {"tag": sec.tag})(), 255)
    assert sec.lightlevel == 255


@requires_wad
def test_stop_parked_plat(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 88)
    world.do_plat(line, "downWaitUpStay", 0)
    assert len(world.activeplats) == 1
    world.stop_plat(line)
    assert world.activeplats[0].status == "in_stasis"
    height = world.activeplats[0].sector.floorheight
    for _ in range(50):
        world.tick()
    assert world.activeplats[0].sector.floorheight == height  # parked
