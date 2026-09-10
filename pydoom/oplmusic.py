"""OPL music (Chocolate Doom i_oplmusic.c idea, opl_doom_1_9 voice
behavior, OPL2 like vanilla DMX): MUS events plus GENMIDI instruments
drive 9 FM voices; a backend renders PCM; MusicPlayer streams it from
a worker thread so frame hitches never gap the song.

Tables (frequency curve, volume map, voice operators) are ported from
Chocolate Doom's driver (GPL-2+), which took them from id's DMX data.
The Scheduler is pure Python and fully unit-tested with a recording
backend; PyOplBackend (pip PyOPL, DOSBox synth, GPL-3.0) renders real
audio and is imported lazily, so music degrades to silence without it.
"""

import queue
import threading

verbose = False  # terminal chatter (viewer sets it from --debug)

TICK_HZ = 140  # MUS delay units per second (DMX score clock)
SAMPLE_RATE = 11025  # matches the SFX mixer (Sound Blaster did the same)
CHUNK_SEC = 0.25  # streaming slice (queue holds ~4 s against hitches)
N_VOICES = 9  # OPL2, like vanilla DMX (no OPL3 second array)
PERCUSSION_CH = 15  # MUS drum channel (MIDI 9 after mus2mid)

# OPL2 register bases (hardware map; operators add voice_operators).
REG_TREMOLO, REG_LEVEL = 0x20, 0x40
REG_ATTACK, REG_SUSTAIN = 0x60, 0x80
REG_FREQ1, REG_FREQ2 = 0xA0, 0xB0
REG_FEEDBACK, REG_WAVEFORM = 0xC0, 0xE0

VOICE_OPERATORS = (
    (0x00, 0x01, 0x02, 0x08, 0x09, 0x0A, 0x10, 0x11, 0x12),
    (0x03, 0x04, 0x05, 0x0B, 0x0C, 0x0D, 0x13, 0x14, 0x15),
)

