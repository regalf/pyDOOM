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


@requires_wad
def test_held_saw_attack_has_no_idle_gap():
    """Mirror of the viewer tic block (cooldown/chained/atk_until): a
    held saw shows attack frames on every rendered tic, with no 1-tic
    drop to the idle blade-up frame per cycle (vanilla chains inside
    the 0-tic refire state)."""
    from pydoom import weapons
    from pydoom.ai import AIContext
    from pydoom.combat import register_combat_actions
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    from pydoom.physics import Physics
    from pydoom.player import PlayerState, WP_CHAINSAW
    register_combat_actions()  # idempotent
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    ctx = AIContext(physics=phys)
    ctx.mobjs = []
    ctx.skyflatnum = None
    player = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    player.z = player.floorz
    ps = PlayerState()
    ps.weapons |= 1 << WP_CHAINSAW
    ps.readyweapon = ps.pendingweapon = WP_CHAINSAW
    cooldown, atkheld, atk_until, atk_span = 0, False, 0, 1
    tics, pending, picks = 0, [], []
    for _ in range(40):  # five held 8-tic revs
        if cooldown:
            cooldown -= 1
        weapons.tick_pending(ps, player, phys, index, ctx.mobjs, None,
                             ctx, pending)
        cd_now = cooldown
        chained = weapons.chained_pull(cd_now, atkheld, WP_CHAINSAW)
        if cd_now == 0 or chained:  # trigger held down
            cd, _flash = weapons.fire(ps, player, phys, index, ctx.mobjs,
                                      None, True, ctx, pending,
                                      held=chained)
            if cd >= 0:
                cooldown = cd
                atk_until = tics + cd + 1  # NOTE: viewer coverage rule
                atk_span = max(1, cd)
                atkheld = chained
        tics += 1  # leveltime closes the tic, renders read the new tics
        assert tics < atk_until  # never falls back to idle mid-rev
        span = max(1, atk_span)
        elapsed = span - (atk_until - tics)
        timeline = weapons.attack_timeline(WP_CHAINSAW, 0, atkheld)
        picks.append(timeline[min(len(timeline) - 1, max(0, elapsed))])
    assert "".join(picks) == "AAAABBBB" * 5  # full down-cycles, no skip
