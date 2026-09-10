"""DMX MUS music lumps (mus2mid.c idea): parse scores, emit MIDI.

parse_mus() keeps MUS-level semantics (raw channels, percussion = 15)
for the OPL scheduler; mus_to_mid() ports the doomgeneric converter
verbatim (type-0 single track, sequential channels skipping 9, default
velocity 127, all-notes-off on first channel use) for MIDI consumers
and offline renders. stdlib only.
"""

MUS_MAGIC = b"MUS\x1a"
TPQN = 70  # MIDI division the converter stamps (delays pass through)
PERCUSSION = 15  # MUS drum channel (MIDI 9 after conversion)

# MUS event nibbles (mus_releasekey .. mus_scoreend).
_EV_OFF, _EV_ON, _EV_BEND, _EV_SYS, _EV_CTRL, _EV_END = 0, 1, 2, 3, 4, 6

# MUS controller number -> MIDI controller ( doomgeneric table ).
CONTROLLER_MAP = (0x00, 0x20, 0x01, 0x07, 0x0A, 0x0B, 0x5B, 0x5D,
                  0x40, 0x43, 0x78, 0x7B, 0x7E, 0x7F, 0x79)

_MIDI_PERCUSSION = 9


def _u16(data: bytes, pos: int) -> int:
    return data[pos] | (data[pos + 1] << 8)


def parse_mus(data: bytes) -> dict:
    """Score events with absolute MUS-tick times.

    Events are (time, kind, channel, args) with kinds off(key),
    on(key, velocity), bend(raw byte), sys(number), program(number),
    ctrl(number, value) and end(). Raises ValueError on bad data.
    """
    if len(data) < 16 or data[:4] != MUS_MAGIC:
        raise ValueError("not a MUS lump")
    score_len = _u16(data, 4)
    score_start = _u16(data, 6)
    pos = score_start
    end = score_start + score_len
    if end > len(data):
        raise ValueError("MUS score overruns lump")
    events: list = []
    time = 0
    velocities = [127] * 16
    while True:
        if pos >= end:
            raise ValueError("MUS ends without scoreend")
        while True:
            if pos >= end:
                raise ValueError("MUS ends without scoreend")
            desc = data[pos]
            pos += 1
            channel = desc & 0x0F
            ev = (desc & 0x70) >> 4
            if ev == _EV_END:
                events.append((time, "end", channel, ()))
                return {"channels": _u16(data, 8),
                        "instruments": _u16(data, 12),
                        "events": events}
            if ev == _EV_OFF:
                key = data[pos]
                pos += 1
                events.append((time, "off", channel, (key & 0x7F,)))
            elif ev == _EV_ON:
                key = data[pos]
                pos += 1
                if key & 0x80:
                    velocities[channel] = data[pos] & 0x7F
                    pos += 1
                events.append((time, "on", channel,
                               (key & 0x7F, velocities[channel])))
            elif ev == _EV_BEND:
                raw = data[pos]
                pos += 1
                events.append((time, "bend", channel, (raw * 64,)))
            elif ev == _EV_SYS:
                num = data[pos]
                pos += 1
                if num < 10 or num > 14:
                    raise ValueError(f"bad MUS sysevent {num}")
                events.append((time, "sys", channel, (num,)))
            elif ev == _EV_CTRL:
                num = data[pos]
                pos += 1
                val = data[pos]
                pos += 1
                if num == 0:
                    events.append((time, "program", channel,
                                   (val & 0x7F,)))
                else:
                    if num < 1 or num > 9:
                        raise ValueError(f"bad MUS controller {num}")
                    events.append((time, "ctrl", channel, (num, val)))
            else:
                raise ValueError(f"bad MUS event {ev}")
            if desc & 0x80:
                break
        delay = 0
        while True:
            if pos >= end:
                raise ValueError("MUS ends inside delay")
            byte = data[pos]
            pos += 1
            delay = delay * 128 + (byte & 0x7F)
            if not byte & 0x80:
                break
        time += delay


def _varlen(out: bytearray, value: int) -> None:
    buf = value & 0x7F
    value >>= 7
    while value:
        buf <<= 8
        buf |= (value & 0x7F) | 0x80
        value >>= 7
    while True:
        out.append(buf & 0xFF)
        if buf & 0x80:
            buf >>= 8
        else:
            return


def mus_to_mid(data: bytes) -> bytes:
    """doomgeneric mus2mid: MUS lump -> type-0 single-track MIDI bytes."""
    score = parse_mus(data)["events"]
    out = bytearray(b"MThd\x00\x00\x00\x06\x00\x00\x00\x01")
    out += bytes((TPQN >> 8, TPQN & 0xFF))
    track = bytearray()
    chan_map = [-1] * 16
    velocities = [127] * 16
    pending = 0

    def alloc(mus_channel: int) -> int:
        if mus_channel == PERCUSSION:
            return _MIDI_PERCUSSION
        if chan_map[mus_channel] == -1:
            top = -1
            for i in range(16):
                top = max(top, chan_map[i])
            nxt = top + 1
            if nxt == _MIDI_PERCUSSION:
                nxt += 1
            chan_map[mus_channel] = nxt
            _event(0xB0 | nxt, 0x7B, 0)  # NOTE: all-notes-off first
        return chan_map[mus_channel]

    def _event(*mid: int) -> None:
        nonlocal pending, track
        _varlen(track, pending)
        pending = 0
        track += bytes(mid)

    last_time = 0
    for time, kind, channel, args in score:
        if kind == "end":
            break
        midi_ch = alloc(channel)
        pending += time - last_time
        last_time = time
        if kind == "off":
            _event(0x80 | midi_ch, args[0], 0)
        elif kind == "on":
            _event(0x90 | midi_ch, args[0], args[1])
        elif kind == "bend":
            _event(0xE0 | midi_ch, args[0] & 0x7F,
                   (args[0] >> 7) & 0x7F)
        elif kind == "sys":
            _event(0xB0 | midi_ch, CONTROLLER_MAP[args[0]], 0)
        elif kind == "program":
            _event(0xC0 | midi_ch, args[0])
        elif kind == "ctrl":
            _event(0xB0 | midi_ch, CONTROLLER_MAP[args[0]],
                   min(args[1], 0x7F))
    _varlen(track, pending)
    track += b"\xff\x2f\x00"
    out += b"MTrk" + len(track).to_bytes(4, "big") + bytes(track)
    return bytes(out)
