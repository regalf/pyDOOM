"""Tests for pydoom.ext: discovery, depends, safe dispatch, sim hooks."""

import os
from pathlib import Path

import pytest

from pydoom import ext
from pydoom.ext import Event, ExtApi, Mod, ModManager

MODS_DIR = Path(__file__).parent.parent / "mods"

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found")


@pytest.fixture()
def mgr():
    ext.set_current(None)
    m = ModManager(str(MODS_DIR))
    m.discover()
    m.refresh()
    yield m
    ext.set_current(None)


def test_discovers_bundled_mods(mgr):
    ids = {mid for mid, _v, _s, _r in mgr.status()}
    assert {"no_mouse_forward", "mouselook", "loot_bounce"} <= ids


def test_defaults_no_dep_refusal(mgr):
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states["no_mouse_forward"] == "on"
    assert states["loot_bounce"] == "on"
    assert states["mouselook"] == "on"  # NOTE: enabled_default=true


def test_mouselook_refuses_without_dep(mgr):
    mgr.toggle("no_mouse_forward")  # dep off
    states = {mid: (s, r) for mid, _v, s, r in mgr.status()}
    assert states["mouselook"][0] == "refused"
    assert states["mouselook"][1] == "NEEDS no_mouse_forward"
    # NOTE: user intent kept: dep back on returns it to on.
    mgr.toggle("no_mouse_forward")
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states["mouselook"] == "on"
    assert mgr.toggle("mouselook") == "off"


def test_mouse_forward_consumes_y(mgr):
    ev = mgr.emit("mouse_motion", dx=10, dy=-4,
                  consume_x=False, consume_y=False)
    assert ev.consume_y is True
    assert ev.consume_x is False
    assert (ev.dx, ev.dy) == (10, -4)


def test_mouselook_reads_dy_before_consume(mgr):
    # NOTE: dy>0 means mouse pushed up (viewer negates pygame rel-y):
    # it pitches the look by the slider factor, never the altitude.
    mgr.emit("mouse_motion", dx=0, dy=100, sens_rad_per_px=0.01,
             consume_x=False, consume_y=False)
    ev = mgr.emit("camera", viewz=41.0, pitch=0.0,
                  keys_up=False, keys_down=False, handled=False)
    assert ev.handled is True
    assert ev.viewz == 41.0
    assert ev.pitch == pytest.approx(1.0)


def test_mouselook_pitch_follows_slider(mgr):
    mgr.emit("mouse_motion", dx=0, dy=100, sens_rad_per_px=0.001,
             consume_x=False, consume_y=False)
    ev = mgr.emit("camera", viewz=41.0, pitch=0.0,
                  keys_up=False, keys_down=False, handled=False)
    assert ev.pitch == pytest.approx(0.1)
    # NOTE: older engines omit the field: without any cached value the
    # cold fallback applies instead of 0 (re-enable to reset the cache).
    mgr.toggle("mouselook")
    mgr.toggle("mouselook")
    mgr.emit("mouse_motion", dx=0, dy=100,
             consume_x=False, consume_y=False)
    ev = mgr.emit("camera", viewz=41.0, pitch=0.0,
                  keys_up=False, keys_down=False, handled=False)
    assert ev.pitch == pytest.approx(0.28)


def test_mouselook_sens_refreshed_by_settings(mgr):
    mgr.emit("settings", mouse_sens=9, mouse_rad_per_px=0.005,
             sfx_vol=8, mus_vol=8)
    mgr.emit("mouse_motion", dx=0, dy=100,
             consume_x=False, consume_y=False)  # NOTE: no sens field
    ev = mgr.emit("camera", viewz=41.0, pitch=0.0,
                  keys_up=False, keys_down=False, handled=False)
    assert ev.pitch == pytest.approx(0.5)


def test_mouselook_keeps_pitch_when_idle(mgr):
    ev = mgr.emit("camera", viewz=60.0, pitch=0.2,
                  keys_up=False, keys_down=False, handled=False)
    assert ev.handled is True
    assert ev.viewz == 60.0  # NOTE: never touched
    assert ev.pitch == 0.2


def test_mouselook_never_touches_viewz(mgr):
    # NOTE: stairs, pits, negative floors: the baseline always wins.
    for base in (41.0, 30.0, -12.5, 200.0):
        ev = mgr.emit("camera", viewz=base, pitch=0.0,
                      keys_up=False, keys_down=False, handled=False)
        assert ev.viewz == base


def test_loot_bounce_kicks_up_from_midair(mgr):
    class Drop:
        dead = False
        floorz = 0
        z = 0
        momz = 0

    drop = Drop()
    # NOTE: like combat.kill_mobj: mid-victim height, fixed units.
    mgr.emit("on_kill", target_type=1, drop=drop,
             fall_from=28 * 65536)
    assert drop.z == 28 * 65536  # NOTE: spawns mid-air, not grounded
    assert drop.momz > 0  # NOTE: the engine flies the arc from here
    mod = mgr.records["loot_bounce"].mod
    assert len(mod.flying) == 1


