"""Tests for cheats.py: sequence matching and m_cheat effects."""

import os

import pytest

from pydoom.cheats import (
    CheatEngine,
    apply_behold,
    apply_choppers,
    apply_fa,
    apply_god,
    apply_kfa,
)
from pydoom.player import (
    CF_GODMODE,
    KEY_BLUE,
    KEY_RED,
    KEY_YELLOW,
    PW_ALLMAP,
    PW_INVULN,
    PW_STRENGTH,
    WP_CHAINSAW,
    PlayerState,
)

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def _type(eng, text):
    hits = []
    for ch in text:
        hits += eng.feed(ch)
    return hits


def test_god_fires_only_on_full_code():
    eng = CheatEngine()
    assert _type(eng, "iddq") == []
    assert _type(eng, "d") == [("iddqd", "")]


def test_wrong_char_breaks_prefix_but_suffix_still_matches():
    eng = CheatEngine()
    assert _type(eng, "idxiddqd") == [("iddqd", "")]


def test_clev_collects_two_param_chars():
    eng = CheatEngine()
    assert _type(eng, "idclev") == []
    assert _type(eng, "1") == []
    assert _type(eng, "9") == [("idclev", "19")]


def test_behold_collects_one_letter():
    eng = CheatEngine()
    assert _type(eng, "idbehold") == []
    assert _type(eng, "v") == [("idbehold", "v")]


def test_uppercase_and_garbage_tolerated():
    eng = CheatEngine()
    assert _type(eng, "IDDQD") == [("iddqd", "")]
    eng2 = CheatEngine()
    assert _type(eng2, "idk") == []
    assert eng2.feed(" ") == []  # skipped, does not eat the prefix
    assert _type(eng2, "fa") == [("idkfa", "")]


def test_god_toggles_and_heals():
    from types import SimpleNamespace
    ps = PlayerState()
    mo = SimpleNamespace(health=12)
    assert apply_god(ps, mo) == "Degreelessness Mode On"
    assert ps.cheats & CF_GODMODE
    assert mo.health == 100
    assert apply_god(ps, mo) == "Degreelessness Mode Off"
    assert not ps.cheats & CF_GODMODE


def test_kfa_loadout_with_keys_fa_without():
    yes, no = PlayerState(), PlayerState()
    apply_kfa(yes)
    apply_fa(no)
    for ps in (yes, no):
        assert ps.weapons == (1 << 9) - 1
        assert ps.ammo == ps.maxammo
        assert (ps.armorpoints, ps.armortype) == (200, 2)
    assert yes.keys & (KEY_BLUE | KEY_YELLOW | KEY_RED)
    assert no.keys == 0


def test_choppers_raises_chainsaw():
    ps = PlayerState()
    assert apply_choppers(ps) == "Mercenary"
    assert ps.weapons & (1 << WP_CHAINSAW)
    assert ps.pendingweapon == WP_CHAINSAW


def test_behold_powers_and_hint():
    ps = PlayerState()
    assert apply_behold(ps, None, "v") == "INVULNERABILITY!"
    assert PW_INVULN in ps.powers
    assert apply_behold(ps, None, "s") == "BERSERK!"
    assert ps.powers.get(PW_STRENGTH) == 1
    assert apply_behold(ps, None, "a") == "COMPUTER AREA MAP"
    assert ps.powers.get(PW_ALLMAP) == -1
    assert "inVuln" in apply_behold(ps, None, "z")


@requires_wad
def test_god_blocks_damage_but_telefrag_kills():
    from pydoom.combat import damage_mobj, register_combat_actions
    from pydoom.info import MT_INDEX
    from pydoom.mapdata import Map
    from pydoom.mobjs import ThingIndex, spawn_mobj
    from pydoom.ai import AIContext
    from pydoom.physics import Physics
    from pydoom.wad import WadFile
    register_combat_actions()
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    ctx = AIContext(physics=phys)
    ctx.mobjs = []
    ps = PlayerState()
    ctx.player_state = ps
    player = spawn_mobj(game_map, phys, index, 1056 << 16, -3616 << 16,
                        0, MT_INDEX["PLAYER"])
    player.is_player = True
    apply_god(ps, player)
    damage_mobj(player, None, None, 500, ctx)
    assert player.health == 100  # NOTE: god shrugs off rockets
    damage_mobj(player, None, None, 10000, ctx)
    assert player.health <= 0  # NOTE: telefrag (>=1000) still kills
