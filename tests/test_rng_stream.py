"""RNG stream parity (milestone C): every P_Random draw must match vanilla
in count AND order, or demos desync. Tests pin draws with set_state and
read the shared table, plus a PYTHONHASHSEED subprocess check."""

import os
import subprocess
import sys
from types import SimpleNamespace

import pytest

from pydoom import combat
from pydoom.info import MF_FLAGS, MT_INDEX
from pydoom.m_random import _RNDTABLE, get_state, set_state


@pytest.fixture(autouse=True)
def _isolated_rng():
    """Pin and restore both streams: these tests jump the P_Random
    index around, and must not leak that into other test files."""
    before = get_state()
    yield
    set_state(before)

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def played(monkeypatch):
    """Capture audio.play calls (name only)."""
    from pydoom import audio
    calls = []
    monkeypatch.setattr(audio, "play",
                        lambda name, *a, **k: calls.append(name) or True)
    return calls


def make_actor(type_name, target=None):
    return SimpleNamespace(type=MT_INDEX[type_name], x=0, y=0, z=0,
                           angle=0, target=target)


def test_scream_cycles_podth_with_one_draw(monkeypatch):
    calls = played(monkeypatch)
    set_state((0, 10))  # menu index 0, gameplay index 10
    combat.a_scream(make_actor("POSSESSED"), None)
    assert calls == [f"podth{_RNDTABLE[11] % 3 + 1}"]
    assert get_state() == (0, 11)  # exactly one gameplay draw


def test_scream_cycles_bgdth_with_one_draw(monkeypatch):
    calls = played(monkeypatch)
    set_state((0, 40))
    combat.a_scream(make_actor("TROOP"), None)
    assert calls == [f"bgdth{_RNDTABLE[41] % 2 + 1}"]
    assert get_state() == (0, 41)


def test_scream_uncycled_draws_nothing(monkeypatch):
    calls = played(monkeypatch)
    set_state((0, 40))
    combat.a_scream(make_actor("SERGEANT"), None)  # sgtdth, no cycle
    assert calls == ["sgtdth"]
    assert get_state() == (0, 40)


def test_posattack_draws_spread_before_damage(monkeypatch):
    seen = []
    monkeypatch.setattr(combat, "aim_line_attack",
                        lambda *a: (0, None))
    monkeypatch.setattr(combat, "line_attack",
                        lambda *a: seen.append((a[1], a[4])) or None)
    played(monkeypatch)
    target = SimpleNamespace(x=100 << 16, y=0, z=0,
                             flags=MF_FLAGS["MF_SHOOTABLE"])
    actor = make_actor("POSSESSED", target)
    phys = SimpleNamespace(things=None)
    ctx = SimpleNamespace(physics=phys, mobjs=None, skyflatnum=0)
    set_state((0, 20))
    t1, t2, t3 = _RNDTABLE[21], _RNDTABLE[22], _RNDTABLE[23]
    combat.a_posattack(actor, ctx)
    assert len(seen) == 1
    angle, damage = seen[0]
    # NOTE: vanilla A_PosAttack draws spread, spread, then damage.
    assert angle == ((t1 - t2) << 20) & 0xFFFFFFFF
    assert damage == ((t3 % 5) + 1) * 3
    assert get_state() == (0, 23)


def test_face_target_sprays_spectres(monkeypatch):
    played(monkeypatch)
    target = SimpleNamespace(x=100 << 16, y=0,
                             flags=MF_FLAGS["MF_SHOOTABLE"]
                             | MF_FLAGS["MF_SHADOW"])
    actor = make_actor("TROOP", target)
    set_state((0, 60))
    combat._face(actor)
    t1, t2 = _RNDTABLE[61], _RNDTABLE[62]
    from pydoom.angles import point_to_angle2
    base = point_to_angle2(0, 0, 100 << 16, 0)
    assert actor.angle == (base + ((t1 - t2) << 21)) & 0xFFFFFFFF
    assert get_state() == (0, 62)
    # NOTE: solid targets cost no draws.
    actor2 = make_actor("TROOP", SimpleNamespace(
        x=100 << 16, y=0, flags=MF_FLAGS["MF_SHOOTABLE"]))
    set_state((0, 60))
    combat._face(actor2)
    assert actor2.angle == base
    assert get_state() == (0, 60)


@requires_wad
def test_headless_run_ignores_hash_seed(tmp_path):
    """Same 24 frames under two PYTHONHASHSEEDs: identical demo checksums
    (the fps ema rides wall-clock timing and is excluded)."""
    root = os.path.join(os.path.dirname(__file__), "..")
    env = dict(os.environ, SDL_VIDEODRIVER="dummy",
               SDL_AUDIODRIVER="dummy")
    sums = []
    for i, seed in enumerate(("1", "2")):
        env["PYTHONHASHSEED"] = seed
        rec = str(tmp_path / f"hash{i}.pkl")
        cmd = [sys.executable, "tools/doom_view.py", "E1M1", "--frames=24",
               f"--record={rec}"]
        out = subprocess.run(cmd, capture_output=True, text=True, cwd=root,
                             env=env, timeout=300).stdout
        line = next(l for l in out.splitlines() if "checksum" in l)
        sums.append(line.split("checksum")[1])
    assert sums[0] == sums[1]
