"""Tests for physics.py: position checks, sliding, traversals."""

import os

import pytest

from pydoom.fixed import FRACUNIT
from pydoom.mapdata import Line, Map, Vertex
from pydoom.physics import (
    Mover,
    Physics,
    aprox_distance,
    point_on_line_side,
)
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@pytest.fixture(scope="module")
def setup():
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    return game_map, Physics(game_map)


def walk_until_blocked(ph, x, y, dx, dy, step_units=8):
    step = step_units << 16
    mo = Mover(x=x, y=y, z=0)
    n = 0
    while n < 500:
        if not ph.try_move(mo, mo.x + dx * step, mo.y + dy * step)[0]:
            break
        n += 1
    return mo, n


@requires_wad
def test_start_position_is_open(setup):
    game_map, ph = setup
    start = next(t for t in game_map.things if t.type == 1)
    mo = Mover(x=start.x << 16, y=start.y << 16, z=0)
    res = ph.check_position(mo, mo.x, mo.y)
    assert res.ok
    assert res.floorz == 0
    assert res.ceilingz - res.floorz >= mo.height


@requires_wad
def test_walls_block_on_all_sides(setup):
    _, ph = setup
    mo, _ = walk_until_blocked(ph, 1056 << 16, -3616 << 16, -1, 0)
    assert (mo.x >> 16, mo.y >> 16) == (960, -3616)  # west pillar face
    mo, _ = walk_until_blocked(ph, 1056 << 16, -3616 << 16, 0, -1)
    assert (mo.x >> 16, mo.y >> 16) == (1056, -3664)  # south wall
    mo, _ = walk_until_blocked(ph, 1056 << 16, -3616 << 16, 1, 0)
    assert (mo.x >> 16, mo.y >> 16) == (1152, -3616)  # east pillar face
    mo, _ = walk_until_blocked(ph, 1056 << 16, -3616 << 16, 0, 1)
    assert (mo.x >> 16, mo.y >> 16) == (1056, -2896)  # north, past the door


@requires_wad
def test_slide_never_penetrates(setup):
    _, ph = setup
    mo = Mover(x=1056 << 16, y=-3616 << 16, z=0)
    mo.momx, mo.momy = 30 << 16, 30 << 16
    for _ in range(20):
        ph.slide_move(mo)
        assert ph.check_position(mo, mo.x, mo.y).ok
    # Made progress north-east along the divider wall.
    assert mo.y > -3616 << 16
    assert mo.x >= 1056 << 16


@requires_wad
def test_step_up_small_step_down_tall_wall(setup):
    game_map, ph = setup
    stepped, blocked = 0, 0
    for line in game_map.lines:
        if line.backsector is None or line.frontsector is None:
            continue
        front, back = line.frontsector, line.backsector
        diff = back.floorheight - front.floorheight
        if not (0 < diff <= 24 * FRACUNIT or diff >= 72 * FRACUNIT):
            continue
        assert line.v1 is not None and line.v2 is not None
        mx = (line.v1.x + line.v2.x) // 2
        my = (line.v1.y + line.v2.y) // 2
        # Find a standing spot on the front side.
        base = None
        for ang in range(0, 360, 45):
            import math
            px = mx + int(math.cos(math.radians(ang)) * 20 * FRACUNIT)
            py = my + int(math.sin(math.radians(ang)) * 20 * FRACUNIT)
            if point_on_line_side(px, py, line) != 0:
                continue
            mo = Mover(x=px, y=py, z=front.floorheight)
            if ph.check_position(mo, px, py).ok:
                base = (px, py)
                break
        if base is None:
            continue
        px, py = base
        mo = Mover(x=px, y=py, z=front.floorheight)
        tx, ty = 2 * mx - px, 2 * my - py  # mirrored across the line
        ok, _ = ph.try_move(mo, tx, ty)
        if 0 < diff <= 24 * FRACUNIT:
            if ok and mo.floorz == back.floorheight:
                stepped += 1
        else:
            if not ok:
                blocked += 1
        if stepped >= 3 and blocked >= 3:
            break
    assert stepped >= 3, "no small step could be climbed"
    assert blocked >= 3, "no tall wall blocked movement"