# F-number curve per 1/32 semitone (DMX data, overrun quirk included).
FREQUENCY_CURVE = (
    # -1
    0x133, 0x133, 0x134, 0x134, 0x135, 0x136, 0x136, 0x137,
    0x137, 0x138, 0x138, 0x139, 0x139, 0x13A, 0x13B, 0x13B,
    0x13C, 0x13C, 0x13D, 0x13D, 0x13E, 0x13F, 0x13F, 0x140,
    0x140, 0x141, 0x142, 0x142, 0x143, 0x143, 0x144, 0x144,
    # -2
    0x145, 0x146, 0x146, 0x147, 0x147, 0x148, 0x149, 0x149,
    0x14A, 0x14A, 0x14B, 0x14C, 0x14C, 0x14D, 0x14D, 0x14E,
    0x14F, 0x14F, 0x150, 0x150, 0x151, 0x152, 0x152, 0x153,
    0x153, 0x154, 0x155, 0x155, 0x156, 0x157, 0x157, 0x158,
    # 0
    0x158, 0x159, 0x15A, 0x15A, 0x15B, 0x15B, 0x15C, 0x15D,
    0x15D, 0x15E, 0x15F, 0x15F, 0x160, 0x161, 0x161, 0x162,
    0x162, 0x163, 0x164, 0x164, 0x165, 0x166, 0x166, 0x167,
    0x168, 0x168, 0x169, 0x16A, 0x16A, 0x16B, 0x16C, 0x16C,
    # 1
    0x16D, 0x16E, 0x16E, 0x16F, 0x170, 0x170, 0x171, 0x172,
    0x172, 0x173, 0x174, 0x174, 0x175, 0x176, 0x176, 0x177,
    0x178, 0x178, 0x179, 0x17A, 0x17A, 0x17B, 0x17C, 0x17C,
    0x17D, 0x17E, 0x17E, 0x17F, 0x180, 0x181, 0x181, 0x182,
    # 2
    0x183, 0x183, 0x184, 0x185, 0x185, 0x186, 0x187, 0x188,
    0x188, 0x189, 0x18A, 0x18A, 0x18B, 0x18C, 0x18D, 0x18D,
    0x18E, 0x18F, 0x18F, 0x190, 0x191, 0x192, 0x192, 0x193,
    0x194, 0x194, 0x195, 0x196, 0x197, 0x197, 0x198, 0x199,
    # 3
    0x19A, 0x19A, 0x19B, 0x19C, 0x19D, 0x19D, 0x19E, 0x19F,
    0x1A0, 0x1A0, 0x1A1, 0x1A2, 0x1A3, 0x1A3, 0x1A4, 0x1A5,
    0x1A6, 0x1A6, 0x1A7, 0x1A8, 0x1A9, 0x1A9, 0x1AA, 0x1AB,
    0x1AC, 0x1AD, 0x1AD, 0x1AE, 0x1AF, 0x1B0, 0x1B0, 0x1B1,
    # 4
    0x1B2, 0x1B3, 0x1B4, 0x1B4, 0x1B5, 0x1B6, 0x1B7, 0x1B8,
    0x1B8, 0x1B9, 0x1BA, 0x1BB, 0x1BC, 0x1BC, 0x1BD, 0x1BE,
    0x1BF, 0x1C0, 0x1C0, 0x1C1, 0x1C2, 0x1C3, 0x1C4, 0x1C4,
    0x1C5, 0x1C6, 0x1C7, 0x1C8, 0x1C9, 0x1C9, 0x1CA, 0x1CB,
    # 5
    0x1CC, 0x1CD, 0x1CE, 0x1CE, 0x1CF, 0x1D0, 0x1D1, 0x1D2,
    0x1D3, 0x1D3, 0x1D4, 0x1D5, 0x1D6, 0x1D7, 0x1D8, 0x1D8,
    0x1D9, 0x1DA, 0x1DB, 0x1DC, 0x1DD, 0x1DE, 0x1DE, 0x1DF,
    0x1E0, 0x1E1, 0x1E2, 0x1E3, 0x1E4, 0x1E5, 0x1E5, 0x1E6,
    # 6
    0x1E7, 0x1E8, 0x1E9, 0x1EA, 0x1EB, 0x1EC, 0x1ED, 0x1ED,
    0x1EE, 0x1EF, 0x1F0, 0x1F1, 0x1F2, 0x1F3, 0x1F4, 0x1F5,
    0x1F6, 0x1F6, 0x1F7, 0x1F8, 0x1F9, 0x1FA, 0x1FB, 0x1FC,
    0x1FD, 0x1FE, 0x1FF, 0x200, 0x201, 0x201, 0x202, 0x203,
    # 7
    0x204, 0x205, 0x206, 0x207, 0x208, 0x209, 0x20A, 0x20B,
    0x20C, 0x20D, 0x20E, 0x20F, 0x210, 0x210, 0x211, 0x212,
    0x213, 0x214, 0x215, 0x216, 0x217, 0x218, 0x219, 0x21A,
    0x21B, 0x21C, 0x21D, 0x21E, 0x21F, 0x220, 0x221, 0x222,
    # 8
    0x223, 0x224, 0x225, 0x226, 0x227, 0x228, 0x229, 0x22A,
    0x22B, 0x22C, 0x22D, 0x22E, 0x22F, 0x230, 0x231, 0x232,
    0x233, 0x234, 0x235, 0x236, 0x237, 0x238, 0x239, 0x23A,
    0x23B, 0x23C, 0x23D, 0x23E, 0x23F, 0x240, 0x241, 0x242,
    # 9
    0x244, 0x245, 0x246, 0x247, 0x248, 0x249, 0x24A, 0x24B,
    0x24C, 0x24D, 0x24E, 0x24F, 0x250, 0x251, 0x252, 0x253,
    0x254, 0x256, 0x257, 0x258, 0x259, 0x25A, 0x25B, 0x25C,
    0x25D, 0x25E, 0x25F, 0x260, 0x262, 0x263, 0x264, 0x265,
    # 10
    0x266, 0x267, 0x268, 0x269, 0x26A, 0x26C, 0x26D, 0x26E,
    0x26F, 0x270, 0x271, 0x272, 0x273, 0x275, 0x276, 0x277,
    0x278, 0x279, 0x27A, 0x27B, 0x27D, 0x27E, 0x27F, 0x280,
    0x281, 0x282, 0x284, 0x285, 0x286, 0x287, 0x288, 0x289,
    # 11
    0x28B, 0x28C, 0x28D, 0x28E, 0x28F, 0x290, 0x292, 0x293,
    0x294, 0x295, 0x296, 0x298, 0x299, 0x29A, 0x29B, 0x29C,
    0x29E, 0x29F, 0x2A0, 0x2A1, 0x2A2, 0x2A4, 0x2A5, 0x2A6,
    0x2A7, 0x2A9, 0x2AA, 0x2AB, 0x2AC, 0x2AE, 0x2AF, 0x2B0,
    # 12
    0x2B1, 0x2B2, 0x2B4, 0x2B5, 0x2B6, 0x2B7, 0x2B9, 0x2BA,
    0x2BB, 0x2BD, 0x2BE, 0x2BF, 0x2C0, 0x2C2, 0x2C3, 0x2C4,
    0x2C5, 0x2C7, 0x2C8, 0x2C9, 0x2CB, 0x2CC, 0x2CD, 0x2CE,
    0x2D0, 0x2D1, 0x2D2, 0x2D4, 0x2D5, 0x2D6, 0x2D8, 0x2D9,
    # 13
    0x2DA, 0x2DC, 0x2DD, 0x2DE, 0x2E0, 0x2E1, 0x2E2, 0x2E4,
    0x2E5, 0x2E6, 0x2E8, 0x2E9, 0x2EA, 0x2EC, 0x2ED, 0x2EE,
    0x2F0, 0x2F1, 0x2F2, 0x2F4, 0x2F5, 0x2F6, 0x2F8, 0x2F9,
    0x2FB, 0x2FC, 0x2FD, 0x2FF, 0x300, 0x302, 0x303, 0x304,
    # 14
    0x306, 0x307, 0x309, 0x30A, 0x30B, 0x30D, 0x30E, 0x310,
    0x311, 0x312, 0x314, 0x315, 0x317, 0x318, 0x31A, 0x31B,
    0x31C, 0x31E, 0x31F, 0x321, 0x322, 0x324, 0x325, 0x327,
    0x328, 0x329, 0x32B, 0x32C, 0x32E, 0x32F, 0x331, 0x332,
    # 15
    0x334, 0x335, 0x337, 0x338, 0x33A, 0x33B, 0x33D, 0x33E,
    0x340, 0x341, 0x343, 0x344, 0x346, 0x347, 0x349, 0x34A,
    0x34C, 0x34D, 0x34F, 0x350, 0x352, 0x353, 0x355, 0x357,
    0x358, 0x35A, 0x35B, 0x35D, 0x35E, 0x360, 0x361, 0x363,
    # 16
    0x365, 0x366, 0x368, 0x369, 0x36B, 0x36C, 0x36E, 0x370,
    0x371, 0x373, 0x374, 0x376, 0x378, 0x379, 0x37B, 0x37C,
    0x37E, 0x380, 0x381, 0x383, 0x384, 0x386, 0x388, 0x389,
    0x38B, 0x38D, 0x38E, 0x390, 0x392, 0x393, 0x395, 0x397,
    # 17
    0x398, 0x39A, 0x39C, 0x39D, 0x39F, 0x3A1, 0x3A2, 0x3A4,
    0x3A6, 0x3A7, 0x3A9, 0x3AB, 0x3AC, 0x3AE, 0x3B0, 0x3B1,
    0x3B3, 0x3B5, 0x3B7, 0x3B8, 0x3BA, 0x3BC, 0x3BD, 0x3BF,
    0x3C1, 0x3C3, 0x3C4, 0x3C6, 0x3C8, 0x3CA, 0x3CB, 0x3CD,
    # 18 (incomplete range plus the famous buffer overrun)
    0x3CF, 0x3D1, 0x3D2, 0x3D4, 0x3D6, 0x3D8, 0x3DA, 0x3DB,
    0x3DD, 0x3DF, 0x3E1, 0x3E3, 0x3E4, 0x3E6, 0x3E8, 0x3EA,
    0x3EC, 0x3ED, 0x3EF, 0x3F1, 0x3F3, 0x3F5, 0x3F6, 0x3F8,
    0x3FA, 0x3FC, 0x3FE, 0x36C,
)

