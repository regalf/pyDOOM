"""Tests for demo.py + flow.init_new + nomonsters spawn (milestone E)."""

import os

import pytest

from pydoom import demo
from pydoom.demo import DemoHeader, DemoReader, DemoWriter
from pydoom.ticcmd import Ticcmd

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def test_header_roundtrip():
    hdr = DemoHeader(skill=4, episode=1, map=3, deathmatch=0, respawn=1,
                     fast=0, nomonsters=0, consoleplayer=0,
                     players=(1, 0, 0, 0))
    back = DemoHeader.from_bytes(hdr.to_bytes())
    assert back == hdr
    assert len(hdr.to_bytes()) == demo.HEADER_LEN == 13
    assert hdr.to_bytes()[0] == demo.VERSION == 109
    assert back.skill_name() == "nightmare"
    assert back.marker() == "E1M3"
    assert back.single_player()


def test_header_refusals():
    with pytest.raises(ValueError):
        DemoHeader.from_bytes(b"\x6c" + bytes(12))  # wrong version
    with pytest.raises(ValueError):
        DemoHeader.from_bytes(bytes(5))  # truncated
    multi = DemoHeader(players=(1, 1, 0, 0))
    assert not multi.single_player()
    dm = DemoHeader(deathmatch=1)
    assert not dm.single_player()


def test_stream_roundtrip_with_marker():
    cmds = [Ticcmd(50, -40, 12288, 3), Ticcmd(), Ticcmd(-25, 0, -256, 2)]
    writer = DemoWriter(DemoHeader())
    for cmd in cmds:
        writer.append(cmd)
    blob = writer.finish()
    assert blob[-1] == demo.DEMOMARKER == 0x80
    reader = DemoReader(blob)
    assert reader.header == DemoHeader()
    first = reader.read_cmd()
    assert first == cmds[0]  # 256-grid angles round-trip exactly
    # NOTE: demo angle rides at 256-unit steps (vanilla quantize).
    assert Ticcmd.unpack(Ticcmd(50, 0, 12345, 0).pack()).angleturn == (
        ((12345 + 128) >> 8) << 8)
    assert reader.read_cmd() == cmds[1]
    assert reader.read_cmd() == cmds[2]
    assert reader.read_cmd() is None  # DEMOMARKER ends the stream
    assert reader.tics == 3


def test_reader_truncated_tail_ends_cleanly():
    blob = DemoHeader().to_bytes() + bytes((10, 20))  # half a packet
    assert DemoReader(blob).read_cmd() is None


def test_init_new_fast_tables_toggle():
    """G_InitNew: nightmare/fast halves sarge tics + fast missiles,
    leaving restores pristine values (idempotent, test-safe)."""
    from pydoom import flow
    from pydoom.fixed import FRACUNIT
    from pydoom.info import MOBJ_TYPES, MT_INDEX, STATES, STATE_INDEX
    first = STATE_INDEX["S_SARG_RUN1"]
    last = STATE_INDEX["S_SARG_PAIN2"]
    saved_tics = [STATES[i][2] for i in range(first, last + 1)]
    saved_shot = MOBJ_TYPES[MT_INDEX["TROOPSHOT"]][10]
    saved_bruis = MOBJ_TYPES[MT_INDEX["BRUISERSHOT"]][10]
    try:
        flow.init_new("nightmare", False)
        for i in range(first, last + 1):
            assert STATES[i][2] == saved_tics[i - first] >> 1
        assert MOBJ_TYPES[MT_INDEX["TROOPSHOT"]][10] == 20 * FRACUNIT
        assert MOBJ_TYPES[MT_INDEX["BRUISERSHOT"]][10] == 20 * FRACUNIT
        flow.init_new("normal", True)  # fast flag alone also enables
        for i in range(first, last + 1):
            assert STATES[i][2] == saved_tics[i - first] >> 1
    finally:
        flow.init_new("normal", False)
    for i in range(first, last + 1):
        assert STATES[i][2] == saved_tics[i - first]
    assert MOBJ_TYPES[MT_INDEX["TROOPSHOT"]][10] == saved_shot
    assert MOBJ_TYPES[MT_INDEX["BRUISERSHOT"]][10] == saved_bruis


@requires_wad
def test_lmp_roundtrip_is_deterministic(tmp_path):
    """Scripted inputs -> .lmp twice -> byte-identical; timedemo twice ->
    identical end stats (record/playback machine self-consistency)."""
    import pickle
    import subprocess
    import sys
    root = os.path.join(os.path.dirname(__file__), "..")
    env = dict(os.environ, SDL_VIDEODRIVER="dummy",
               SDL_AUDIODRIVER="dummy")
    script = tmp_path / "fwd.pkl"
    with open(script, "wb") as f:
        pickle.dump([{"ev": [],
                      "mv": [True, False, False, False,
                             False, False, False, False]}
                     for _ in range(90)], f)

    def run(*args):
        cmd = [sys.executable, "tools/doom_view.py", *args]
        out = subprocess.run(cmd, capture_output=True, text=True,
                             cwd=root, env=env, timeout=300)
        return out.stdout

    lmps = []
    for i in (1, 2):
        rec = str(tmp_path / f"move{i}.lmp")
        run("E1M1", f"--play={script}", f"--record-demo={rec}",
            "--frames=90")
        lmps.append(open(rec, "rb").read())
    assert lmps[0] == lmps[1]  # byte-identical re-records
    stats = [run(f"--timedemo={tmp_path / 'move1.lmp'}")
             for _ in range(2)]
    ends = [[l for l in s.splitlines() if l.startswith("timedemo:")]
            for s in stats]
    assert ends[0] and ends[0] == ends[1]
    assert "52 tics end E1M1" in ends[0][0]


@requires_wad
def test_nomonsters_spawn_skips_kill_types():
    from pydoom.info import MF_FLAGS
    from pydoom.mapdata import Map
    from pydoom.mobjs import ThingIndex, spawn_map
    from pydoom.physics import Physics
    from pydoom.wad import WadFile
    game_map = Map.from_wad(WadFile(WAD_PATH), "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    full = spawn_map(game_map, phys, index, "normal", False)
    assert any(mo.flags & MF_FLAGS["MF_COUNTKILL"] for mo in full)
    index2 = ThingIndex(game_map)
    phys.things = index2
    bare = spawn_map(game_map, phys, index2, "normal", True)
    assert not any(mo.flags & MF_FLAGS["MF_COUNTKILL"] for mo in bare)
