"""Vanilla input packets (g_game.c G_BuildTiccmd, d_ticcmd.h, d_event.h).

One Ticcmd per game tic (35 Hz): movement in vanilla forward/side units,
turning as an int16 angleturn, actions as BT_* button bits. The demo
format stores these packets, so the builder is deterministic and free of
pygame: the viewer samples held keys once per tic, accumulates mouse
motion between tics, and latches use/weapon key edges (a tap shorter
than one frame must still reach a tic).

What vanilla has that this layer skips (netgame-only or bound later):
consistancy/chatchar fields, joystick input, double-click use, the
BT_SPECIAL pause/special buttons, and mousebforward. Mouse forward
(mousey into forwardmove) *is* kept, exactly like vanilla.
"""
from __future__ import annotations

from dataclasses import dataclass

# NOTE: d_event.h button bits; BT_WEAPONMASK picks bits 3-5 (weapons 0-7).
BT_ATTACK = 1
BT_USE = 2
BT_CHANGE = 4
BT_WEAPONMASK = 8 + 16 + 32
BT_WEAPONSHIFT = 3
BT_SPECIAL = 128

# NOTE: g_game.c demo stream: header/footer marker, then 4 bytes per tic
# (G_ReadDemoTiccmd/G_WriteDemoTiccmd). Angleturn rides at 256-unit
# resolution: the writer stores the high byte of (angleturn+128) and the
# game itself plays back the quantized value.
DEMOMARKER = 0x80

# NOTE: g_game.c movement tables: index 0 walks, 1 runs (Shift held).
FORWARDMOVE = (0x19, 0x32)  # (25, 50)
SIDEMOVE = (0x18, 0x28)  # (24, 40)
# NOTE: key turning, index 0 normal / 1 fast / 2 slow-start ramp.
ANGLETURN = (640, 1280, 320)
SLOWTURNTICS = 6
MAXPLMOVE = FORWARDMOVE[1]  # 50, clamps forwardmove and sidemove
# NOTE: vanilla mouse: angleturn -= mousex*0x8, side += mousex*2 strafing.
MOUSE_TURN = 0x8
MOUSE_STRAFE = 2
# NOTE: full circle in angleturn units (BAM/65536 short).
ANGLEUNITS = 65536


def wrap_angleturn(value: int) -> int:
    """Wrap to int16, like the C short overflowing in ticcmd_t."""
    return ((int(value) + 32768) % ANGLEUNITS) - 32768


def angleturn_to_rad(angleturn: int) -> float:
    """Angleturn units to radians (viewer applies these to the camera)."""
    return wrap_angleturn(angleturn) * 2.0 * 3.141592653589793 / ANGLEUNITS


@dataclass
class RawInput:
    """Held-button snapshot for one tic (viewer fills from key state)."""

    up: bool = False
    down: bool = False
    strafeleft: bool = False
    straferight: bool = False
    turnleft: bool = False
    turnright: bool = False
    speed: bool = False  # run key held
    attack: bool = False  # fire key or mouse button held
    strafe_mod: bool = False  # strafe modifier: turns become strafes


@dataclass
class Ticcmd:
    """One vanilla input packet (demo stream stores exactly this)."""

    forwardmove: int = 0
    sidemove: int = 0
    angleturn: int = 0  # int16, positive turns left (CCW)
    buttons: int = 0

    def pack(self) -> bytes:
        """4 demo bytes (G_WriteDemoTiccmd, angle at 256-unit steps)."""
        angle = (wrap_angleturn(self.angleturn) + 128) >> 8
        return bytes((
            self.forwardmove & 0xFF,
            self.sidemove & 0xFF,
            angle & 0xFF,
            self.buttons & 0xFF,
        ))

    @staticmethod
    def unpack(data: bytes) -> "Ticcmd":
        """Inverse of pack (G_ReadDemoTiccmd); needs 4 bytes."""
        fwd, side, anglehi, buttons = bytes(data[:4])
        fwd -= 256 if fwd >= 128 else 0
        side -= 256 if side >= 128 else 0
        angle = anglehi << 8
        angle -= ANGLEUNITS if angle >= 32768 else 0
        return Ticcmd(fwd, side, angle, buttons)