# MIDI volume (0-127) to OPL level attenuation.
VOLUME_MAP = (
    0, 1, 3, 5, 6, 8, 10, 11,
    13, 14, 16, 17, 19, 20, 22, 23,
    25, 26, 27, 29, 30, 32, 33, 34,
    36, 37, 39, 41, 43, 45, 47, 49,
    50, 52, 54, 55, 57, 59, 60, 61,
    63, 64, 66, 67, 68, 69, 71, 72,
    73, 74, 75, 76, 77, 79, 80, 81,
    82, 83, 84, 84, 85, 86, 87, 88,
    89, 90, 91, 92, 92, 93, 94, 95,
    96, 96, 97, 98, 99, 99, 100, 101,
    101, 102, 103, 103, 104, 105, 105, 106,
    107, 107, 108, 109, 109, 110, 110, 111,
    112, 112, 113, 113, 114, 114, 115, 115,
    116, 117, 117, 118, 118, 119, 119, 120,
    120, 121, 121, 122, 122, 123, 123, 123,
    124, 124, 125, 125, 126, 126, 127, 127,
)


def _new_channel(instr0):
    """InitChannel: piano, full-ish volume, centered, unbended."""
    return {"instrument": instr0, "volume": 100, "volume_base": 100,
            "bend": 0}


