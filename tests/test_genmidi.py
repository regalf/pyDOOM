"""Tests for genmidi.py: the IWAD instrument table splits sanely."""

import os

import pytest

from pydoom.genmidi import MAIN_INSTRS, PERC_INSTRS, parse_genmidi
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


@requires_wad
def test_table_splits_128_plus_47():
    wad = WadFile(WAD_PATH)
    main, perc = parse_genmidi(wad.read_lump("GENMIDI"))
    assert len(main) == MAIN_INSTRS
    assert len(perc) == PERC_INSTRS
    for inst in main + perc:
        assert len(inst.voices) == 2
        assert inst.name and inst.name.isprintable()
        for voice in inst.voices:
            for op in (voice.modulator, voice.carrier):
                assert 0 <= op.level <= 0xFF
                assert 0 <= op.waveform <= 0x07
            assert -0x80 <= voice.base_note_offset <= 0x7F
    names = [i.name for i in main]
    assert len(set(names)) > 100  # NOTE: distinct DMX names, not padding


def test_bad_lump_rejected():
    with pytest.raises(ValueError):
        parse_genmidi(b"junk")
