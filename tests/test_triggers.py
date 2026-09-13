"""Tests for walk-over triggers, floors, plats and lights."""

import math
import os

import pytest

from pydoom.doors import World
from pydoom.fixed import FRACUNIT
from pydoom.mapdata import Map
from pydoom.physics import Mover, Physics
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

REG_WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "doom.wad")

requires_reg = pytest.mark.skipif(
    not os.path.exists(REG_WAD_PATH), reason="doom.wad not found"
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
        if ok and line in [ld for ld, _side in crossed]:
            msgs = [m for m in
                    (world.cross_special_line(ld, True, mo, ph, None)
                     for ld, _side in crossed)
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
    assert len(movers(world)) == 1
    plat = movers(world)[0]
    home = plat.sector.floorheight
    assert plat.status == "down"
    for _ in range(1200):
        world.tick()
        if not movers(world):
            break
    assert not movers(world)  # down-wait-up-stay finished
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
        if not movers(world):
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


@requires_wad
def test_w1_bridge_22_raises_walkover(setup):
    """E1M5 bridge: W1-22 lifts the tag-6 floor to the next highest."""
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M5")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 22)
    # NOTE: vanilla USE on 22 does nothing (p_switch.c has no case 22).
    assert world.use_special_line(line, 0, True) is None
    assert line.special == 22
    assert not movers(world)
    # ...but walking over it raises the bridge (p_spec.c W1 case 22).
    assert world.cross_special_line(line, True) is None
    assert line.special == 0  # W1 clears
    assert len(movers(world)) == 1
    sec = world.find_sectors_from_tag(6)[0]
    want = world.find_next_highest_floor(sec, -24 * FRACUNIT)
    for _ in range(600):
        world.tick()
        if not movers(world):
            break
    assert not movers(world)  # raise-and-stay finished
    assert sec.floorheight == want  # bridge up at the far ledge
    assert sec.specialdata is None


@requires_wad
def test_w1_stairs_build(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M3")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = game_map.lines[967]
    assert line.special == 8
    assert world.cross_special_line(line, True) is None
    assert line.special == 0  # walk-once spent
    assert movers(world)  # steps are rising


@requires_wad
def test_s1_donut_pillar_drops(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M2")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = game_map.lines[604]
    assert line.special == 9
    assert world.use_special_line(line, 0, True) is None
    assert line.special == 0
    assert len(movers(world)) == 2  # ring rises, hole drops


@requires_wad
def test_gunshot_opens_impact_door(setup):
    from pydoom.ai import AIContext
    from pydoom.combat import _Shot
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    from pydoom.physics import Physics
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M2")
    texman.resolve_map(game_map)
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    world = World(game_map, texman)
    ctx = AIContext(physics=phys, world=world, players=[])
    ctx.mobjs = []
    line = game_map.lines[572]
    assert line.special == 46
    shooter = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16, 0,
                         MT_INDEX["PLAYER"])
    shooter.is_player = True
    from pydoom.combat import _hit_line
    from pydoom.fixed import FRACUNIT
    shot = _Shot(shooter, 0, 2048 * FRACUNIT, 0, 5, phys, index, [],
                 None)
    shot.ctx = ctx
    phys._trace = (shooter.x, shooter.y, FRACUNIT, 0)  # path state
    _hit_line(shot, line, FRACUNIT // 2)
    # NOTE: G1 impact doors retrigger (special kept), but one opens now.
    assert line.special == 46
    assert len(movers(world)) == 1


@requires_wad
def test_scroll_lines_advance_each_tick(setup):
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    assert world.scroll_lines  # E1M1 has scrollers
    before = [game_map.sides[li.sidenum[0]].textureoffset
              for li in world.scroll_lines]
    world.tick()
    after = [game_map.sides[li.sidenum[0]].textureoffset
             for li in world.scroll_lines]
    from pydoom.fixed import FRACUNIT
    assert all(a == b + FRACUNIT for a, b in zip(after, before))


@requires_wad
def test_fall_makes_corpses_walkable(setup):
    from pydoom.ai import AIContext
    from pydoom.combat import COMBAT_ACTIONS
    from pydoom.info import MF_FLAGS, MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    from pydoom.physics import Physics
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    ctx = AIContext(physics=phys)
    body = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16, 0,
                      MT_INDEX["POSSESSED"])
    assert body.flags & MF_FLAGS["MF_SOLID"]
    COMBAT_ACTIONS["A_Fall"](body, ctx)
    assert not (body.flags & MF_FLAGS["MF_SOLID"])
    other = spawn_mobj(game_map, phys, index, 900 << 16, -3616 << 16, 0,
                       MT_INDEX["POSSESSED"])
    ok, _ = phys.try_move(other, body.x, body.y)
    assert ok  # the living walk straight over the dead


def _reg_world(marker):
    """Fresh registered-wad world (E2/E3 specials live in doom.wad)."""
    wad = WadFile(REG_WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, marker)
    texman.resolve_map(game_map)
    return World(game_map, texman), game_map


def test_walk_tables_cover_e1_e3_gaps():
    """Dispatch tables pin the vanilla W1/WR/SR numbers by role."""
    from pydoom.doors import _SWITCH_DOORS, _SWITCH_PLATS, _WALK_ONCE
    from pydoom.doors import _WALK_RETRIGGER
    from pydoom.doors import DoorType
    assert _WALK_ONCE[22] == ("plat", ("raiseToNearestAndChange", 0))
    assert _WALK_ONCE[30] == ("floor", "raiseToTexture")
    assert _WALK_ONCE[37] == ("floor", "lowerAndChange")
    assert _WALK_ONCE[56] == ("floor", "raiseFloorCrush")
    assert _WALK_ONCE[59] == ("floor", "raiseFloor24AndChange")
    assert _WALK_ONCE[104] == ("lightsOff", None)
    assert _WALK_RETRIGGER[89] == ("platStop", None)
    assert _WALK_RETRIGGER[95] == ("plat", ("raiseToNearestAndChange", 0))
    assert _SWITCH_DOORS[42] == (DoorType.CLOSE, True)
    assert 22 not in _SWITCH_PLATS  # W1 only: USE does nothing


@requires_reg
def test_w1_raise_to_texture_e2m2():
    world, game_map = _reg_world("E2M2")
    line = next(li for li in game_map.lines if li.special == 30)
    secs = world.find_sectors_from_tag(line.tag)
    floors = [s.floorheight for s in secs]
    assert world.cross_special_line(line, True) is None
    assert line.special == 0  # W1 clears
    assert len(movers(world)) == len(secs)
    for _ in range(1200):
        world.tick()
        if not movers(world):
            break
    assert not movers(world)
    for sec, before in zip(secs, floors):
        assert sec.floorheight > before  # shortest-texture lift ran


@requires_reg
def test_w1_raise24_and_change_e2m2():
    world, game_map = _reg_world("E2M2")
    line = next(li for li in game_map.lines
                if li.special == 59 and li.tag == 19)
    sec = world.find_sectors_from_tag(19)[0]
    assert world.cross_special_line(line, True) is None
    assert line.special == 0
    for _ in range(600):
        world.tick()
        if not movers(world):
            break
    assert sec.floorheight == 40 * FRACUNIT + 24 * FRACUNIT


@requires_reg
def test_w1_lower_and_change_e2m1():
    world, game_map = _reg_world("E2M1")
    line = next(li for li in game_map.lines
                if li.special == 37 and li.tag == 1)
    sec = world.find_sectors_from_tag(1)[0]
    assert world.cross_special_line(line, True) is None
    assert line.special == 0
    assert movers(world)
    for _ in range(1200):
        world.tick()
        if not movers(world):
            break
    assert sec.floorheight < -64 * FRACUNIT  # dropped to the lowest


@requires_reg
def test_w1_raise_crush_e2m4():
    from pydoom.doors import FloorMover
    world, game_map = _reg_world("E2M4")
    line = next(li for li in game_map.lines if li.special == 56)
    assert world.cross_special_line(line, True) is None
    assert line.special == 0
    crushing = [t for t in movers(world) if isinstance(t, FloorMover)]
    assert crushing and all(t.crush for t in crushing)


@requires_reg
def test_wr_stop_parks_plat_e2m2():
    world, game_map = _reg_world("E2M2")
    line = next(li for li in game_map.lines if li.special == 89)
    world.do_plat(line, "downWaitUpStay", 0)  # lift running on that tag
    assert world.cross_special_line(line, True) is None
    assert line.special == 89  # WR retriggers
    assert world.activeplats
    assert all(p.status == "in_stasis" for p in world.activeplats
               if p.tag == line.tag)


@requires_reg
def test_sr_close_door_e3m1():
    from pydoom.doors import VerticalDoor
    world, game_map = _reg_world("E3M1")
    line = next(li for li in game_map.lines if li.special == 42)
    assert world.use_special_line(line, 0, True) is None
    assert any(isinstance(t, VerticalDoor) for t in world.thinkers)


@requires_reg
def test_w1_lights_off_e2m5():
    world, game_map = _reg_world("E2M5")
    line = next(li for li in game_map.lines if li.special == 104)
    secs = world.find_sectors_from_tag(line.tag)
    assert secs and all(s.lightlevel == 255 for s in secs)
    assert world.cross_special_line(line, True) is None
    assert line.special == 0
    for sec in secs:
        assert sec.lightlevel <= 255
        assert all(sec.lightlevel <= n.lightlevel
                   for n in world._neighbors(sec))  # dimmest wins


def _mover_sounds(monkeypatch):
    """Capture audio.play names (vanilla mover voices)."""
    from pydoom import audio as audio_mod
    sounds = []
    monkeypatch.setattr(audio_mod, "play",
                        lambda n, *a: sounds.append(n) or False)
    return sounds


@requires_wad
def test_lift_dwus_pings_start_stops(setup, monkeypatch):
    """E1M1 lift: pstart down, pstop at the bottom, pstart up, pstop
    at the top (p_plats.c T_PlatRaise)."""
    sounds = _mover_sounds(monkeypatch)
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 88)
    assert world.cross_special_line(line, True) is None
    assert sounds == ["pstart"]
    for _ in range(1200):
        world.tick()
        if not movers(world):
            break
    assert not movers(world)
    stops = [s for s in sounds if s == "pstop"]
    starts = [s for s in sounds if s == "pstart"]
    assert len(starts) == 2 and len(stops) == 2  # down/up legs pinged
    assert sounds.index("pstop") > 0
    assert sounds[-1] == "pstop"  # parked at the top


@requires_wad
def test_bridge_raise_grinds_then_stops(setup, monkeypatch):
    """E1M5 bridge (W1-22): stnmov grind while rising, pstop on top."""
    sounds = _mover_sounds(monkeypatch)
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M5")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = next(li for li in game_map.lines if li.special == 22)
    assert world.cross_special_line(line, True) is None
    for _ in range(600):
        world.tick()
        if not movers(world):
            break
    assert not movers(world)
    assert sounds[0] == "stnmov"  # EV_DoPlat raise-change voice
    assert sounds.count("stnmov") > 2  # 8-tic grind on the way up
    assert sounds[-1] == "pstop"  # bridge seated


@requires_wad
def test_stairs_grind_e1m3(setup, monkeypatch):
    """E1M3 exit stairs (W1-8): floor thinkers grind, then stop."""
    sounds = _mover_sounds(monkeypatch)
    wad, texman = setup
    game_map = Map.from_wad(wad, "E1M3")
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    line = game_map.lines[967]
    assert line.special == 8
    assert world.cross_special_line(line, True) is None
    for _ in range(1200):
        world.tick()
        if not movers(world):
            break
    assert not movers(world)
    assert "stnmov" in sounds  # T_MoveFloor grind covers stairs too
    assert "pstop" in sounds  # every step pings on arrival
