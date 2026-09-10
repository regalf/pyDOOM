"""Tests for oplmusic.py: tables, voices, scheduler, player thread."""

import os
import time

import pytest

from pydoom.genmidi import Instrument, Voice
from pydoom.oplmusic import (
    FREQUENCY_CURVE,
    VOLUME_MAP,
    MockBackend,
    MusicPlayer,
    Scheduler,
)

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def plain(feedback: int = 0):
    return Instrument(flags=0, fine_tuning=0, fixed_note=0,
                      voices=[Voice(feedback=feedback), Voice()], name="T")


def sched(events, n_main=1, feedback=0, volume=100):
    main = [plain(feedback) for _ in range(n_main)]
    perc = [plain() for _ in range(47)]
    back = MockBackend()
    return Scheduler(events, main, perc, back, volume), back


def keyons(back):
    """FREQ2 writes with the key-on bit set."""
    return [w for w in back.writes if w[0] & 0xF0 == 0xB0 and w[1] & 0x20]


def test_tables():
    assert len(FREQUENCY_CURVE) == 20 * 32 + 28
    assert FREQUENCY_CURVE[0] == 0x133
    assert FREQUENCY_CURVE[-1] == 0x36C  # NOTE: DMX buffer overrun
    assert len(VOLUME_MAP) == 128
    assert VOLUME_MAP[0] == 0 and VOLUME_MAP[127] == 127


def test_init_programs_chip():
    _, back = sched([])
    assert (0x01, 0x20) in back.writes  # NOTE: waveform enable
    assert (0x04, 0x80) in back.writes  # NOTE: timer reset pair
    assert (0xBD, 0x00) in back.writes  # NOTE: rhythm mode off
    assert (0x40, 0x3F) in back.writes  # NOTE: levels hot-muted


def test_note_on_off_keyon_bit():
    s, back = sched([(0, "on", 0, (60, 100)), (70, "off", 0, (60,)),
                     (140, "bend", 0, (64,))])
    s.advance(0.4)
    assert keyons(back)  # NOTE: voice singing
    n = len(back.writes)
    s.advance(0.4)
    assert len(back.writes) > n  # NOTE: keyoff writes
    assert not [v for v in s.alloced]  # NOTE: voice returned


def test_zero_velocity_means_off():
    s, back = sched([(0, "on", 0, (60, 100)), (70, "on", 0, (60, 0)),
                     (140, "bend", 0, (64,))])
    s.advance(0.6)
    assert not s.alloced


def test_percussion_uses_drum_table():
    main = [plain() for _ in range(128)]
    perc = [plain(0x07) for _ in range(47)]
    back = MockBackend()
    s = Scheduler([(0, "on", 15, (40, 100))], main, perc, back)
    s.advance(0.5)
    assert (0xC0, 0x07 | 0x30) in back.writes  # NOTE: drum feedback
    assert keyons(back)


def test_silence_maps_to_min_level():
    s, back = sched([(0, "on", 0, (60, 100))], volume=0)
    s.advance(0.5)
    levels = [v for r, v in back.writes if r == 0x40 + 0x03]
    assert levels and levels[-1] & 0x3F == 0x3F


def test_program_change_switches_instrument():
    main = [plain(0x00), plain(0x02)]
    back = MockBackend()
    s = Scheduler([(0, "program", 0, (1,)), (0, "on", 0, (60, 100))],
                  main, [plain() for _ in range(47)], back)
    s.advance(0.5)
    assert (0xC0, 0x02 | 0x30) in back.writes


def test_bend_retunes_live_voices():
    s, back = sched([(0, "on", 0, (60, 100)), (70, "bend", 0, (128,))])
    s.advance(0.4)
    n = len(back.writes)
    s.advance(0.4)
    assert len(back.writes) > n  # NOTE: fresh FREQ pair


def test_all_notes_off_releases():
    s, _ = sched([(0, "on", 0, (60, 100)), (0, "on", 1, (64, 100)),
                  (70, "sys", 0, (11,)), (140, "bend", 0, (64,))])
    s.advance(0.4)
    assert len(s.alloced) == 2
    s.advance(0.4)
    assert len(s.alloced) == 1  # NOTE: only channel 0 went quiet


def test_ninth_voice_steals():
    evts = [(0, "on", ch % 15, (60 + ch, 100)) for ch in range(12)]
    s, _ = sched(evts)
    s.advance(0.5)
    assert len(s.alloced) == 9  # NOTE: OPL2 has nine voices


def test_song_loops():
    s, back = sched([(0, "on", 0, (60, 100)), (70, "off", 0, (60,))])
    s.advance(2.0)  # NOTE: past the 0.5s song, twice around
    assert s.base > 0
    assert len(keyons(back)) >= 2


