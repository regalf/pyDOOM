"""DMX GENMIDI lump (i_oplmusic.c idea): OPL instrument definitions.

Layout, verified against the IWAD (8 + 175*36 + 175*32 = 11908):
"#OPL_II#" magic, 128 melodic + 47 percussion instruments of 36
bytes (flags u16, fine tuning, fixed note, two 16-byte voices), then
175 zero-padded 32-byte names. stdlib only.
"""

from dataclasses import dataclass, field

HEADER = b"#OPL_II#"
MAIN_INSTRS = 128
PERC_INSTRS = 47
NAME_LEN = 32

_OP_FIELDS = ("tremolo", "attack", "sustain", "waveform", "scale",
              "level")


@dataclass
class Operator:
    """One OPL operator: raw register nibbles, vanilla-packed."""

    tremolo: int = 0
    attack: int = 0
    sustain: int = 0
    waveform: int = 0
    scale: int = 0
    level: int = 0


@dataclass
class Voice:
    """Half an instrument: modulator, carrier, feedback, base offset."""

    modulator: Operator = field(default_factory=Operator)
    feedback: int = 0
    carrier: Operator = field(default_factory=Operator)
    base_note_offset: int = 0


@dataclass
class Instrument:
    """genmidi_instr_t: flags, tuning, two voices, DMX name."""

    flags: int = 0
    fine_tuning: int = 0
    fixed_note: int = 0
    voices: list = field(default_factory=list)
    name: str = ""

    @property
    def fixed(self) -> bool:
        """GENMIDI_FLAG_FIXED: ignore the played pitch (percussion)."""
        return bool(self.flags & 0x0001)

    @property
    def double(self) -> bool:
        """GENMIDI_FLAG_2VOICE: needs two OPL voices (OPL3 only)."""
        return bool(self.flags & 0x0004)


def _operator(data: bytes, pos: int) -> Operator:
    vals = data[pos:pos + 6]
    return Operator(**dict(zip(_OP_FIELDS, vals)))


def _voice(data: bytes, pos: int) -> Voice:
    mod = _operator(data, pos)
    feedback = data[pos + 6]
    car = _operator(data, pos + 7)
    offset = int.from_bytes(data[pos + 14:pos + 16], "little",
                            signed=True)
    return Voice(modulator=mod, feedback=feedback, carrier=car,
                 base_note_offset=offset)


def parse_genmidi(data: bytes) -> tuple[list, list]:
    """Split the lump into (128 melodic, 47 percussion) instruments."""
    if len(data) < 8 or data[:8] != HEADER:
        raise ValueError("not a GENMIDI lump")
    pos = 8
    instrs: list = []
    for _ in range(MAIN_INSTRS + PERC_INSTRS):
        if pos + 36 > len(data):
            raise ValueError("GENMIDI overruns lump")
        flags = int.from_bytes(data[pos:pos + 2], "little")
        fine_tuning = data[pos + 2]
        fixed_note = data[pos + 3]
        voices = [_voice(data, pos + 4), _voice(data, pos + 20)]
        instrs.append(Instrument(flags=flags, fine_tuning=fine_tuning,
                                 fixed_note=fixed_note, voices=voices))
        pos += 36
    for inst in instrs:
        if pos + NAME_LEN > len(data):
            raise ValueError("GENMIDI names overrun lump")
        inst.name = data[pos:pos + NAME_LEN].split(b"\x00")[0].decode(
            "ascii", "replace")
        pos += NAME_LEN
    return instrs[:MAIN_INSTRS], instrs[MAIN_INSTRS:]
