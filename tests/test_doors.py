"""Tests for doors.py: manual doors open, block, close; switches flip."""

import os

import pytest

from pydoom.doors import BUTTONTIME, DoorType, World, move_plane
from pydoom.fixed import FRACUNIT
from pydoom.mapdata import Map
from pydoom.physics import Mover, Physics, point_on_line_side
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def movers(world):
    """Door/floor/plat movers, excluding ambient light thinkers."""
    from pydoom.doors import FloorMover, Plat, VerticalDoor
    return [t for t in world.thinkers
            if isinstance(t, (VerticalDoor, FloorMover, Plat))]


@pytest.fixture(scope="module")
def setup():
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    return wad, texman, game_map


def front_spot(line, dist_units=48):
    """A point on the line's front side, dist units from its midpoint."""
    import math
    assert line.v1 is not None and line.v2 is not None
    mx = (line.v1.x + line.v2.x) // 2
    my = (line.v1.y + line.v2.y) // 2
    for deg in range(0, 360, 15):
        px = mx + int(math.cos(math.radians(deg)) * dist_units * FRACUNIT)
        py = my + int(math.sin(math.radians(deg)) * dist_units * FRACUNIT)
        if point_on_line_side(px, py, line) == 0:
            return px, py
    raise AssertionError("no front-side spot found")


@requires_wad
def test_manual_door_opens_walk_through_closes(setup):
    _, texman, game_map = setup
    phys = Physics(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines
                if li.special == 1 and li.backsector is not None)
    assert line.sidenum[1] != -1
    doorsec = game_map.sides[line.sidenum[1]].sector
    closed = doorsec.ceilingheight

    px, py = front_spot(line)
    mo = Mover(x=px, y=py, z=0)
    assert phys.check_position(mo, px, py).ok
    # Facing the door: angle from spot to midpoint.
    import math
    mx = (line.v1.x + line.v2.x) // 2
    my = (line.v1.y + line.v2.y) // 2
    ang = int((math.atan2(my - py, mx - px) / (2 * math.pi)
               * 0x100000000)) & 0xFFFFFFFF
    assert world.use_lines(px, py, ang, phys) is None
    assert len(movers(world)) == 1

    for _ in range(120):
        world.tick()
    assert doorsec.ceilingheight > closed  # door opened

    # Walk through the open doorway.
    tx, ty = 2 * mx - px, 2 * my - py
    ok, _ = phys.try_move(mo, tx, ty)
    assert ok, "open doorway still blocks"

    # Normal doors close again after the wait; thinker goes away.
    for _ in range(600):
        world.tick()
        if not movers(world):
            break
    assert not movers(world)
    assert doorsec.specialdata is None
    assert doorsec.ceilingheight == closed


@requires_wad
def test_locked_door_denied_without_key(setup):
    wad, texman, _ = setup
    for marker in wad.list_maps():
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        for line in game_map.lines:
            if line.special in (26, 27, 28, 32, 33, 34):
                world = World(game_map, texman)
                msg = world.vertical_door(line, True)
                assert msg is not None and "key" in msg
                assert not movers(world)
                return
    pytest.skip("no locked manual door in this WAD")


@requires_wad
def test_switch_flips_texture_and_opens(setup):
    wad, texman, _ = setup
    for marker in wad.list_maps():
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        phys = Physics(game_map)
        world = World(game_map, texman)
        for line in game_map.lines:
            if line.special not in (29, 103):
                continue
            if line.sidenum[0] == -1:
                continue
            side = game_map.sides[line.sidenum[0]]
            before = (side.toptexture, side.midtexture, side.bottomtexture)
            if not any(t in world.switchlist for t in before):
                continue
            msg = world.use_special_line(line, 0, True)
            assert msg is None
            after = (side.toptexture, side.midtexture, side.bottomtexture)
            assert after != before  # texture flipped...
            assert line.special == 0  # ...once (S1 clears special)
            assert len(movers(world)) == 1  # ...and the door runs
            return
    pytest.skip("no S1 door switch found")