def test_loot_bounce_rekicks_on_landing_then_rests(mgr):
    class Drop:
        dead = False
        floorz = 0
        z = 0
        momz = 0

    drop = Drop()
    mgr.emit("on_kill", target_type=1, drop=drop,
             fall_from=28 * 65536)
    mod = mgr.records["loot_bounce"].mod
    kicks = []
    for _ in range(6):
        drop.z, drop.momz = drop.floorz, 0  # NOTE: the engine lands it
        mgr.emit("post_tic", tic=0)
        kicks.append(drop.momz)
    # NOTE: damped re-kicks (5u -> 2.25u -> ~1u) then let go.
    assert kicks[0] == int(5 * 65536 * 0.45)
    assert 0 < kicks[1] < kicks[0]
    assert kicks[2] == 0
    assert mod.flying == []


def test_loot_bounce_without_fall_from_launches_from_ground(mgr):
    class Drop:
        dead = False
        floorz = 0
        z = 0
        momz = 0

    drop = Drop()
    mgr.emit("on_kill", target_type=1, drop=drop)  # NOTE: old engine
    assert drop.z == 0
    assert drop.momz > 0  # NOTE: plain pop instead of a guessed height


def test_loot_bounce_grabbed_midair_stops(mgr):
    class Drop:
        dead = False
        floorz = 0
        z = 0
        momz = 0

    drop = Drop()
    mgr.emit("on_kill", target_type=1, drop=drop,
             fall_from=28 * 65536)
    mgr.emit("post_tic", tic=0)
    assert drop.momz > 0  # NOTE: still flying, untouched
    drop.dead = True  # NOTE: grabbed mid-air
    mgr.emit("post_tic", tic=1)
    mod = mgr.records["loot_bounce"].mod
    assert mod.flying == []


def test_raising_handler_never_breaks_emit(mgr):
    class Bad(Mod):
        id = "bad"
        version = "0"

        def on_enable(self, api):
            api.on("pre_tic", self._boom)

        def _boom(self, ev):
            raise RuntimeError("boom")

    rec = ext.ModRecord({"id": "bad"}, Bad())
    mgr.records["bad"] = rec
    mgr.refresh()
    assert rec.state == "on"
    ev = mgr.emit("pre_tic", tic=0)  # must not raise
    assert ev.hook == "pre_tic"


def test_demo_guard_skips_sim_handlers(mgr):
    class SimMod(Mod):
        id = "simmod"
        version = "0"
        sim_affecting = True
        seen = 0

        def on_enable(self, api):
            api.on("on_kill", self._hit)

        def _hit(self, ev):
            type(self).seen += 1

    rec = ext.ModRecord({"id": "simmod"}, SimMod())
    SimMod.seen = 0
    mgr.records["simmod"] = rec
    mgr.refresh()
    mgr.demo_guard = True
    mgr.emit("on_kill", target_type=1, drop=None)
    assert SimMod.seen == 0
    mgr.demo_guard = False
    mgr.emit("on_kill", target_type=1, drop=None)
    assert SimMod.seen == 1


def test_unknown_hook_rejected(mgr):
    with pytest.raises(ValueError):
        ExtApi(mgr, "x").on("nope", lambda ev: None)


def test_broken_manifest_is_error(tmp_path):
    bad = tmp_path / "badmod"
    bad.mkdir()
    (bad / "mod.toml").write_text("id = [unclosed\n")
    (bad / "mod.py").write_text("MOD = None\n")
    m = ModManager(str(tmp_path))
    m.discover()
    assert m.records["badmod"].state == "error"


def test_event_consume_stops_chain(mgr):
    calls = []

    class First(Mod):
        id = "first"
        version = "0"

        def on_enable(self, api):
            api.on("pre_tic", self._a, priority=10)

        def _a(self, ev):
            calls.append("first")
            ev.consume()

    class Second(Mod):
        id = "second"
        version = "0"

        def on_enable(self, api):
            api.on("pre_tic", self._b)

        def _b(self, ev):
            calls.append("second")

    mgr.records["first"] = ext.ModRecord({"id": "first"}, First())
    mgr.records["second"] = ext.ModRecord({"id": "second"}, Second())
    mgr.refresh()
    mgr.emit("pre_tic", tic=0)
    assert calls == ["first"]


def test_settings_snapshot_and_hook(mgr):
    class FakeSettings:
        mouse_sens = 7
        sfx_vol = 3
        mus_vol = 11

    snap = ext.settings_snapshot(FakeSettings())
    assert snap == {"mouse_sens": 7, "mouse_rad_per_px": pytest.approx(
        0.0004 + 7 * 0.0006), "sfx_vol": 3, "mus_vol": 11}

    seen = []

    class Watcher(Mod):
        id = "watcher"
        version = "0"

        def on_enable(self, api):
            api.on("settings", self._got)

        def _got(self, ev):
            seen.append((ev.sfx_vol, ev.mus_vol))

    mgr.records["watcher"] = ext.ModRecord({"id": "watcher"}, Watcher())
    mgr.refresh()
    mgr.emit("settings", **snap)
    assert seen == [(3, 11)]


def test_event_attr_errors():
    ev = Event("camera", viewz=1.0)
    assert ev.viewz == 1.0
    with pytest.raises(AttributeError):
        ev.missing