def _new_voice(index):
    return {"index": index, "op1": VOICE_OPERATORS[0][index],
            "op2": VOICE_OPERATORS[1][index], "instr": None,
            "instr_voice": 0, "channel": None, "key": 0, "note": 0,
            "freq": 0, "note_volume": 0, "car_volume": 0,
            "mod_volume": 0, "priority": 0}


class Scheduler:
    """MUS score plus GENMIDI voices to an OPL register stream.

    Pure Python and deterministic: same events in, same writes out.
    OPL2 (9 voices, no stereo pan) like vanilla DMX.
    """

    def __init__(self, events, main_instrs, perc_instrs, backend,
                 volume: int = 100) -> None:
        self.events = [(t / TICK_HZ, kind, ch, args)
                       for t, kind, ch, args in events
                       if kind != "end"]
        self.total = (self.events[-1][0] if self.events else 0.0)
        self.main = main_instrs
        self.perc = perc_instrs
        self.backend = backend
        self.music_volume = max(0, min(127, volume))
        self.start_volume = self.music_volume
        self.channels = [_new_channel(main_instrs[0]) for _ in range(16)]
        for ch in self.channels:  # NOTE: InitChannel volume home
            ch["volume"] = min(self.music_volume, ch["volume_base"])
        self.voices = [_new_voice(i) for i in range(N_VOICES)]
        self.free = list(self.voices)
        self.alloced: list = []
        self.pos = 0
        self.base = 0.0
        self.now = 0.0
        self._init_chip()

    # -- setup --

    def _init_chip(self) -> None:
        write = self.backend.write_reg
        # NOTE: OPL_InitRegisters (OPL2 branch): levels hot-muted,
        # everything else zeroed, timers reset, waveforms enabled.
        for reg in range(REG_LEVEL, REG_LEVEL + 22):
            write(reg, 0x3F)
        for reg in range(REG_ATTACK, REG_WAVEFORM + 22):
            write(reg, 0x00)
        for reg in range(1, REG_LEVEL):
            write(reg, 0x00)
        write(0x04, 0x60)
        write(0x04, 0x80)
        write(0x01, 0x20)

    def set_music_volume(self, volume: int) -> None:
        """I_OPL_SetMusicVolume: master, drums track it directly."""
        volume = max(0, min(127, volume))
        if volume == self.music_volume:
            return
        self.music_volume = volume
        for i, ch in enumerate(self.channels):
            if i == PERCUSSION_CH:
                self._channel_volume(i, volume)
            else:
                self._channel_volume(i, ch["volume_base"])

    def restart(self) -> None:
        """RestartSong: channels home, score rewound (songs loop)."""
        self.pos = 0
        self.base = self.now
        self.start_volume = self.music_volume
        for i in range(16):
            self.channels[i] = _new_channel(self.main[0])

    def stop(self) -> None:
        """I_OPL_StopSong: every voice off, score rewound."""
        for i in range(16):
            self._all_notes_off(i)
        self.pos = 0
        self.base = self.now

    # -- time --

    def advance(self, seconds: float) -> None:
        """Fire due score events; loop the song past its end."""
        self.now += seconds
        while True:
            while self.pos < len(self.events) \
                    and self.events[self.pos][0] <= self.now - self.base:
                _, kind, channel, args = self.events[self.pos]
                self.pos += 1
                self._fire(kind, channel, args)
            if self.total <= 0 or self.now - self.base < self.total:
                return
            self.base += self.total
            self.pos = 0
            self.start_volume = self.music_volume
            for i in range(16):
                self.channels[i] = _new_channel(self.main[0])

    def next_time(self) -> float:
        """Absolute song time of the next score event (loop-aware)."""
        if self.pos < len(self.events):
            return self.base + self.events[self.pos][0]
        return self.base + self.total

    def render_chunk(self, backend, rate: int, ntarget: int):
        """Event-exact PCM for ~ntarget samples: slice the span at
        score events (Chocolate schedules per-event callbacks) so
        short notes never collapse into the chunk-end state."""
        import numpy as np
        end = self.now + ntarget / rate
        if self.total <= 0:
            # NOTE: degenerate song (all events at tick 0, or empty):
            # fire them, render the span whole, never loop-spin.
            self.advance(end - self.now)
            return backend.render(ntarget)
        parts: list = []
        while self.now < end - 1e-9:
            seg = min(max(self.next_time() - self.now, 0.0),
                      end - self.now)
            nsamp = int(round(seg * rate))
            if nsamp > 0:
                parts.append(backend.render(nsamp))
            self.advance(seg if nsamp == 0 else nsamp / rate)
        if not parts:
            return np.zeros(0, dtype=np.int16)
        return parts[0] if len(parts) == 1 else np.concatenate(parts)

    def _fire(self, kind: str, channel: int, args: tuple) -> None:
        if kind == "on":
            key, vel = args
            if vel <= 0:
                self._key_off(channel, key)
            else:
                self._key_on(channel, key, key, vel)
        elif kind == "off":
            self._key_off(channel, args[0])
        elif kind == "program":
            self.channels[channel]["instrument"] = self.main[args[0] % 128]
        elif kind == "ctrl":
            num, val = args
            if num == 3:  # NOTE: MUS volume
                self._channel_volume(channel, val, clip_start=True)
            # NOTE: pan/expression/reverb stay OPL2-mono, like DMX.
        elif kind == "sys":
            if args[0] == 11:  # NOTE: MUS all-notes-off
                self._all_notes_off(channel)
        elif kind == "bend":
            self.channels[channel]["bend"] = (args[0] >> 7) - 64
            for voice in self.alloced:
                if voice["channel"] == channel:
                    self._update_freq(voice)

    # -- voices --

    def _get_free(self):
        if not self.free:
            return None
        voice = self.free.pop(0)
        self.alloced.append(voice)
        return voice

    def _release(self, index: int) -> None:
        # NOTE: the 1.666 guard (index past the end resets the lists).
        if index >= len(self.alloced):
            self.alloced = []
            self.free = list(self.voices)
            return
        voice = self.alloced.pop(index)
        self._write(REG_FREQ2 + voice["index"], voice["freq"] >> 8)
        voice["channel"] = None
        voice["note"] = 0
        self.free.append(voice)  # NOTE: freed voices queue at the end

    def _replace(self) -> None:
        """ReplaceExistingVoice (1.9): second voices go first, then the
        highest channel number (lower MIDI channels win ties)."""
        result = 0
        for i, voice in enumerate(self.alloced):
            if voice["instr_voice"] != 0 or voice["channel"] \
                    >= self.alloced[result]["channel"]:
                result = i
        self._release(result)

    def _key_off(self, channel: int, key: int) -> None:
        i = 0
        while i < len(self.alloced):
            voice = self.alloced[i]
            if voice["channel"] == channel and voice["key"] == key:
                self._release(i)
            else:
                i += 1

    def _all_notes_off(self, channel: int) -> None:
        i = 0
        while i < len(self.alloced):
            if self.alloced[i]["channel"] == channel:
                self._release(i)
            else:
                i += 1

    def _key_on(self, channel: int, note: int, key: int,
                volume: int) -> None:
        if channel == PERCUSSION_CH:
            if key < 35 or key > 81:
                return
            instrument = self.perc[key - 35]
            note = 60
        else:
            instrument = self.channels[channel]["instrument"]
        if not self.free:
            self._replace()
        self._voice_on(channel, instrument, 0, note, key, volume)
        if instrument.double:
            # NOTE: doom 1.9 replaces once, then the second voice may
            # find no room (Chocolate would fault; we drop it nicely).
            self._voice_on(channel, instrument, 1, note, key, volume)

    def _voice_on(self, channel: int, instrument, instr_voice: int,
                  note: int, key: int, volume: int) -> None:
        voice = self._get_free()
        if voice is None:
            return
        voice["channel"] = channel
        voice["key"] = key
        voice["note"] = (instrument.fixed_note if instrument.fixed
                         else note)
        self._set_instrument(voice, instrument, instr_voice)
        self._set_note_volume(voice, volume)
        voice["freq"] = 0
        self._update_freq(voice)

    def _write(self, reg: int, val: int) -> None:
        self.backend.write_reg(reg & 0x1FF, val & 0xFF)

    def _load_operator(self, op: int, data, max_level: bool) -> int:
        level = data.scale | (0x3F if max_level else data.level)
        self._write(REG_LEVEL + op, level)
        self._write(REG_TREMOLO + op, data.tremolo)
        self._write(REG_ATTACK + op, data.attack)
        self._write(REG_SUSTAIN + op, data.sustain)
        self._write(REG_WAVEFORM + op, data.waveform)
        return level

    def _set_instrument(self, voice, instrument, instr_voice: int) -> None:
        if voice["instr"] is instrument \
                and voice["instr_voice"] == instr_voice:
            return
        voice["instr"] = instrument
        voice["instr_voice"] = instr_voice
        data = instrument.voices[instr_voice]
        modulating = (data.feedback & 0x01) == 0
        # NOTE: carrier loads first at minimum volume, like DMX.
        voice["car_volume"] = self._load_operator(voice["op2"], data.carrier,
                                                  True)
        voice["mod_volume"] = self._load_operator(voice["op1"],
                                                  data.modulator,
                                                  not modulating)
        self._write(REG_FEEDBACK + voice["index"],
                    data.feedback | 0x30)
        voice["priority"] = (0x0F - (data.carrier.attack >> 4)
                             + 0x0F - (data.carrier.sustain & 0x0F))

    def _set_note_volume(self, voice, volume: int) -> None:
        voice["note_volume"] = volume
        data = voice["instr"].voices[voice["instr_voice"]]
        ch_vol = self.channels[voice["channel"]]["volume"]
        midi_volume = 2 * (VOLUME_MAP[ch_vol] + 1)
        full = (VOLUME_MAP[volume] * midi_volume) >> 9
        car = 0x3F - full
        if car != voice["car_volume"] & 0x3F:
            voice["car_volume"] = car | voice["car_volume"] & 0xC0
            self._write(REG_LEVEL + voice["op2"], voice["car_volume"])
            if data.feedback & 0x01 and data.modulator.level != 0x3F:
                mod = data.modulator.level
                if mod < car:
                    mod = car
                mod |= voice["mod_volume"] & 0xC0
                if mod != voice["mod_volume"]:
                    voice["mod_volume"] = mod
                    self._write(REG_LEVEL + voice["op1"], mod)

    def _freq_for(self, voice) -> int:
        data = voice["instr"].voices[voice["instr_voice"]]
        note = voice["note"]
        if not voice["instr"].fixed:
            note += _s16(data.base_note_offset)
        while note < 0:
            note += 12
        while note > 95:
            note -= 12
        index = 64 + 32 * note + self.channels[voice["channel"]]["bend"]
        if voice["instr_voice"] != 0:
            index += voice["instr"].fine_tuning // 2 - 64
        if index < 0:
            index = 0
        if index < 284:
            return FREQUENCY_CURVE[index]
        sub = (index - 284) % (12 * 32)
        octave = (index - 284) // (12 * 32)
        if octave >= 7:
            octave = 7
        return FREQUENCY_CURVE[sub + 284] | (octave << 10)

    def _update_freq(self, voice) -> None:
        freq = self._freq_for(voice)
        if voice["freq"] != freq:
            self._write(REG_FREQ1 + voice["index"], freq & 0xFF)
            self._write(REG_FREQ2 + voice["index"], (freq >> 8) | 0x20)
            voice["freq"] = freq

    def _channel_volume(self, channel: int, volume: int,
                        clip_start: bool = False) -> None:
        ch = self.channels[channel]
        ch["volume_base"] = volume
        if volume > self.music_volume:
            volume = self.music_volume
        if clip_start and volume > self.start_volume:
            volume = self.start_volume
        ch["volume"] = volume
        for voice in self.voices:
            if voice["channel"] == channel:
                self._set_note_volume(voice, voice["note_volume"])


