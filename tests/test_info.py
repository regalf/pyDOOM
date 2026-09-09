"""Tests for the generated info.py tables against known game data."""

from pydoom.info import (
    FF_FRAMEMASK,
    FF_FULLBRIGHT,
    MF_FLAGS,
    MF_SHADOW,
    MOBJ_TYPES,
    MT_INDEX,
    MT_NAMES,
    SPRITE_INDEX,
    SPRITE_NAMES,
    STATES,
    STATE_INDEX,
    spawn_visual,
    type_record,
)


def test_table_sizes():
    assert len(SPRITE_NAMES) == 138
    assert len(STATES) == 967
    assert len(MOBJ_TYPES) == 137
    assert len(MT_NAMES) == 137
    assert SPRITE_NAMES[0] == "TROO"
    assert "S_NULL" in STATE_INDEX and STATE_INDEX["S_NULL"] == 0
    assert MT_NAMES[0] == "PLAYER" and MT_INDEX["TROOP"] == 11


def test_spawn_visual_spot_checks():
    sprite, frame, flags = spawn_visual(3001)  # imp
    assert SPRITE_NAMES[sprite] == "TROO"
    assert frame == 0
    sprite, frame, flags = spawn_visual(2001)  # shotgun
    assert SPRITE_NAMES[sprite] == "SHOT"
    sprite, frame, flags = spawn_visual(2035)  # barrel
    assert SPRITE_NAMES[sprite] == "BAR1"
    assert SPRITE_INDEX["BAR1"] == sprite


def test_spectre_has_shadow_flag():
    sprite, frame, flags = spawn_visual(58)  # spectre: demon sprite + fuzz
    assert SPRITE_NAMES[sprite] == "SARG"
    assert flags & MF_SHADOW


def test_unknown_type_returns_none():
    assert spawn_visual(9999) is None
    assert spawn_visual(1) is None  # players have doomednum -1


def test_frame_constants():
    assert FF_FRAMEMASK == 0x7FFF
    assert FF_FULLBRIGHT == 0x8000


def test_type_record_has_body_dims():
    rec = type_record(3001)  # imp: radius 20, height 56
    assert rec is not None
    assert rec["radius"] == 20 * 65536
    assert rec["height"] == 56 * 65536
    assert rec["spawnhealth"] == 60
    assert type_record(9999) is None


def test_state_entries_have_tics_and_next():
    sprite, frame, tics, nxt, action = STATES[0]  # S_NULL
    assert (sprite, frame, tics, nxt, action) == (0, 0, -1, 0, None)
    sprite, frame, tics, nxt, action = STATES[STATE_INDEX["S_POSS_STND"]]
    assert action == "A_Look" and tics == 10
    assert MF_FLAGS["MF_SOLID"] == 2
    assert MF_FLAGS["MF_DROPOFF"] == 0x400