def test_default_mods_dir_unfrozen():
    import sys
    assert getattr(sys, "frozen", False) is False  # NOTE: test env
    found = ext.default_mods_dir()
    assert Path(found).name == "mods"
    assert (Path(found) / "mouselook" / "mod.toml").is_file()


def test_default_mods_dir_frozen(monkeypatch):
    import sys
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", "/fake/app/pyDOOM")
    assert ext.default_mods_dir() == "/fake/app/mods"


def _write_mod(tmp_path, mid, toml_extra=""):
    d = tmp_path / mid
    d.mkdir()
    (d / "mod.toml").write_text(
        f"id = \"{mid}\"\nversion = \"1\"\napi_version = 1\n"
        + toml_extra)
    (d / "mod.py").write_text(
        "from pydoom.ext import Mod\n"
        f"class M(Mod):\n    id = \"{mid}\"\n    version = \"1\"\n"
        "MOD = M()\n")


def test_backend_gate_refuses_and_reenables(tmp_path):
    _write_mod(tmp_path, "glmod", "backend = \"opengl\"\n")
    m = ModManager(str(tmp_path))
    m.discover()
    m.set_backend("software")
    states = {mid: (s, r) for mid, _v, s, r in m.status()}
    assert states["glmod"][0] == "refused"
    assert states["glmod"][1] == "NEEDS opengl"
    m.set_backend("opengl")
    states = {mid: s for mid, _v, s, _r in m.status()}
    assert states["glmod"] == "on"


def test_bad_backend_value_refused(tmp_path):
    _write_mod(tmp_path, "weird", "backend = \"vulkan\"\n")
    m = ModManager(str(tmp_path))
    m.discover()
    m.set_backend("opengl")
    states = {mid: (s, r) for mid, _v, s, r in m.status()}
    assert states["weird"][0] == "refused"
    assert "BAD backend" in states["weird"][1]


def test_backend_unknown_value_rejected():
    m = ModManager(str(MODS_DIR))
    with pytest.raises(ValueError):
        m.set_backend("vulkan")


def test_bundled_mods_are_backend_any(mgr):
    mgr.set_backend("software")
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states["no_mouse_forward"] == "on"
    mgr.set_backend("opengl")
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states["no_mouse_forward"] == "on"


def test_mouselook_requires_opengl(mgr):
    mgr.set_backend("software")
    states = {mid: (s, r) for mid, _v, s, r in mgr.status()}
    assert states["mouselook"] == ("refused", "NEEDS opengl")
    assert mgr.toggle("mouselook") == "refused"  # NOTE: cannot force on
    mgr.set_backend("opengl")
    # NOTE: default intent is on, but the refused toggle flipped it off.
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states["mouselook"] == "off"
    assert mgr.toggle("mouselook") == "on"


# -- sim core (P0 launch slice) --

def _wad_setup():
    from pydoom.ai import AIContext
    from pydoom.combat import register_combat_actions
    from pydoom.mapdata import Map
    from pydoom.mobjs import ThingIndex
    from pydoom.physics import Physics
    from pydoom.wad import WadFile
    register_combat_actions()  # idempotent
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    ctx = AIContext(physics=phys)
    ctx.mobjs = []
    ctx.skyflatnum = None
    return game_map, phys, index, ctx


def _mount(mgr, mod):
    mgr.records[mod.id] = ext.ModRecord({"id": mod.id}, mod)
    mgr.refresh()
    return mod


def test_build_ticcmd_mutates_and_chain_stops(tmp_path):
    order = []

    class Double(Mod):
        id = "double"
        version = "0"

        def on_enable(self, api):
            api.on("build_ticcmd", self._run, priority=10)

        def _run(self, ev):
            order.append("double")
            ev.forwardmove *= 2

    class Zero(Mod):
        id = "zero"
        version = "0"

        def on_enable(self, api):
            api.on("build_ticcmd", self._run, priority=0)

        def _run(self, ev):
            order.append("zero")
            ev.forwardmove = 0
            ev.consume()

    class Never(Mod):
        id = "never"
        version = "0"

        def on_enable(self, api):
            api.on("build_ticcmd", self._run, priority=-10)

        def _run(self, ev):
            order.append("never")

    mgr = ModManager(str(tmp_path))
    for cls in (Double, Zero, Never):
        _mount(mgr, cls())
    ev = mgr.emit("build_ticcmd", forwardmove=50, sidemove=3,
                  angleturn=100, buttons=1)
    assert (ev.forwardmove, ev.sidemove, ev.angleturn, ev.buttons) == \
        (0, 3, 100, 1)
    assert order == ["double", "zero"]  # NOTE: consume stops the chain


def test_demo_guard_skips_sim_ticcmd(tmp_path):
    seen = []

    class Rig(Mod):
        id = "rig"
        version = "0"
        sim_affecting = True

        def on_enable(self, api):
            api.on("build_ticcmd", self._run)
            api.on("player_think", self._think)

        def _run(self, ev):
            seen.append("ticcmd")

        def _think(self, ev):
            seen.append("think")

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Rig())
    mgr.demo_guard = True
    mgr.emit("build_ticcmd", forwardmove=0, sidemove=0, angleturn=0,
             buttons=0)
    mgr.emit("player_think", tic=0)
    assert seen == []
    mgr.demo_guard = False
    mgr.emit("build_ticcmd", forwardmove=0, sidemove=0, angleturn=0,
             buttons=0)
    mgr.emit("player_think", tic=1)
    assert seen == ["ticcmd", "think"]