class TiccmdBuilder:
    """Accumulates between-tic input, builds one Ticcmd per tic.

    Mouse pixels accumulate via add_mouse (consumed and cleared per
    build, like vanilla's mousex/mousey). Use taps and weapon digits
    latch on key down so sub-frame taps still reach the next tic; the
    latched weapon set clears per build while physically-held digits
    persist until key up (vanilla re-sends BT_CHANGE while held).
    """

    def __init__(self) -> None:
        self.mousex = 0
        self.mousey = 0
        # NOTE: angleturn units per mouse px; vanilla MOUSE_TURN (8).
        # The viewer drives this from the sensitivity slider.
        self.mouse_units_per_px = float(MOUSE_TURN)
        self.turnheld = 0  # tics of continuous key turning (slow ramp)
        self.use_pending = False
        self.weap_held: set[int] = set()
        self.weap_latched: set[int] = set()
        # NOTE: low-res turning (vanilla G_BuildTiccmd, recording only):
        # angleturn quantizes to 256-unit steps and the rounding error
        # carries to the next tic, so small moves accumulate. Plain
        # play stays full-res; the viewer enables this while recording.
        self.lowres_turn = False
        self.carry = 0

    def add_mouse(self, dx: int, dy: int) -> None:
        """Accumulate one motion event (vanilla mousex/mousey units).

        Sign convention is vanilla's: positive dy means pushed forward
        (mouse up). Callers using y-down APIs (pygame rel) negate first.
        """
        self.mousex += int(dx)
        self.mousey += int(dy)

    def note_use(self) -> None:
        """Use-key edge (viewer calls on key down, level-gated)."""
        self.use_pending = True

    def note_weapon_down(self, index: int) -> None:
        """Weapon digit 0-6 ('1'-'7') pressed."""
        self.weap_held.add(index)
        self.weap_latched.add(index)

    def note_weapon_up(self, index: int) -> None:
        self.weap_held.discard(index)

    def build(self, raw: RawInput) -> Ticcmd:
        speed = 1 if raw.speed else 0
        if raw.turnleft or raw.turnright:
            self.turnheld += 1
        else:
            self.turnheld = 0
        # NOTE: two-stage accelerative key turning (vanilla tspeed).
        tspeed = 2 if self.turnheld < SLOWTURNTICS else speed

        forward = 0
        side = 0
        angleturn = 0
        buttons = 0

        if raw.strafe_mod:
            if raw.turnright:
                side += SIDEMOVE[speed]
            if raw.turnleft:
                side -= SIDEMOVE[speed]
        else:
            if raw.turnright:
                angleturn -= ANGLETURN[tspeed]
            if raw.turnleft:
                angleturn += ANGLETURN[tspeed]

        if raw.up:
            forward += FORWARDMOVE[speed]
        if raw.down:
            forward -= FORWARDMOVE[speed]
        if raw.straferight:
            side += SIDEMOVE[speed]
        if raw.strafeleft:
            side -= SIDEMOVE[speed]

        if raw.attack:
            buttons |= BT_ATTACK
        if self.use_pending:
            buttons |= BT_USE
            self.use_pending = False
        pending = self.weap_held | self.weap_latched
        if pending:
            # NOTE: vanilla picks the lowest held digit ('1' wins).
            buttons |= BT_CHANGE | (min(pending) << BT_WEAPONSHIFT)
        self.weap_latched.clear()

        if raw.strafe_mod:
            side += int(round(self.mousex * MOUSE_STRAFE))
        else:
            angleturn -= int(round(self.mousex * self.mouse_units_per_px))
        forward += self.mousey
        self.mousex = 0
        self.mousey = 0

        if forward > MAXPLMOVE:
            forward = MAXPLMOVE
        elif forward < -MAXPLMOVE:
            forward = -MAXPLMOVE
        if side > MAXPLMOVE:
            side = MAXPLMOVE
        elif side < -MAXPLMOVE:
            side = -MAXPLMOVE

        if self.lowres_turn:
            desired = wrap_angleturn(angleturn + self.carry)
            angleturn = ((desired + 128) // 256) * 256
            self.carry = desired - angleturn

        return Ticcmd(forward, side, wrap_angleturn(angleturn), buttons)
