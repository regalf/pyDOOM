"""Tests for mus.py: every IWAD song parses and converts cleanly."""

import os

import pytest

from pydoom.mus import PERCUSSION, TPQN, mus_to_mid, parse_mus
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)

SONGS = ["D_E1M%d" % i for i in range(1, 10)] + ["D_INTRO", "D_INTER",
                                                 "D_VICTOR"]


def lumps():
    wad = WadFile(WAD_PATH)
    return {name: wad.read_lump(name) for name in SONGS}


@requires_wad
def test_all_songs_parse():
    for name, data in lumps().items():
        score = parse_mus(data)
        events = score["events"]
        assert events, name
        assert events[-1][1] == "end", name  # NOTE: scoreend terminates
        times = [t for t, _, _, _ in events]
        assert times == sorted(times), name  # NOTE: monotonic clock
        assert times[-1] > 0, name  # NOTE: a song, not a blip
        for _, kind, channel, _ in events:
            assert kind in ("off", "on", "bend", "sys", "program",
                            "ctrl", "end"), (name, kind)
            assert 0 <= channel <= 15, (name, channel)


@requires_wad
def test_conversion_is_valid_type0_midi():
    for name, data in lumps().items():
        mid = mus_to_mid(data)
        assert mid[:4] == b"MThd", name
        assert mid[4:8] == b"\x00\x00\x00\x06", name
        assert mid[8:10] == b"\x00\x00", name  # NOTE: format 0
        assert mid[10:12] == b"\x00\x01", name  # NOTE: one track
        assert mid[12:14] == TPQN.to_bytes(2, "big"), name
        assert mid[14:18] == b"MTrk", name
        track_len = int.from_bytes(mid[18:22], "big")
        assert 22 + track_len == len(mid), name  # NOTE: length honest
        assert mid[-3:] == b"\xff\x2f\x00", name  # NOTE: end of track


@requires_wad
def test_note_counts_survive_conversion():
    for name, data in lumps().items():
        score = parse_mus(data)
        want = sum(1 for _, kind, _, _ in score["events"]
                   if kind == "on")
        assert want > 10, name  # NOTE: real songs press keys
        mid = mus_to_mid(data)
        body = mid[22:-3]
        # NOTE: every presskey becomes 0x9n key vel (no running status).
        got = sum(1 for i in range(len(body) - 2)
                  if body[i] & 0xF0 == 0x90 and body[i + 1] & 0x80 == 0
                  and body[i + 2] & 0x80 == 0)
        assert got == want, (name, got, want)


@requires_wad
def test_drums_reach_midi_channel_9():
    wad = WadFile(WAD_PATH)
    data = wad.read_lump("D_E1M1")
    score = parse_mus(data)
    assert any(ch == PERCUSSION for _, kind, ch, _ in score["events"]
               if kind == "on")  # NOTE: E1M1 has drums
    mid = mus_to_mid(data)
    assert b"\x99" in mid  # NOTE: MUS 15 -> MIDI 9 note-ons


def test_bad_lumps_rejected():
    with pytest.raises(ValueError):
        parse_mus(b"junk")
    with pytest.raises(ValueError):
        parse_mus(b"MUS\x1a" + bytes(12))  # NOTE: header, no score


def _read_midi_events(mid: bytes):
    """Minimal type-0 reader (our writer uses explicit status bytes)."""
    assert mid[14:18] == b"MTrk"
    pos, end = 22, 22 + int.from_bytes(mid[18:22], "big")
    events = []
    time = 0
    while pos < end:
        delta, pos = _read_varlen(mid, pos)
        time += delta
        status = mid[pos]
        pos += 1
        if status == 0xFF:
            assert mid[pos] == 0x2F  # NOTE: only end-of-track metas
            events.append((time, "end"))
            pos += 2
        else:
            kind, ch = status & 0xF0, status & 0x0F
            a = mid[pos]
            pos += 1
            if kind in (0xC0, 0xD0):
                b = 0  # NOTE: program/aftertouch take one parameter
            else:
                b = mid[pos]
                pos += 1
            if kind == 0x90 and b == 0:
                kind = 0x80  # NOTE: reader-side courtesy only
            events.append((time, kind, ch, a, b))
    return events


def _read_varlen(mid: bytes, pos: int):
    value = 0
    while True:
        byte = mid[pos]
        pos += 1
        value = value * 128 + (byte & 0x7F)
        if not byte & 0x80:
            return value, pos


@requires_wad
def test_midi_round_trip_matches_score():
    wad = WadFile(WAD_PATH)
    data = wad.read_lump("D_E1M3")
    score = parse_mus(data)["events"]
    # NOTE: MUS allocation order is first-seen (drums aside).
    seen, mapping = [], {}
    for _, _, ch, _ in score:
        if ch not in seen:
            seen.append(ch)
    for i, ch in enumerate(seen):
        mapping[ch] = 9 if ch == PERCUSSION else i + (i >= 9)
    got = [e for e in _read_midi_events(mus_to_mid(data))
           if e[1] in (0x80, 0x90)]  # NOTE: skip setup controllers
    want = []
    for time, kind, channel, args in score:
        if kind == "off":
            want.append((time, 0x80, mapping[channel], args[0]))
        elif kind == "on":
            # NOTE: zero-velocity note-on reads back as note-off.
            want.append((time, 0x90 if args[1] else 0x80,
                         mapping[channel], args[0]))
    assert len(got) == len(want)
    for (time, gk, channel, key), (gt, ek, gc, ga, _gb) in zip(
            want, got):
        assert (time, gk, channel, key) == (gt, ek, gc, ga), time