@requires_wad
def test_damage_hook_halves(tmp_path):
    from pydoom.combat import damage_mobj
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import spawn_mobj
    game_map, phys, index, ctx = _wad_setup()

    class Half(Mod):
        id = "half"
        version = "0"
        sim_affecting = True

        def on_enable(self, api):
            api.on("damage", self._half)

        def _half(self, ev):
            ev.amount = ev.amount // 2

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Half())
    ext.set_current(mgr)
    try:
        troop = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16,
                           0, MT_INDEX["TROOP"])
        damage_mobj(troop, None, None, 100, ctx)
        assert troop.health == 10  # NOTE: 60 spawn - 50 halved
    finally:
        ext.set_current(None)


@requires_wad
def test_damage_hook_consume_blocks_all(tmp_path):
    from pydoom.combat import damage_mobj
    from pydoom.info import MF_FLAGS, MT_INDEX
    from pydoom.mobjs import spawn_mobj
    game_map, phys, index, ctx = _wad_setup()

    class Ghost(Mod):
        id = "ghost"
        version = "0"
        sim_affecting = True

        def on_enable(self, api):
            api.on("damage", self._nope)

        def _nope(self, ev):
            ev.consume()

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Ghost())
    ext.set_current(mgr)
    try:
        troop = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16,
                           0, MT_INDEX["TROOP"])
        damage_mobj(troop, None, None, 100, ctx)
        assert troop.health == 60  # NOTE: untouched, not even pain
        assert not (troop.flags & MF_FLAGS["MF_JUSTHIT"])
    finally:
        ext.set_current(None)


@requires_wad
def test_pickup_hook_blocks_take(tmp_path):
    from pydoom.info import MT_INDEX, type_record
    from pydoom.mobjs import spawn_mobj
    from pydoom.pickup import touch_special_thing
    from pydoom.player import AM_CLIP, PlayerState
    game_map, phys, index, ctx = _wad_setup()

    class Vegan(Mod):
        id = "vegan"
        version = "0"
        sim_affecting = True
        got_kind = None

        def on_enable(self, api):
            api.on("pickup", self._block)

        def _block(self, ev):
            type(self).got_kind = ev.kind
            ev.consume()

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Vegan())
    ext.set_current(mgr)
    try:
        player = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16,
                            0, MT_INDEX["PLAYER"])
        player.is_player = True
        player.z = player.floorz
        ps = PlayerState()
        rec = type_record(2007)
        item = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16,
                          -1, rec["mt"])
        item.doomednum = 2007
        picked, msg = touch_special_thing(item, player, ps, ctx)
        assert (picked, msg) == (False, None)
        assert ps.ammo[AM_CLIP] == 50  # NOTE: nothing taken
        assert Vegan.got_kind == "ammo"
    finally:
        ext.set_current(None)


@requires_wad
def test_prefire_consume_blocks_shot(tmp_path):
    from pydoom import weapons
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import spawn_mobj
    from pydoom.player import AM_CLIP, PlayerState
    game_map, phys, index, ctx = _wad_setup()

    class Jam(Mod):
        id = "jam"
        version = "0"
        sim_affecting = True
        shots = 0

        def on_enable(self, api):
            api.on("pre_fire", self._jam)
            api.on("post_fire", self._count)

        def _jam(self, ev):
            ev.consume()

        def _count(self, ev):
            type(self).shots += 1

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Jam())
    ext.set_current(mgr)
    try:
        player = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16,
                            0, MT_INDEX["PLAYER"])
        ps = PlayerState()  # NOTE: pistol ready, 50 bullets
        cd, flash = weapons.fire(ps, player, phys, index, [], None, True,
                                 ctx, None, False)
        assert (cd, flash) == (-1, False)
        assert ps.ammo[AM_CLIP] == 50  # NOTE: no shot, no spend
        assert Jam.shots == 0
    finally:
        ext.set_current(None)


@requires_wad
def test_postfire_notifies_per_shot(tmp_path):
    from pydoom import weapons
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import spawn_mobj
    from pydoom.player import AM_CLIP, PlayerState
    game_map, phys, index, ctx = _wad_setup()

    class Counter(Mod):
        id = "counter"
        version = "0"
        sim_affecting = True
        shots = []

        def on_enable(self, api):
            api.on("post_fire", self._count)

        def _count(self, ev):
            type(self).shots.append(ev.weapon)

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Counter())
    ext.set_current(mgr)
    try:
        player = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16,
                            0, MT_INDEX["PLAYER"])
        ps = PlayerState()
        cd, _flash = weapons.fire(ps, player, phys, index, [], None, True,
                                  ctx, None, False)
        assert cd >= 0
        assert ps.ammo[AM_CLIP] == 49  # NOTE: one bullet spent
        assert Counter.shots == [ps.readyweapon]
    finally:
        ext.set_current(None)


# -- launch slice 2: world, audio, video, flow hooks --