def _s16(value: int) -> int:
    value &= 0xFFFF
    return value - 0x10000 if value & 0x8000 else value


class MockBackend:
    """Recording backend for tests (no chip, no audio)."""

    def __init__(self) -> None:
        self.writes: list = []

    def write_reg(self, reg: int, val: int) -> None:
        self.writes.append((reg & 0x1FF, val & 0xFF))

    def render(self, n: int):
        import numpy as np
        return np.zeros(n, dtype=np.int16)


class NullBackend:
    """Silent stand-in when PyOPL is missing (mixer policy: no crash)."""

    def write_reg(self, reg: int, val: int) -> None:
        pass

    def render(self, n: int):
        return None


class PyOplBackend:
    """DOSBox OPL2 via pip PyOPL (lazy import, mono int16 at 11025)."""

    def __init__(self, rate: int = SAMPLE_RATE) -> None:
        import pyopl
        self.chip = pyopl.opl(rate, 2, 1)

    def write_reg(self, reg: int, val: int) -> None:
        self.chip.writeReg(reg & 0x1FF, val & 0xFF)

    def render(self, n: int):
        import numpy as np
        # NOTE: PyOPL fills 2..512 samples; lone tails overshoot by
        # one sample (0.09 ms, trimmed below, scheduler stays exact).
        sizes: list = []
        left = max(int(n), 2)
        while left > 0:
            step = min(left, 512)
            if step < 2:
                step = 2
            sizes.append(step)
            left -= step
        out = np.zeros(sum(sizes), dtype=np.int16)
        off = 0
        for step in sizes:
            buf = bytearray(step * 2)
            self.chip.getSamples(buf)
            out[off:off + step] = np.frombuffer(buf, dtype="<i2")
            off += step
        return out[:int(n)]