@requires_wad
def test_button_reverts_after_buttontime(setup):
    wad, texman, _ = setup
    for marker in wad.list_maps():
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        world = World(game_map, texman)
        for line in game_map.lines:
            if line.special not in (61, 63):
                continue
            if line.sidenum[0] == -1:
                continue
            side = game_map.sides[line.sidenum[0]]
            before = (side.toptexture, side.midtexture, side.bottomtexture)
            if not any(t in world.switchlist for t in before):
                continue
            world.use_special_line(line, 0, True)
            assert (side.toptexture, side.midtexture,
                    side.bottomtexture) != before
            for _ in range(BUTTONTIME + 5):
                world.tick()
            assert (side.toptexture, side.midtexture,
                    side.bottomtexture) == before
            return
    pytest.skip("no button door switch found")


def test_move_plane_crushes_blocker():
    from pydoom.mapdata import Sector
    sec = Sector(floorheight=0, ceilingheight=56 * FRACUNIT)
    # Blocker exactly filling the sector: any drop crushes.
    blocker = (sec, 0, 56 * FRACUNIT)
    from pydoom.doors import PlaneResult
    assert move_plane(sec, 8 * FRACUNIT, 0, False, 1, -1, blocker) == (
        PlaneResult.CRUSHED)
    assert sec.ceilingheight == 56 * FRACUNIT  # reverted
    # No blocker, or blocker elsewhere: moves freely.
    assert move_plane(sec, 64 * FRACUNIT, 0, False, 1, -1, None) == (
        PlaneResult.PASTDEST)
    assert sec.ceilingheight == 0


@requires_wad
def test_light_thinkers_spawn_and_clear(setup):
    from pydoom.doors import FireFlicker, GlowLight, LightFlash, StrobeFlash
    wad, texman, _ = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    kinds = {type(t).__name__ for t in world.thinkers}
    assert {"LightFlash", "GlowLight", "StrobeFlash"} <= kinds
    leftovers = {s.special for s in game_map.sectors if s.special}
    assert not (leftovers & {1, 2, 3, 8, 12, 13, 17})  # consumed
    assert leftovers & {5, 7, 9}  # NOTE: damage/secrets stay live


@requires_wad
def test_strobe_toggles_min_max_only(setup):
    from pydoom.doors import StrobeFlash
    wad, texman, _ = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    strobes = [t for t in world.thinkers if isinstance(t, StrobeFlash)]
    assert strobes
    seen = set()
    for _ in range(300):
        world.tick()
        for st in strobes:
            assert st.sector.lightlevel in (st.minlight, st.maxlight)
            seen.add((id(st.sector), st.sector.lightlevel))
    assert any(len({lv for sid, lv in seen if sid == id(st.sector)}) > 1
               for st in strobes)  # every strobe actually blinks


@requires_wad
def test_glow_breathes_and_reverses(setup):
    from pydoom.doors import GlowLight
    wad, texman, _ = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    glows = [t for t in world.thinkers if isinstance(t, GlowLight)]
    assert glows
    g = glows[0]
    dirs = set()
    for _ in range(400):
        world.tick()
        assert g.minlight <= g.sector.lightlevel <= g.maxlight
        dirs.add(g.direction)
    assert dirs == {-1, 1}  # reversed at least once


@requires_wad
def test_flicker_stays_in_bounds(setup):
    from pydoom.doors import FireFlicker, LightFlash
    wad, texman, _ = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    flickers = [t for t in world.thinkers
                if isinstance(t, (LightFlash, FireFlicker))]
    assert flickers
    seen = set()
    for _ in range(400):
        world.tick()
        for f in flickers:
            assert f.minlight <= f.sector.lightlevel <= f.maxlight
            seen.add((id(f.sector), f.sector.lightlevel))
    assert any(len({lv for sid, lv in seen if sid == id(f.sector)}) > 1
               for f in flickers)