def test_line_teleport_crush_consume_passthrough(tmp_path):
    seen = []

    class Spy(Mod):
        id = "spy"
        version = "0"

        def on_enable(self, api):
            api.on("line_activate", self._line)
            api.on("teleport", self._tp)
            api.on("sector_crush", self._crush)

        def _line(self, ev):
            seen.append(("line", ev.kind, ev.special, ev.side,
                         ev.is_player))

        def _tp(self, ev):
            seen.append(("tp", ev.mover is not None))

        def _crush(self, ev):
            seen.append(("crush",))
            ev.consume()

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Spy())
    mgr.emit("line_activate", line=None, special=11, kind="use",
             side=0, is_player=True, mover=None)
    mgr.emit("teleport", mover=object(), line=None)
    ev = mgr.emit("sector_crush", victim=None, sector=None)
    assert seen == [("line", "use", 11, 0, True), ("tp", True),
                    ("crush",)]
    assert ev.consumed is True


@requires_wad
def test_line_activate_consume_blocks_door(tmp_path):
    from pydoom.doors import World, VerticalDoor
    from pydoom.mapdata import Map
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile

    def fresh():
        wad = WadFile(WAD_PATH)
        texman = TextureManager(wad)
        game_map = Map.from_wad(wad, "E1M1")
        texman.resolve_map(game_map)
        return World(game_map, texman), game_map

    class Plug(Mod):
        id = "plug"
        version = "0"
        sim_affecting = True
        saw = None

        def on_enable(self, api):
            api.on("line_activate", self._plug)

        def _plug(self, ev):
            type(self).saw = (ev.kind, ev.special)
            ev.consume()

    # NOTE: baseline (no manager): the manual door starts moving.
    world, game_map = fresh()
    line = next(li for li in game_map.lines
                if li.special == 1 and li.backsector is not None)
    world.use_special_line(line, 0, True, 0)
    assert any(isinstance(t, VerticalDoor) for t in world.thinkers)

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Plug())
    ext.set_current(mgr)
    try:
        world2, game_map2 = fresh()
        line2 = next(li for li in game_map2.lines
                     if li.special == 1 and li.backsector is not None)
        world2.use_special_line(line2, 0, True, 0)
        assert not any(isinstance(t, VerticalDoor)
                       for t in world2.thinkers)
        assert Plug.saw == ("use", 1)
    finally:
        ext.set_current(None)


@requires_wad
def test_teleport_hook_fires_with_mover(tmp_path):
    from types import SimpleNamespace
    from pydoom.doors import World
    from pydoom.info import MT_INDEX
    from pydoom.mapdata import Map
    from pydoom.mobjs import spawn_mobj
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    game_map, phys, index, ctx = _wad_setup()
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    texman.resolve_map(game_map)
    world = World(game_map, texman)

    class Spy(Mod):
        id = "tspy"
        version = "0"
        sim_affecting = True
        saw = None

        def on_enable(self, api):
            api.on("teleport", self._spy)

        def _spy(self, ev):
            type(self).saw = (ev.mover, ev.line.tag)

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Spy())
    ext.set_current(mgr)
    try:
        troop = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16,
                           0, MT_INDEX["TROOP"])
        line = SimpleNamespace(tag=9999)
        assert world.teleport(line, troop, phys, ctx.mobjs, 0) is False
        mover, tag = Spy.saw
        assert mover is troop and tag == 9999
    finally:
        ext.set_current(None)


@requires_wad
def test_sector_crush_spare_skips_damage(tmp_path):
    from pydoom.doors import World, grind_sector
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import spawn_mobj
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    game_map, phys, index, ctx = _wad_setup()
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    texman.resolve_map(game_map)
    world = World(game_map, texman)
    world.time = 0  # NOTE: crush damage ticks when time & 3 == 0

    def wedged():
        troop = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16,
                           0, MT_INDEX["TROOP"])
        troop.z = troop.ceilingz + 65536  # NOTE: nofit above the ceiling
        return troop

    troop = wedged()
    grind_sector(world, troop.sector, True, [troop], phys, ctx)
    assert troop.health == 50  # NOTE: baseline 60 - 10 crush

    class Mercy(Mod):
        id = "mercy"
        version = "0"
        sim_affecting = True
        victims = []

        def on_enable(self, api):
            api.on("sector_crush", self._spare)

        def _spare(self, ev):
            type(self).victims.append(ev.victim)
            ev.consume()

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Mercy())
    ext.set_current(mgr)
    try:
        troop2 = wedged()
        # NOTE: troop is still wedged in the same sector: the grind
        # visits both, and the mod spares both.
        grind_sector(world, troop2.sector, True, [troop, troop2], phys,
                     ctx)
        assert (troop.health, troop2.health) == (50, 60)
        assert Mercy.victims == [troop, troop2]
    finally:
        ext.set_current(None)


def test_sfx_play_rename_and_mute(monkeypatch, tmp_path):
    from pydoom import audio
    calls = []
    monkeypatch.setattr(audio.engine, "play",
                        lambda *a: calls.append(a) or True)

    class DJ(Mod):
        id = "dj"
        version = "0"

        def on_enable(self, api):
            api.on("sfx_play", self._dj)

        def _dj(self, ev):
            if ev.name == "pistol":
                ev.name = "shotgn"
            if ev.name == "oof":
                ev.consume()

    mgr = ModManager(str(tmp_path))
    _mount(mgr, DJ())
    ext.set_current(mgr)
    try:
        assert audio.play("pistol", 1, 2) is True
        assert calls[-1][0] == "shotgn"  # NOTE: lump swapped
        assert calls[-1][1:3] == (1, 2)  # NOTE: position kept
        assert audio.play("oof") is False  # NOTE: silenced
        assert all(c[0] != "oof" for c in calls)
    finally:
        ext.set_current(None)


