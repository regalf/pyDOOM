"""Tests for renderer.py: tables, frame rendering, determinism."""

import os

import numpy as np
import pytest

from pydoom.automap import thing_degrees_to_bam
from pydoom.fixed import ANG90
from pydoom.mapdata import ML_MAPPED, Map
from pydoom.renderer import Renderer, SCREENHEIGHT, SCREENWIDTH
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
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    start = next(t for t in game_map.things if t.type == 1)
    return renderer, game_map, start


@requires_wad
def test_mapping_tables(setup):
    renderer, _, _ = setup
    assert len(renderer.viewangletox) == 4096
    assert len(renderer.xtoviewangle) == SCREENWIDTH + 1
    assert 0 < renderer.clipangle < ANG90
    assert renderer.viewangletox[2048] == SCREENWIDTH // 2


@requires_wad
def test_render_player_view(setup):
    renderer, game_map, start = setup
    fb = renderer.render_view(
        game_map, start.x << 16, start.y << 16,
        thing_degrees_to_bam(start.angle),
    )
    assert fb.shape == (SCREENHEIGHT, SCREENWIDTH)
    assert fb.dtype == np.uint8
    assert len(renderer.drawsegs) > 0
    assert int(np.count_nonzero(fb)) > 5000
    # Player looks into the level: the middle column shows wall.
    assert int(np.count_nonzero(fb[:, SCREENWIDTH // 2])) > 0
    # Rendering marks seen lines for the automap, like vanilla.
    assert any(li.flags & ML_MAPPED for li in game_map.lines)


@requires_wad
def test_render_is_deterministic(setup):
    renderer, game_map, start = setup
    args = (game_map, start.x << 16, start.y << 16,
            thing_degrees_to_bam(start.angle))
    assert np.array_equal(renderer.render_view(*args), renderer.render_view(*args))


@requires_wad
def test_point_in_subsector(setup):
    renderer, game_map, start = setup
    sub = renderer.point_in_subsector(start.x << 16, start.y << 16)
    assert sub.sector is not None
    assert 0 <= sub.firstline < len(game_map.segs)


@requires_wad
def test_psprite_anchor_matches_vanilla(setup):
    """Weapon blits are 1:1 lump pixels at the R_DrawPSprite anchor
    (x0 = 1+bobx-leftoffset, y0 = 32+boby-topoffset, unmirrored)."""
    renderer, _, _ = setup
    fb = np.zeros((SCREENHEIGHT, SCREENWIDTH), dtype=np.uint8)
    assert renderer.draw_psprite(fb, "PISG", 0, 0)
    ys, xs = np.nonzero(fb)
    # NOTE: PISGA0 is 57x62 at offsets (-126, -106).
    assert (xs.min(), xs.max()) == (127, 183)
    assert (ys.min(), ys.max()) == (138, 199)
    assert not renderer.draw_psprite(fb, "PLSG", 0, 0)  # shareware: none


@requires_wad
def test_status_bar_draws_bottom_strip(setup):
    """Classic STBAR composition: background, face, numbers, keys."""
    import numpy as np
    from pydoom.player import PlayerState
    from pydoom.statusbar import draw_status_bar
    renderer, _, _ = setup
    fb = np.zeros((SCREENHEIGHT, SCREENWIDTH), dtype=np.uint8)
    ps = PlayerState()
    ps.ammo = [50, 8, 0, 0]
    ps.armorpoints = 100
    ps.armortype = 1
    ps.keys = 1 | 16
    ps.weapons |= 1 << 2
    draw_status_bar(renderer, fb, ps, 100)
    assert not np.any(fb[:168])  # NOTE: the bar owns only the bottom
    assert np.count_nonzero(fb[168:]) > 4000  # bg + face + digits


@requires_wad
def test_plane_light_index_never_escapes(setup):
    """Adversarial plane heights (wrapped negative, huge) must still
    map inside the zlight table instead of raising."""
    renderer, game_map, start = setup
    renderer.render_view(
        game_map, start.x << 16, start.y << 16, 0, 41 << 16, [])
    renderer._plane_zlight = renderer.zlight[0]
    for height in (-2 ** 31, -1, 0, 1, 10 ** 9, 10 ** 12, 2 ** 40):
        renderer._plane_height = height
        renderer._map_plane(100, 10, 20)  # must not raise


@requires_wad
def test_extra_light_brightens_view(setup):
    """Muzzle-flash room light (A_Light1/2) must move pixels."""
    import hashlib
    renderer, game_map, start = setup
    args = (game_map, start.x << 16, start.y << 16, 0, 41 << 16, [])
    dark = hashlib.md5(bytes(bytearray(
        renderer.render_view(*args)))).hexdigest()
    lit = hashlib.md5(bytes(bytearray(
        renderer.render_view(*args, extra_light=2)))).hexdigest()
    assert dark != lit


@requires_wad
def test_face_cascade():
    """Doomguy grins, hurts, rampages and dies through ST indices."""
    from pydoom.info import MT_INDEX
    from pydoom.mobjs import ThingIndex, spawn_mobj
    from pydoom.physics import Physics
    from pydoom.player import PlayerState
    from pydoom.statusbar import FaceState, face_lump, update_face
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    mo = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16, 0,
                    MT_INDEX["PLAYER"])
    assert face_lump(0) == "STFST00"
    assert face_lump(40) == "STFGOD0"
    assert face_lump(41) == "STFDEAD0"
    assert face_lump(8 + 5) == "STFOUCH1"
    # NOTE: idle picks a straight face and settles.
    fs, ps = FaceState(), PlayerState()
    assert update_face(fs, ps, mo, False).startswith("STFST")
    # NOTE: weapon bonus grins (priority 8 beats pain below).
    ps.bonuscount = 6
    ps.weapons |= 1 << 2
    assert update_face(fs, ps, mo, False).startswith("STFEVL")
    # NOTE: corpse face wins over everything while dead.
    mo.health = 0
    assert update_face(fs, ps, mo, False) == "STFDEAD0"
    # NOTE: overkill corpses (hp<0) hold STFDEAD0 tick after tick,
    # never the god face (vanilla tests !health, not == 0).
    mo.health = -23
    for _ in range(4):
        assert update_face(fs, ps, mo, False) == "STFDEAD0"


@requires_wad
def test_fullbright_visor_lifts_dark(setup):
    renderer, game_map, start = setup
    args = dict(game_map=game_map, x=start.x << 16, y=start.y << 16,
                angle=thing_degrees_to_bam(start.angle))
    dark = renderer.render_view(**args)
    lit = renderer.render_view(**args, fullbright=True)
    assert lit.shape == dark.shape and lit.dtype == dark.dtype
    assert not (lit == dark).all()  # NOTE: flag reaches the pipeline
    assert float(lit.mean()) >= float(dark.mean())
