"""Tests for doors.py: manual doors open, block, close; switches flip."""

import os

import pytest

from pydoom.doors import BUTTONTIME, World, move_plane
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
    blocker = ((sec,), 0, 56 * FRACUNIT)
    from pydoom.doors import PlaneResult
    assert move_plane(sec, 8 * FRACUNIT, 0, False, 1, -1, blocker) == (
        PlaneResult.CRUSHED)
    assert sec.ceilingheight == 56 * FRACUNIT  # reverted
    # No blocker, or blocker elsewhere: moves freely.
    assert move_plane(sec, 64 * FRACUNIT, 0, False, 1, -1, None) == (
        PlaneResult.PASTDEST)
    assert sec.ceilingheight == 0


def test_move_plane_crushes_threshold_blocker():
    """A body overlapping the moving sector crushes even when its center
    is in the next room: the blocker carries every overlapped sector,
    so a closing door reopens on a player mid-threshold instead of
    sealing them inside solid geometry (stuck under the map)."""
    from pydoom.doors import PlaneResult, move_plane
    from pydoom.mapdata import Sector
    door = Sector(floorheight=0, ceilingheight=56 * FRACUNIT)
    room = Sector(floorheight=0, ceilingheight=72 * FRACUNIT)
    blocker = ((room, door), 0, 56 * FRACUNIT)
    assert move_plane(door, 8 * FRACUNIT, 0, False, 1, -1, blocker) == (
        PlaneResult.CRUSHED)
    assert door.ceilingheight == 56 * FRACUNIT  # reverted
    # A body fully outside still lets the door close.
    clear = ((room,), 0, 56 * FRACUNIT)
    assert move_plane(door, 64 * FRACUNIT, 0, False, 1, -1, clear) == (
        PlaneResult.PASTDEST)
    assert door.ceilingheight == 0


@requires_wad
def test_light_thinkers_spawn_and_clear(setup):
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


@requires_wad
def test_ceiling_crusher_bounces_and_hurts(setup):
    """T_MoveCeiling crushAndRaise: down to floor+8, bounce, grind hurts."""
    from types import SimpleNamespace
    from pydoom.doors import Ceiling, grind_sector
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    _, texman, game_map = setup
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, texman)
    sec = next(s for s in game_map.sectors if s.tag)
    top0 = sec.ceilingheight
    mobjs: list = []
    world.grind = lambda s, c: grind_sector(world, s, c, mobjs, phys, None)
    # NOTE: a live victim inside the crusher sector (bbox center).
    xs = [v.x for l in game_map.lines
          for v in (l.v1, l.v2) if l.frontsector is sec]
    ys = [v.y for l in game_map.lines
          for v in (l.v1, l.v2) if l.frontsector is sec]
    troop = spawn_mobj(None, phys, index, (min(xs) + max(xs)) // 2,
                       (min(ys) + max(ys)) // 2, -1, MT_INDEX["TROOP"])
    assert troop.sector is sec
    mobjs.append(troop)
    try:
        assert world.do_ceiling(SimpleNamespace(tag=sec.tag),
                                "crushAndRaise")
        ceil = sec.specialdata
        assert isinstance(ceil, Ceiling) and ceil.crush
        assert ceil.bottomheight == sec.floorheight + 8 * FRACUNIT
        for _ in range(3000):
            world.tick()
            if ceil.direction == 1:
                break
        assert ceil.direction == 1  # hit the floor, bounced back up
        assert troop.health < 60  # PIT_ChangeSector damage ran grinding down
    finally:
        sec.ceilingheight = top0
        sec.specialdata = None


@requires_wad
def test_ceiling_grind_slowdown_and_quiet_lowerer(setup):
    """Grinding crushers slow to CEILSPEED/8; lowerAndCrush (44) never
    hurts (crush stays false, like vanilla)."""
    from types import SimpleNamespace
    from pydoom.doors import CEILSPEED, grind_sector
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    _, texman, game_map = setup
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, texman)
    sec = next(s for s in game_map.sectors if s.tag)
    top0 = sec.ceilingheight
    mobjs: list = []
    world.grind = lambda s, c: grind_sector(world, s, c, mobjs, phys, None)
    # NOTE: a tall blocker in the sector grinds the ceiling forever.
    world.blocker = ((sec,), sec.floorheight, 200 * FRACUNIT)
    try:
        assert world.do_ceiling(SimpleNamespace(tag=sec.tag),
                                "crushAndRaise")
        for _ in range(1200):
            world.tick()
        assert sec.specialdata.speed == CEILSPEED // 8
        sec.specialdata.dead = True
        sec.specialdata = None
        sec.ceilingheight = top0
        xs = [v.x for l in game_map.lines
              for v in (l.v1, l.v2) if l.frontsector is sec]
        ys = [v.y for l in game_map.lines
              for v in (l.v1, l.v2) if l.frontsector is sec]
        troop = spawn_mobj(None, phys, index, (min(xs) + max(xs)) // 2,
                           (min(ys) + max(ys)) // 2, -1,
                           MT_INDEX["TROOP"])
        assert troop.sector is sec
        mobjs.append(troop)
        assert world.do_ceiling(SimpleNamespace(tag=sec.tag),
                                "lowerAndCrush")
        assert not sec.specialdata.crush
        for _ in range(400):
            world.tick()
        assert troop.health == 60  # NOTE: type-44 grinds without damage
        assert sec.specialdata.direction == -1  # still grinding
    finally:
        sec.ceilingheight = top0
        sec.specialdata = None