def test_music_change_swap_and_hold(monkeypatch, tmp_path):
    from pydoom import audio

    class FakeWad:
        def __init__(self):
            self.asked = []

        def read_lump(self, name):
            self.asked.append(name)
            return b"\x00" * 10

    class FakePlayer:
        def __init__(self):
            self.songs = 0

        def play_song(self, *a):
            self.songs += 1

    wad, player = FakeWad(), FakePlayer()
    monkeypatch.setattr(audio, "_music_wad", wad)
    monkeypatch.setattr(audio, "_music_player", player)
    monkeypatch.setattr(audio, "_music_main", [])
    monkeypatch.setattr(audio, "_music_perc", [])

    class Juke(Mod):
        id = "juke"
        version = "0"
        mode = "swap"

        def on_enable(self, api):
            api.on("music_change", self._j)

        def _j(self, ev):
            if type(self).mode == "swap":
                ev.song = "D_E1M2"
            else:
                ev.consume()

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Juke())
    ext.set_current(mgr)
    try:
        assert audio.music_play("D_E1M1", "boot") is True
        assert wad.asked == ["D_E1M2"]  # NOTE: lump swapped
        assert player.songs == 1
        Juke.mode = "hold"
        assert audio.music_play("D_E1M3", "x") is False
        assert wad.asked == ["D_E1M2"]  # NOTE: untouched
        assert player.songs == 1
    finally:
        ext.set_current(None)


def test_palette_flash_remap():
    from pydoom.player import PlayerState, palette_index

    class Shades(Mod):
        id = "shades"
        version = "0"

        def on_enable(self, api):
            api.on("palette_flash", self._flat)

        def _flat(self, ev):
            ev.palette = 0

    ps = PlayerState()
    ps.damagecount = 20
    assert palette_index(ps) == 4  # NOTE: no manager: vanilla slot
    mgr = ModManager("/nonexistent")
    _mount(mgr, Shades())
    ext.set_current(mgr)
    try:
        assert palette_index(ps) == 0  # NOTE: mod remap wins
    finally:
        ext.set_current(None)


def test_flow_statusbar_demo_dispatch(tmp_path):
    seen = []

    class Spy(Mod):
        id = "spy2"
        version = "0"

        def on_enable(self, api):
            api.on("level_exit", self._exit)
            api.on("map_unload", self._unload)
            api.on("demo_start", self._dstart)
            api.on("demo_stop", self._dstop)
            api.on("statusbar", self._sb)

        def _exit(self, ev):
            seen.append(("exit", ev.exited, ev.entering, ev.secret))

        def _unload(self, ev):
            seen.append(("unload", ev.map))

        def _dstart(self, ev):
            seen.append(("dstart", ev.mode))

        def _dstop(self, ev):
            seen.append(("dstop", ev.mode))

        def _sb(self, ev):
            seen.append(("sb", ev.fb is not None))

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Spy())
    mgr.emit("level_exit", exited="E1M1", entering="E1M2", secret=False)
    mgr.emit("map_unload", map="E1M1")
    mgr.emit("demo_start", mode="record")
    mgr.emit("demo_stop", mode="record")
    mgr.emit("statusbar", fb=object())
    assert seen == [("exit", "E1M1", "E1M2", False), ("unload", "E1M1"),
                    ("dstart", "record"), ("dstop", "record"),
                    ("sb", True)]


def test_backend_changed_announces(tmp_path):
    seen = []

    class Spy(Mod):
        id = "spy3"
        version = "0"

        def on_enable(self, api):
            api.on("backend_changed", self._bc)

        def _bc(self, ev):
            seen.append((ev.old, ev.new))

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Spy())
    mgr.set_backend("software")
    mgr.set_backend("opengl")
    mgr.set_backend("opengl")  # NOTE: no change, no event
    assert seen == [("any", "software"), ("software", "opengl")]


@requires_wad
def test_automap_mod_marks_render():
    from pydoom.automap import Automap, load_playpal_from_wad
    from pydoom.mapdata import Map
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    am = Automap(game_map, 320, 200, load_playpal_from_wad(wad))
    base = am.collect_segments()
    # NOTE: E1M1 start, hangar floor: inside the initial whole-map view.
    am.mod_marks = [(1056 << 16, -3616 << 16)]
    marked = am.collect_segments()
    assert len(marked) == len(base) + 4  # NOTE: one square outline
    am.mod_marks = []
    assert len(am.collect_segments()) == len(base)


def test_automap_draw_hook_appends(tmp_path):
    class Pins(Mod):
        id = "pins"
        version = "0"

        def on_enable(self, api):
            api.on("automap_draw", self._pin)

        def _pin(self, ev):
            ev.marks.append((100, 200))

    mgr = ModManager(str(tmp_path))
    _mount(mgr, Pins())
    marks = []
    mgr.emit("automap_draw", marks=marks, amap=None)
    assert marks == [(100, 200)]