def create_backend():
    """PyOPL when installed, silence otherwise (single notice)."""
    try:
        backend = PyOplBackend()
    except ImportError:
        if verbose:
            print("music: PyOPL missing (pip install PyOPL), silent")
        return NullBackend()
    return backend


class MusicPlayer:
    """Song thread: scheduler plus backend render fixed PCM chunks.

    The worker owns the Scheduler (no shared mutable state); control
    travels by commands, PCM chunks travel back. pump() is main-thread
    only and never blocks.
    """

    def __init__(self, backend=None, chunk_sec: float = CHUNK_SEC,
                 rate: int = SAMPLE_RATE) -> None:
        self.backend = backend if backend is not None else create_backend()
        self.chunk_n = int(chunk_sec * rate)
        self.rate = rate
        self._cmd: queue.Queue = queue.Queue()
        self._out: queue.Queue = queue.Queue(maxsize=16)  # NOTE: ~4 s
        # of ride-through for transient CPU spikes (background tasks).
        self._muted = False
        self._live = True
        self._dc_x = 0.0  # NOTE: one-pole DC blocker state (below)
        self._dc_y = 0.0
        self.pumped = 0  # NOTE: chunks handed out (starvation readout)
        self.starved = 0  # NOTE: dry pumps (worker behind or dead)
        self.error = None  # NOTE: worker crash, surfaced, never silent
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def play_song(self, mus_bytes: bytes, main, perc,
                  volume: int = 100) -> None:
        """Queue a song (drains stale chunks first)."""
        self._cmd.put(("play", mus_bytes, main, perc, volume))

    def stop(self) -> None:
        self._cmd.put(("stop",))

    def set_volume(self, volume: int) -> None:
        self._cmd.put(("volume", max(0, min(127, volume))))

    def set_muted(self, muted: bool) -> None:
        self._muted = bool(muted)

    def pump(self):
        """One uint8 mono chunk, or None when the queue is dry."""
        try:
            chunk = self._out.get_nowait()
        except queue.Empty:
            self.starved += 1
            return None
        self.pumped += 1
        return chunk

    def close(self) -> None:
        self._live = False
        self._cmd.put(("quit",))
        self._thread.join(timeout=2.0)

    # -- worker --

    def _drain(self) -> None:
        try:
            while True:
                self._out.get_nowait()
        except queue.Empty:
            pass

    def _run(self) -> None:
        from pydoom.mus import parse_mus
        try:
            self._serve(parse_mus)
        except Exception as exc:  # NOTE: a dead song thread used to
            self.error = exc  # look exactly like "music never starts"
            if verbose:
                print(f"music: worker died: {exc!r}")

    def _serve(self, parse_mus) -> None:
        sched = None
        while self._live:
            try:
                cmd = self._cmd.get(timeout=0.05)
            except queue.Empty:
                cmd = None
            if cmd is not None:
                if cmd[0] == "quit":
                    return
                if cmd[0] == "stop":
                    if sched is not None:
                        sched.stop()
                    sched = None
                    self._drain()
                elif cmd[0] == "volume":
                    if sched is not None:
                        sched.set_music_volume(cmd[1])
                elif cmd[0] == "play":
                    _, mus_bytes, main, perc, volume = cmd
                    try:
                        events = parse_mus(mus_bytes)["events"]
                    except ValueError:
                        sched = None
                        continue
                    sched = Scheduler(events, main, perc, self.backend,
                                      volume)
                    self._drain()
            if sched is None or isinstance(self.backend, NullBackend):
                continue
            pcm = sched.render_chunk(self.backend, self.rate,
                                     self.chunk_n)
            if len(pcm) == 0:
                continue
            import numpy as np
            # NOTE: the chip sums small DC biases (half-sine waves);
            # real cards AC-coupled it away, so do we (one-pole).
            x = pcm.astype(np.float64)
            y = np.empty_like(x)
            dc_x, dc_y = self._dc_x, self._dc_y
            for i, sample in enumerate(x):
                dc_y = sample - dc_x + 0.995 * dc_y
                dc_x = sample
                y[i] = dc_y
            self._dc_x, self._dc_y = dc_x, dc_y
            chunk = ((y.astype(np.int32) >> 8) + 128)
            chunk = chunk.clip(0, 255).astype(np.uint8)
            if self._muted:
                chunk[:] = 128  # NOTE: song runs on, silently
            self._out.put(chunk.tobytes())