@requires_wad
def test_point_on_line_side_known_cases(setup):
    # NOTE: this intentionally does NOT compare against R_PointOnSide
    # (angles.py): the two vanilla functions use different arithmetic
    # (sign-bit fast path vs full FixedMul) and genuinely disagree on
    # near-degenerate inputs. Each is tested against hand-computed sides.
    u = FRACUNIT
    assert point_on_line_side(
        -u, 0, Line(v1=Vertex(x=0, y=0), v2=Vertex(x=0, y=u),
                    dx=0, dy=u)) == 1  # west of northward line
    assert point_on_line_side(
        u, 0, Line(v1=Vertex(x=0, y=0), v2=Vertex(x=0, y=u),
                   dx=0, dy=u)) == 0  # east of northward line
    assert point_on_line_side(
        0, -u, Line(v1=Vertex(x=0, y=0), v2=Vertex(x=u, y=0),
                    dx=u, dy=0)) == 0  # south of eastward line
    assert point_on_line_side(
        0, u, Line(v1=Vertex(x=0, y=0), v2=Vertex(x=u, y=0),
                   dx=u, dy=0)) == 1  # north of eastward line
    diag = Line(v1=Vertex(x=0, y=0), v2=Vertex(x=u, y=u), dx=u, dy=u)
    assert point_on_line_side(u, 0, diag) == 0  # below y=x, front side
    assert point_on_line_side(0, u, diag) == 1  # above y=x, back side


@requires_wad
def test_path_traverse_sorted_intercepts(setup):
    game_map, ph = setup
    seen: list = []
    ph.path_traverse(1000 << 16, -3616 << 16, 1200 << 16, -3616 << 16,
                     1, lambda inter: (seen.append(inter[0]), True)[1])
    assert len(seen) >= 1  # crosses the east pillar
    assert all(b >= a for a, b in zip(seen, seen[1:]))
    assert all(f >= 0 for f in seen)


def test_aprox_distance():
    # 3-4-5 triangle estimates to 5.5 (dx+dy-(dx>>1) with dx<dy).
    assert aprox_distance(3 << 16, 4 << 16) == 360448
    assert aprox_distance(4 << 16, 3 << 16) == 360448
    assert aprox_distance(0, 0) == 0


@requires_wad
def test_try_move_relinks_across_blocks(setup):
    """Walking bodies must follow their blocklinks (vanilla SetThingPos).

    Without the re-file, roamed bodies become ghosts: hitscans and
    collisions miss them at their real position (E1M1 blue-room bug).
    """
    from pydoom.mobjs import ThingIndex, spawn_mobj
    from pydoom.info import MT_INDEX
    game_map, ph = setup
    index = ThingIndex(game_map)
    ph.things = index
    mo = spawn_mobj(game_map, ph, index, 1056 << 16, -3616 << 16, 0,
                    MT_INDEX["POSSESSED"])
    oldkey = index._key(mo.x, mo.y)
    assert any(t is mo for t in index.iter_block(*oldkey))
    target = None
    for i in range(1, 40):
        ok, _ = ph.try_move(mo, (1056 - 8 * i) << 16, -3616 << 16)
        if not ok:
            break
        if index._key(mo.x, mo.y) != oldkey:
            target = index._key(mo.x, mo.y)
            break
    assert target is not None, "test never crossed a block boundary"
    assert not any(t is mo for t in index.iter_block(*oldkey))
    assert any(t is mo for t in index.iter_block(*target))


@requires_wad
def test_moved_bodies_still_collide(setup):
    """Two bodies overlap after walking: the move must be refused."""
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    game_map, ph = setup
    index = ThingIndex(game_map)
    ph.things = index
    a = spawn_mobj(game_map, ph, index, 1056 << 16, -3616 << 16, 0,
                   MT_INDEX["POSSESSED"])
    b = spawn_mobj(game_map, ph, index, 900 << 16, -3616 << 16, 0,
                   MT_INDEX["POSSESSED"])
    ok, _ = ph.try_move(b, a.x, a.y)
    assert not ok  # solid bodies block even after (no) travel
    ok, _ = ph.try_move(a, (1056 - 64) << 16, -3616 << 16)
    assert ok
    res = ph.check_position(b, a.x, a.y)
    assert not res.ok  # a is found at its NEW block, not as a ghost


@requires_wad
def test_try_move_refreshes_sector(setup):
    """Sector refs must follow bodies across boundaries (SetThingPos).

    Stale sectors poison REJECT sight checks: the E1M1 blue-room zombies
    could never see a roamed player stuck in its spawn sector.
    """
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    game_map, ph = setup
    index = ThingIndex(game_map)
    ph.things = index
    mo = spawn_mobj(game_map, ph, index, 2272 << 16, -2352 << 16, 0,
                    MT_INDEX["POSSESSED"])
    assert game_map.sectors.index(mo.sector) == 52
    ok, _ = ph.try_move(mo, 2100 << 16, -2352 << 16)
    assert ok
    assert game_map.sectors.index(mo.sector) == 7
    live = ph.subsector_at(mo.x, mo.y).sector
    assert mo.sector is live
    assert mo in live.thinglist