def test_renamed_sfx_obeys_master_volume(monkeypatch, tmp_path):
    import struct
    from pydoom import audio
    from pydoom.audio import SoundEngine, upsample_sfx

    class FakeChannel:
        def __init__(self):
            self.vol = None
            self.snd = None

        def get_busy(self):
            return False

        def stop(self):
            pass

        def set_volume(self, left, right):
            self.vol = (left, right)

        def play(self, snd):
            self.snd = snd

    class FakeMixer:
        def __init__(self):
            self.channels = [FakeChannel() for _ in range(8)]

        def Channel(self, i):
            return self.channels[i]

        def Sound(self, buffer=None):
            return object()

    pcm = bytes([0, 64, 128, 192, 255] * 20)
    lump = struct.pack("<HHI", 3, 11025, len(pcm)) + pcm

    class FakeWad:
        def read_lump(self, name):
            assert name in ("DSPISTOL", "DSSHOTGN")
            return lump

    eng = SoundEngine()
    eng.mixer = FakeMixer()
    eng.wad = FakeWad()
    eng.master = 0.5  # NOTE: options slider, driven per-frame
    monkeypatch.setattr(audio, "engine", eng)

    class DJ(Mod):
        id = "dj2"
        version = "0"

        def on_enable(self, api):
            api.on("sfx_play", self._dj)

        def _dj(self, ev):
            if ev.name == "pistol":
                ev.name = "shotgn"

    mgr = ModManager(str(tmp_path))
    _mount(mgr, DJ())
    ext.set_current(mgr)
    try:
        assert audio.play("pistol") is True
        # NOTE: renamed lump requested, settings volume still applied.
        assert eng.slots[0][0] == "shotgn"
        assert eng.mixer.channels[0].vol == (0.5, 0.5)
        assert eng.mixer.channels[0].snd is not None
        # NOTE: mute governs renamed sounds too (before any mixer work).
        eng.muted = True
        assert audio.play("pistol") is False
    finally:
        ext.set_current(None)


def test_api_image_blits_with_alpha_and_clip(tmp_path):
    pygame = pytest.importorskip("pygame")
    import numpy as np
    from pydoom.ext import ExtApi, ModManager
    moddir = tmp_path / "imgmod"
    moddir.mkdir()
    surf = pygame.Surface((3, 2), pygame.SRCALPHA, 32)
    surf.set_at((0, 0), (255, 0, 0, 255))
    surf.set_at((1, 0), (0, 255, 0, 255))
    surf.set_at((2, 0), (0, 0, 255, 0))  # NOTE: transparent, keeps bg
    surf.set_at((0, 1), (0, 0, 0, 255))
    surf.set_at((1, 1), (255, 255, 255, 255))
    surf.set_at((2, 1), (255, 0, 0, 255))
    pygame.image.save(surf, str(moddir / "icon.png"))
    mgr = ModManager(str(tmp_path))
    pal = [(0, 0, 0), (255, 0, 0), (0, 255, 0), (0, 0, 255),
           (255, 255, 255)] + [(0, 0, 0)] * 251
    mgr.palette = np.array(pal, dtype=np.uint8)
    api = ExtApi(mgr, "imgmod")
    fb = np.full((4, 5), 7, dtype=np.uint8)
    assert api.image(fb, "icon.png", 1, 1) is True
    assert fb[1, 1] == 1 and fb[1, 2] == 2 and fb[1, 3] == 7
    assert fb[2, 1] == 0 and fb[2, 2] == 4 and fb[2, 3] == 1
    assert ("imgmod", "icon.png") in mgr._img_cache  # NOTE: converted once
    fb2 = np.full((4, 5), 7, dtype=np.uint8)
    assert api.image(fb2, "icon.png", -2, -1) is True
    assert fb2[0, 0] == 1  # NOTE: only src (2,1) stays visible
    assert fb2.sum() == 7 * 19 + 1
    assert api.image(fb2, "nope.png", 0, 0) is False  # NOTE: log + skip
    assert api.image(fb2, "../evil.png", 0, 0) is False  # NOTE: contained


def _write_mod(root, mid, assets_toml=""):
    pygame = pytest.importorskip("pygame")
    import numpy as np
    d = root / mid
    (d / "hud").mkdir(parents=True)
    surf = pygame.Surface((2, 2), pygame.SRCALPHA, 32)
    surf.fill((255, 0, 0, 255))
    pygame.image.save(surf, str(d / "hud" / "icon.png"))
    (d / "mod.toml").write_text(
        f'id = "{mid}"\nversion = "0.1.0"\napi_version = 1\n'
        + assets_toml)
    (d / "mod.py").write_text(
        "from pydoom.ext import Mod\n\n\n"
        f"class M(Mod):\n    id = \"{mid}\"\n    version = \"0.1.0\"\n"
        "    seen = []\n"
        "    def on_enable(self, api):\n"
        "        type(self).seen.append(True)\n\n\nMOD = M()\n")
    return np