def test_scheduler_deterministic():
    evts = [(0, "on", 0, (60, 100)), (35, "bend", 0, (100,)),
            (70, "off", 0, (60,)), (70, "on", 15, (40, 90))]
    logs = []
    for _ in range(2):
        s, back = sched(evts)
        s.advance(2.0)
        logs.append(list(back.writes))
    assert logs[0] == logs[1]


@requires_wad
def test_real_song_drives_voices():
    from pydoom.genmidi import parse_genmidi
    from pydoom.mus import parse_mus
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    main, perc = parse_genmidi(wad.read_lump("GENMIDI"))
    events = parse_mus(wad.read_lump("D_E1M1"))["events"]
    back = MockBackend()
    s = Scheduler(events, main, perc, back)
    drums = False
    for _ in range(40):
        s.advance(0.25)
        drums |= any(v["channel"] == 15 for v in s.alloced)
    assert len(back.writes) > 1000
    assert keyons(back)
    assert drums  # NOTE: E1M1 keeps its drummer busy


def _tiny_mus():
    # NOTE: on@0, off@70 (delay follows the last-flagged event), end.
    score = bytes((0x90, 60, 0x46, 0x80, 60, 0x00, 0xE0))
    head = b"MUS\x1a" + (7).to_bytes(2, "little") + (14).to_bytes(
        2, "little") + (1).to_bytes(2, "little") * 3
    return head + score


def test_player_streams_chunks():
    main = [plain() for _ in range(128)]
    perc = [plain() for _ in range(47)]
    player = MusicPlayer(MockBackend(), chunk_sec=0.05)
    try:
        player.play_song(_tiny_mus(), main, perc)
        chunk = None
        for _ in range(100):
            chunk = player.pump() or chunk
            if chunk is not None:
                break
            time.sleep(0.02)
        assert chunk is not None
        assert abs(len(chunk) - int(0.05 * 11025)) <= 2  # NOTE: slicing
        player.set_volume(64)
        player.set_muted(True)
        player.stop()
    finally:
        player.close()


def test_short_notes_survive_chunk():
    """Two quick notes inside one chunk both reach the render (the
    old collapse-to-chunk-end swallowed the first)."""

    class RenderVoices(MockBackend):
        def render(self, n):
            import numpy as np
            keys = sorted(v["key"] for v in sched.alloced)
            val = keys[0] + 1 if keys else 0
            return np.full(n, val, dtype=np.int16)

    back = RenderVoices()
    main = [plain() for _ in range(128)]
    perc = [plain() for _ in range(47)]
    sched = Scheduler([(0, "on", 0, (60, 100)),
                       (17, "off", 0, (60,)),
                       (18, "on", 0, (64, 100)),
                       (35, "off", 0, (64,))], main, perc, back)
    pcm = sched.render_chunk(back, 11025, 2756)
    assert 61 in pcm  # NOTE: first note rang before the second struck
    assert 65 in pcm


def test_degenerate_song_terminates():
    """All events at tick 0 (or none): render whole span, never spin."""
    back = MockBackend()
    main = [plain() for _ in range(128)]
    perc = [plain() for _ in range(47)]
    sched = Scheduler([(0, "on", 0, (60, 100))], main, perc, back)
    pcm = sched.render_chunk(back, 11025, 100)
    assert len(pcm) == 100
    sched2 = Scheduler([], main, perc, MockBackend())
    assert len(sched2.render_chunk(MockBackend(), 11025, 100)) == 100


def test_pyopl_backend_tolerates_tiny_fills():
    """Thread-crash regression: sub-2-sample segments (tight events)
    clamp to PyOPL's minimum instead of raising in the worker."""
    pyopl = pytest.importorskip("pyopl")
    from pydoom.oplmusic import PyOplBackend
    back = PyOplBackend()
    assert len(back.render(1)) == 1
    assert len(back.render(2)) == 2
    assert len(back.render(513)) == 513


@requires_wad
def test_loop_wrap_terminates_and_replays():
    """Float dust at the loop wrap used to spin render_chunk forever
    (worker at 100% CPU, music stopping after one pass)."""
    from pydoom.genmidi import parse_genmidi
    from pydoom.mus import parse_mus
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    main, perc = parse_genmidi(wad.read_lump("GENMIDI"))
    events = parse_mus(wad.read_lump("D_INTRO"))["events"]
    back = MockBackend()
    sched = Scheduler(events, main, perc, back)
    ntarget = int((sched.total + 0.5) * 11025)
    pcm = sched.render_chunk(back, 11025, ntarget)
    assert len(pcm) == ntarget  # NOTE: chunks come out exact
    assert sched.base > 0  # NOTE: wrapped around at least once