@requires_wad
def test_ceiling_raise_to_highest_parks(setup):
    """raiseToHighest climbs to the tallest neighbor, then exits."""
    from types import SimpleNamespace
    _, texman, game_map = setup
    world = World(game_map, texman)
    sec = next(s for s in game_map.sectors
               if world.find_highest_ceiling(s) > s.ceilingheight)
    tag0 = sec.tag
    sec.tag = 4242  # NOTE: borrow an untagged sector for the lift
    want = world.find_highest_ceiling(sec)
    top0 = sec.ceilingheight
    try:
        assert world.do_ceiling(SimpleNamespace(tag=4242),
                                "raiseToHighest")
        for _ in range(3000):
            world.tick()
            if sec.specialdata is None:
                break
        assert sec.specialdata is None
        assert sec.ceilingheight == want
    finally:
        sec.ceilingheight = top0
        sec.specialdata = None
        sec.tag = tag0


@requires_wad
def test_walk_triggers_wire_ceilings(setup):
    """W1 6/25/40/44 and WR 72/73/77 dispatch to do_ceiling; W1 clears."""
    from types import SimpleNamespace
    from pydoom.doors import CEILSPEED, _WALK_ONCE, _WALK_RETRIGGER, Ceiling
    assert _WALK_ONCE[6] == ("ceiling", "fastCrushAndRaise")
    assert _WALK_ONCE[25] == ("ceiling", "crushAndRaise")
    assert _WALK_ONCE[40] == ("ceiling", "raiseToHighest")
    assert _WALK_ONCE[44] == ("ceiling", "lowerAndCrush")
    assert _WALK_RETRIGGER[72] == ("ceiling", "lowerAndCrush")
    assert _WALK_RETRIGGER[73] == ("ceiling", "crushAndRaise")
    assert _WALK_RETRIGGER[77] == ("ceiling", "fastCrushAndRaise")
    _, texman, game_map = setup
    world = World(game_map, texman)
    sec = next(s for s in game_map.sectors if s.tag)
    top0 = sec.ceilingheight
    try:
        line = SimpleNamespace(special=25, tag=sec.tag)
        world.cross_special_line(line, True)
        assert line.special == 0  # W1 fires once
        assert isinstance(sec.specialdata, Ceiling)
        assert sec.specialdata.speed == CEILSPEED
    finally:
        sec.ceilingheight = top0
        sec.specialdata = None
    try:
        line = SimpleNamespace(special=77, tag=sec.tag)
        world.cross_special_line(line, True)
        assert sec.specialdata.speed == 2 * CEILSPEED  # fast x2
    finally:
        sec.ceilingheight = top0
        sec.specialdata = None


@requires_wad
def test_teleport_refuses_blocked_landing(setup):
    """P_TeleportMove: pad inside a wall refuses silently, no fog."""
    from types import SimpleNamespace
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    _, texman, game_map = setup
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, texman)
    # NOTE: a point 16 units behind a one-sided wall is solid rock.
    wall = next(li for li in game_map.lines if li.backsector is None)
    import pydoom.physics as _ph
    mx = (wall.v1.x + wall.v2.x) // 2
    my = (wall.v1.y + wall.v2.y) // 2
    dx, dy = wall.v2.x - wall.v1.x, wall.v2.y - wall.v1.y
    leng = max(1, (dx * dx + dy * dy) ** 0.5)
    for sgn in (1, -1):
        px = int(mx + sgn * dy / leng * 16 * FRACUNIT)
        py = int(my - sgn * dx / leng * 16 * FRACUNIT)
        if _ph.point_on_line_side(px, py, wall) == 1:
            break
    sec = game_map.sectors[0]
    tag0 = sec.tag
    sec.tag = 9999
    mobjs: list = []
    dest = mover = None
    try:
        dest = spawn_mobj(None, phys, index, px, py, 0,
                          MT_INDEX["TELEPORTMAN"])
        dest.sector = sec  # pad claims the line's tag from inside rock
        mover = spawn_mobj(game_map, phys, index, 1056 << 16,
                           -3616 << 16, 0, MT_INDEX["PLAYER"])
        mover.sector = phys.subsector_at(mover.x, mover.y).sector
        nfog0 = sum(1 for mo in mobjs if mo.type == MT_INDEX["TFOG"])
        line = SimpleNamespace(tag=9999, special=39)
        assert world.teleport(line, mover, phys, mobjs) is False
        assert (mover.x, mover.y) == (1056 << 16, -3616 << 16)
        assert sum(1 for mo in mobjs
                   if mo.type == MT_INDEX["TFOG"]) == nfog0
    finally:
        sec.tag = tag0
        for mo in (dest, mover):
            if mo is None:
                continue
            try:
                index.unlink(mo)
            except Exception:
                pass


@requires_wad
def test_ceiling_crush_stop_and_reactivation(setup):
    """EV_CeilingCrushStop parks tagged crushers (W1-57/WR-74); the next
    crusher trigger resumes the old course (P_ActivateInStasis)."""
    from types import SimpleNamespace
    _, texman, game_map = setup
    world = World(game_map, texman)
    sec = next(s for s in game_map.sectors if s.tag)
    top0 = sec.ceilingheight
    line = SimpleNamespace(tag=sec.tag)
    try:
        assert world.do_ceiling(line, "crushAndRaise")
        ceil = sec.specialdata
        assert ceil.direction == -1
        assert world.ceiling_crush_stop(line)
        assert ceil.direction == 0
        assert ceil.olddirection == -1
        frozen = sec.ceilingheight
        for _ in range(35):
            world.tick()
        assert sec.ceilingheight == frozen  # parked, thinker idles
        assert world.do_ceiling(line, "crushAndRaise")  # retrigger resumes
        assert ceil.direction == -1
        world.tick()
        assert sec.ceilingheight != frozen
    finally:
        sec.ceilingheight = top0
        sec.specialdata = None
