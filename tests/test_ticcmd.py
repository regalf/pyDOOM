"""Tests for ticcmd.py: vanilla G_BuildTiccmd parity, latches, packing."""

import math

from pydoom import ticcmd
from pydoom.ticcmd import (
    ANGLETURN,
    BT_ATTACK,
    BT_CHANGE,
    BT_USE,
    BT_WEAPONSHIFT,
    FORWARDMOVE,
    MAXPLMOVE,
    SIDEMOVE,
    SLOWTURNTICS,
    RawInput,
    Ticcmd,
    TiccmdBuilder,
)


def test_vanilla_tables():
    assert FORWARDMOVE == (25, 50)
    assert SIDEMOVE == (24, 40)
    assert ANGLETURN == (640, 1280, 320)
    assert SLOWTURNTICS == 6
    assert MAXPLMOVE == 50
    assert ticcmd.BT_WEAPONMASK == 8 + 16 + 32


def test_neutral_build_is_empty():
    cmd = TiccmdBuilder().build(RawInput())
    assert (cmd.forwardmove, cmd.sidemove, cmd.angleturn, cmd.buttons) == (
        0, 0, 0, 0)


def test_walk_run_forward_and_cancel():
    assert TiccmdBuilder().build(RawInput(up=True)).forwardmove == 25
    raw = RawInput(up=True, speed=True)
    assert TiccmdBuilder().build(raw).forwardmove == 50
    raw = RawInput(up=True, down=True)
    assert TiccmdBuilder().build(raw).forwardmove == 0
    assert TiccmdBuilder().build(RawInput(down=True)).forwardmove == -25


def test_strafe_units():
    assert TiccmdBuilder().build(RawInput(straferight=True)).sidemove == 24
    raw = RawInput(strafeleft=True, speed=True)
    assert TiccmdBuilder().build(raw).sidemove == -40


def test_key_turn_slow_ramp_then_full():
    builder = TiccmdBuilder()
    for _ in range(SLOWTURNTICS - 1):
        cmd = builder.build(RawInput(turnleft=True))
        assert cmd.angleturn == 320
    cmd = builder.build(RawInput(turnleft=True))
    assert cmd.angleturn == 640
    cmd = builder.build(RawInput(turnright=True, speed=True))
    assert cmd.angleturn == -1280


def test_turnheld_resets_on_release():
    builder = TiccmdBuilder()
    for _ in range(SLOWTURNTICS + 2):
        builder.build(RawInput(turnleft=True))
    builder.build(RawInput())
    cmd = builder.build(RawInput(turnleft=True))
    assert cmd.angleturn == 320


def test_mouse_turn_consumed_once():
    builder = TiccmdBuilder()
    builder.add_mouse(10, 0)
    cmd = builder.build(RawInput())
    assert cmd.angleturn == -80  # vanilla mousex*0x8
    cmd = builder.build(RawInput())
    assert cmd.angleturn == 0


def test_mouse_forward_and_clamp():
    builder = TiccmdBuilder()
    builder.add_mouse(0, 7)
    cmd = builder.build(RawInput(up=True, speed=True))
    assert cmd.forwardmove == MAXPLMOVE  # 50 + 7 clamps to 50
    builder = TiccmdBuilder()
    builder.add_mouse(0, -100)
    cmd = builder.build(RawInput())
    assert cmd.forwardmove == -MAXPLMOVE


def test_angleturn_wraps_like_c_short():
    builder = TiccmdBuilder()
    builder.add_mouse(10000, 0)
    cmd = builder.build(RawInput())
    assert cmd.angleturn == ticcmd.wrap_angleturn(-80000)
    assert -32768 <= cmd.angleturn <= 32767


def test_attack_use_weapon_bits():
    builder = TiccmdBuilder()
    builder.note_use()
    cmd = builder.build(RawInput(attack=True))
    assert cmd.buttons & BT_ATTACK
    assert cmd.buttons & BT_USE
    # NOTE: the use latch fires exactly one tic.
    cmd = builder.build(RawInput())
    assert not cmd.buttons & BT_USE


def test_weapon_lowest_digit_wins_and_latches():
    builder = TiccmdBuilder()
    builder.note_weapon_down(2)
    builder.note_weapon_down(0)
    builder.note_weapon_up(2)
    builder.note_weapon_up(0)  # tap shorter than a tic still lands
    cmd = builder.build(RawInput())
    assert cmd.buttons & BT_CHANGE
    assert (cmd.buttons & ticcmd.BT_WEAPONMASK) >> BT_WEAPONSHIFT == 0
    cmd = builder.build(RawInput())
    assert not cmd.buttons & BT_CHANGE  # latch cleared...
    builder.note_weapon_down(4)
    cmd = builder.build(RawInput())  # ...but held digits re-send
    assert (cmd.buttons & ticcmd.BT_WEAPONMASK) >> BT_WEAPONSHIFT == 4


def test_pack_unpack_roundtrip():
    cmd = Ticcmd(50, -40, 12345,
                 BT_ATTACK | BT_CHANGE | (3 << BT_WEAPONSHIFT))
    data = cmd.pack()
    assert len(data) == 4
    back = Ticcmd.unpack(data)
    assert (back.forwardmove, back.sidemove, back.buttons) == (
        50, -40, cmd.buttons)
    # NOTE: demo angle rides at 256-unit steps, like vanilla.
    assert back.angleturn == ((12345 + 128) >> 8) << 8


def test_pack_matches_vanilla_demo_bytes():
    cmd = Ticcmd(-25, 24, -80, BT_USE)
    data = cmd.pack()
    assert data[0] == 256 - 25
    assert data[1] == 24
    assert data[2] == ((-80 + 128) >> 8) & 0xFF
    assert data[3] == BT_USE


def test_angleturn_to_rad_sign_convention():
    # NOTE: positive angleturn turns left (CCW), like cam.turn(+).
    assert ticcmd.angleturn_to_rad(16384) == math.pi / 2
    assert ticcmd.angleturn_to_rad(-16384) == -math.pi / 2


def test_lowres_carry_accumulates():
    # NOTE: vanilla G_BuildTiccmd low-res turning: 256-unit steps with
    # the rounding error carried, so small moves accumulate exactly.
    builder = TiccmdBuilder()
    builder.lowres_turn = True
    builder.mouse_units_per_px = 8.0
    total = 0
    for _ in range(32):
        builder.add_mouse(1, 0)  # 8 units/tic, below one 256 step
        total += builder.build(RawInput()).angleturn
    assert total == -32 * 8  # nothing lost to quantization (left is -)
    # Plain play stays full-res.
    free = TiccmdBuilder()
    free.mouse_units_per_px = 8.0
    free.add_mouse(1, 0)
    assert free.build(RawInput()).angleturn == -8