def test_manifest_preload_ok_and_cached(tmp_path):
    import numpy as np
    from pydoom.ext import ModManager
    _write_mod(tmp_path, "good",
               assets_toml='[assets]\nimages = ["hud/icon.png"]\n')
    mgr = ModManager(str(tmp_path))
    mgr.discover()
    mgr.palette = np.zeros((256, 3), dtype=np.uint8)
    mgr.palette[1] = (255, 0, 0)
    mgr.refresh()
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states["good"] == "on"
    assert ("good", "hud/icon.png") in mgr._img_cache  # NOTE: ready at boot


def test_manifest_preload_missing_refuses(tmp_path):
    import numpy as np
    from pydoom.ext import ModManager
    _write_mod(tmp_path, "bad",
               assets_toml='[assets]\nimages = ["hud/ghost.png"]\n')
    _write_mod(tmp_path, "ugly",
               assets_toml='[assets]\nimages = "hud/icon.png"\n')
    mgr = ModManager(str(tmp_path))
    mgr.discover()
    mgr.palette = np.zeros((256, 3), dtype=np.uint8)
    mgr.refresh()
    rows = {mid: (s, r) for mid, _v, s, r in mgr.status()}
    assert rows["bad"][0] == "refused"  # NOTE: game boots without it
    assert rows["bad"][1] == "BAD ASSET hud/ghost.png"
    assert rows["ugly"] == ("refused", "BAD ASSET manifest")
    # NOTE: refused mods never enable: no hooks, no cache entries.
    assert not any(k[0] in ("bad", "ugly") for k in mgr._img_cache)


def test_crosshair_mod_draws_both_arts():
    import numpy as np
    from pydoom import ext
    from pydoom.ext import ModManager
    mgr = ModManager(ext.default_mods_dir())
    mgr.discover()
    assert "crosshair" in mgr.records  # NOTE: bundled concept mod
    mgr.palette = np.array([tuple((i * 37) % 256 for _ in range(3))
                            for i in range(256)], dtype=np.uint8)
    mgr.refresh()
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states["crosshair"] == "on"
    assert ("crosshair", "hud/greendotted.png") in mgr._img_cache
    assert ("crosshair", "hud/greendotted_target.png") in mgr._img_cache
    for key in (("crosshair", "hud/greendotted.png"),
                ("crosshair", "hud/greendotted_target.png")):
        assert mgr._img_cache[key][0].shape == (4, 4)  # NOTE: 4px art
    cold = np.zeros((200, 320), dtype=np.uint8)
    mgr.emit("aim", target=False)
    mgr.emit("post_overlay", fb=cold)
    hot = np.zeros((200, 320), dtype=np.uint8)
    mgr.emit("aim", target=True)
    mgr.emit("post_overlay", fb=hot)
    assert cold.sum() > 0 and hot.sum() > 0  # NOTE: 32x32 dot lands
    assert not (cold == hot).all()  # NOTE: art actually swaps


@requires_wad
def test_aim_ray_hits_target_ahead():
    from pydoom.combat import MISSILERANGE, aim_line_attack
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import spawn_mobj
    game_map, phys, index, ctx = _wad_setup()
    troop = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16,
                       0, MT_INDEX["TROOP"])
    player = spawn_mobj(game_map, phys, index, 1000 << 16, -3500 << 16,
                        0, MT_INDEX["PLAYER"])
    mobjs = [troop, player]
    player.angle = 0x80000000  # NOTE: face west, straight at the troop
    _slope, tgt = aim_line_attack(player, player.angle, MISSILERANGE,
                                  phys, index, mobjs, None)
    assert tgt is troop
    player.angle = 0x00000000  # NOTE: face east, empty hangar
    _slope, tgt = aim_line_attack(player, player.angle, MISSILERANGE,
                                  phys, index, mobjs, None)
    assert tgt is None


def test_crosshair_hidden_outside_level():
    import numpy as np
    from pydoom import ext
    from pydoom.ext import ModManager
    mgr = ModManager(ext.default_mods_dir())
    mgr.discover()
    mgr.palette = np.array([tuple((i * 37) % 256 for _ in range(3))
                            for i in range(256)], dtype=np.uint8)
    mgr.refresh()
    menu_fb = np.zeros((200, 320), dtype=np.uint8)
    mgr.emit("gamestate", old="level", new="menu")
    mgr.emit("aim", target=True)
    mgr.emit("post_overlay", fb=menu_fb)
    assert menu_fb.sum() == 0  # NOTE: no dot over menus/title
    level_fb = np.zeros((200, 320), dtype=np.uint8)
    mgr.emit("gamestate", old="menu", new="level")
    mgr.emit("post_overlay", fb=level_fb)
    assert level_fb.sum() > 0


def test_default_mods_dir_frozen_hits_internal(monkeypatch, tmp_path):
    import sys
    from pydoom import ext
    bundle = tmp_path / "bundle"
    internal = bundle / "_internal"
    (internal / "mods").mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)
    monkeypatch.setattr(sys, "executable", str(bundle / "pyDOOM"),
                        raising=False)
    # NOTE: onedir datas land in _internal (like DOOM1.WAD): bundled
    # mods resolve there, not beside the exe.
    assert ext.default_mods_dir() == str(internal / "mods")
    (internal / "mods").rmdir()
    assert ext.default_mods_dir() == str(bundle / "mods")
