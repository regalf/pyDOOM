"""Status bar (st_stuff.c lite): static STBAR, numbers, face, arms.

Layout coordinates are vanilla (st_stuff.c ST_*X/ST_*Y): big current
ammo, health/armor with percent, arms 2-7, face, key slots and the
mini current/max ammo pairs. Animated faces, palette flashes and
frags arrive with the menu phase.
"""

from __future__ import annotations

from pydoom.player import (
    AM_CELL,
    AM_CLIP,
    AM_MISL,
    AM_SHELL,
    KEY_BLUE,
    KEY_BSKULL,
    KEY_RED,
    KEY_RSKULL,
    KEY_YELLOW,
    KEY_YSKULL,
    WEAPON_AMMO,
    WP_CHAINSAW,
    WP_FIST,
)

ST_Y = 168
FACE_X, FACE_Y = 143, 168
AMMO_X, AMMO_Y = 44, 171
HEALTH_X, HEALTH_Y = 90, 171
ARMSBG_X, ARMSBG_Y = 104, 168
ARMS_X, ARMS_Y, ARMS_DX, ARMS_DY = 110, 172, 12, 10
ARMOR_X, ARMOR_Y = 221, 171
KEY_X = 239
KEY_Y = (171, 181, 191)
MINI_X, MAX_X = 288, 314
# NOTE: right-column rows run top-down BULL/SHEL/ROKT/CELL (st_stuff):
# rockets sit above cells, like the STBAR labels.
MINI_Y = {AM_CLIP: 173, AM_SHELL: 179, AM_MISL: 185, AM_CELL: 191}


import numpy as np

_PATCH_CACHE: dict = {}


def _cached(renderer, name: str):
    """Decoded patch + stacked pixels/mask (one decode per lump)."""
    hit = _PATCH_CACHE.get(name)
    if hit is None:
        from pydoom.textures import decode_patch
        patch = decode_patch(renderer.wad.read_lump(name))
        cols = [patch.column_pixels(sx) for sx in range(patch.width)]
        mat = np.stack([np.frombuffer(c[0], dtype=np.uint8) for c in cols],
                       axis=1)
        msk = np.stack([np.frombuffer(c[1], dtype=np.uint8) for c in cols],
                       axis=1)
        hit = (patch, mat, msk)
        _PATCH_CACHE[name] = hit
    return hit


def _blit(renderer, fb, name: str, x: int, y: int) -> int:
    """Blit a patch lump 1:1, clipped (V_DrawPatch origin honored)."""
    try:
        patch, mat, msk = _cached(renderer, name)
    except Exception:
        return 0
    h, w = patch.height, patch.width
    # NOTE: vanilla draws at (x - leftoffset, y - topoffset); the face
    # (STFST00 -5,-2) and key slots (-1) need it to sit like vanilla.
    ox, oy = x - patch.leftoffset, y - patch.topoffset
    x0, y0 = max(ox, 0), max(oy, 168)
    x1, y1 = min(ox + w, 320), min(oy + h, 200)
    if x0 >= x1 or y0 >= y1:
        return w
    ox0, oy0 = x0 - ox, y0 - oy
    ox1, oy1 = ox0 + (x1 - x0), oy0 + (y1 - y0)
    region = msk[oy0:oy1, ox0:ox1].astype(bool)
    fb[y0:y1, x0:x1][region] = mat[oy0:oy1, ox0:ox1][region]
    return w


def _patch_width(renderer, name: str) -> int:
    try:
        return _cached(renderer, name)[0].width
    except Exception:
        return 0


def _draw_number(renderer, fb, value: int, x_right: int, y: int,
                 prefix: str) -> int:
    """STlib_drawNum: fixed-step digits ending at x_right (the vanilla
    x IS the right edge; glyphs step left by the '0' width)."""
    step = _patch_width(renderer, f"{prefix}0") or 1
    for digit in reversed(str(max(0, value))):
        x_right -= step
        _blit(renderer, fb, f"{prefix}{digit}", x_right, y)
    return x_right


def draw_status_bar(renderer, fb, ps, health: int,
                      face: str = "STFST00") -> None:
    """ST_Ticker lite: background, face, ammo/health/armor/arms/keys."""
    _blit(renderer, fb, "STBAR", 0, ST_Y)
    _blit(renderer, fb, "STARMS", ARMSBG_X, ARMSBG_Y)
    # NOTE: big current-ammo readout (fists count bullets, like vanilla).
    ammo = WEAPON_AMMO[ps.readyweapon]
    if ammo is None or ammo < 0:
        ammo = AM_CLIP
    _draw_number(renderer, fb, ps.ammo[ammo], AMMO_X, AMMO_Y, "STTNUM")
    _draw_number(renderer, fb, max(0, health), HEALTH_X, HEALTH_Y, "STTNUM")
    _blit(renderer, fb, "STTPRCNT", HEALTH_X, HEALTH_Y)
    _draw_number(renderer, fb, ps.armorpoints, ARMOR_X, ARMOR_Y, "STTNUM")
    _blit(renderer, fb, "STTPRCNT", ARMOR_X, ARMOR_Y)
    _blit(renderer, fb, face, FACE_X, FACE_Y)
    for i in range(6):  # NOTE: arms 2-7 light up when owned.
        if ps.weapons & (1 << (i + 1)):
            _blit(renderer, fb, f"STGNUM{i + 2}",
                  ARMS_X + (i % 3) * ARMS_DX, ARMS_Y + (i // 3) * ARMS_DY)
    for slot, (card, skull, num) in enumerate(
            ((KEY_BLUE, KEY_BSKULL, 0), (KEY_YELLOW, KEY_YSKULL, 1),
             (KEY_RED, KEY_RSKULL, 2))):
        if ps.keys & card:
            _blit(renderer, fb, f"STKEYS{num}", KEY_X, KEY_Y[slot])
        elif ps.keys & skull:
            _blit(renderer, fb, f"STKEYS{num + 3}", KEY_X, KEY_Y[slot])
    for ammo, y in MINI_Y.items():
        _draw_number(renderer, fb, ps.ammo[ammo], MINI_X, y, "STYSNUM")
        _draw_number(renderer, fb, ps.maxammo[ammo], MAX_X, y, "STYSNUM")


# -- Doomguy face (ST_updateFaceWidget lite) --

TICRATE = 35
MUCHPAIN = 20


class FaceState:
    """st_faceindex/priority/count statics, per viewer run."""

    def __init__(self) -> None:
        self.priority = 0
        self.facecount = 0
        self.faceindex = 0
        self.oldhealth = -1
        self.lastattackdown = -1
        self.oldweapons = 0
        self.lastcalc = 0
        self.calchealth = -1


def _pain_offset(fs: FaceState, health: int) -> int:
    h = min(health, 100)
    if h != fs.calchealth:
        fs.lastcalc = 8 * (((100 - h) * 5) // 101)
        fs.calchealth = h
    return fs.lastcalc


def face_lump(faceindex: int) -> str:
    """Face table name for a st_faceindex (god/dead past the grid)."""
    if faceindex == 40:
        return "STFGOD0"
    if faceindex == 41:
        return "STFDEAD0"
    pain, sub = faceindex // 8, faceindex % 8
    if sub <= 2:
        return f"STFST{pain}{sub}"
    if sub == 3:
        return f"STFTR{pain}0"
    if sub == 4:
        return f"STFTL{pain}0"
    if sub == 5:
        return f"STFOUCH{pain}"
    if sub == 6:
        return f"STFEVL{pain}"
    return f"STFKILL{pain}"


def update_face(fs: FaceState, ps, mo, attackdown: bool) -> str:
    """ST_updateFaceWidget: dead/grin/pain/rampage/invuln/idle cascade."""
    from pydoom.angles import point_to_angle2
    from pydoom.fixed import ANG45, ANG180
    from pydoom.m_random import m_random
    from pydoom.player import PW_INVULN
    health = mo.health
    # NOTE: vanilla tests !health (dead on overkill too); == 0 would
    # miss corpses and leak pain-offset indexes into the god face.
    if fs.priority < 10 and health <= 0:
        fs.priority, fs.faceindex, fs.facecount = 9, 41, 1
    if fs.priority < 9 and ps.bonuscount:
        grin = False
        for i in range(9):
            if bool(fs.oldweapons & (1 << i)) != bool(ps.weapons & (1 << i)):
                grin = True
                fs.oldweapons = ps.weapons
        if grin:
            fs.priority, fs.facecount = 8, 2 * TICRATE
            fs.faceindex = _pain_offset(fs, health) + 6
    if fs.priority < 8 and ps.damagecount and mo.attacker is not None \
            and mo.attacker is not mo:
        fs.priority = 7
        if health - fs.oldhealth > MUCHPAIN:
            fs.facecount = TICRATE
            fs.faceindex = _pain_offset(fs, health) + 5
        else:
            badguy = point_to_angle2(mo.x, mo.y, mo.attacker.x,
                                     mo.attacker.y)
            if badguy > mo.angle:
                diffang = (badguy - mo.angle) & 0xFFFFFFFF
                side = diffang > ANG180
            else:
                diffang = (mo.angle - badguy) & 0xFFFFFFFF
                side = diffang <= ANG180
            fs.facecount = TICRATE
            fs.faceindex = _pain_offset(fs, health)
            if diffang < ANG45:
                fs.faceindex += 7  # NOTE: head-on rampage glare
            elif side:
                fs.faceindex += 3  # NOTE: turn right
            else:
                fs.faceindex += 4  # NOTE: turn left
    if fs.priority < 7 and ps.damagecount:
        if health - fs.oldhealth > MUCHPAIN:
            fs.priority, fs.facecount = 7, TICRATE
            fs.faceindex = _pain_offset(fs, health) + 5
        else:
            fs.priority, fs.facecount = 6, TICRATE
            fs.faceindex = _pain_offset(fs, health) + 7
    if fs.priority < 6:
        if attackdown:
            if fs.lastattackdown == -1:
                fs.lastattackdown = 2 * TICRATE
            else:
                fs.lastattackdown -= 1
                if not fs.lastattackdown:
                    fs.priority, fs.faceindex, fs.facecount = \
                        5, _pain_offset(fs, health) + 7, 1
                    fs.lastattackdown = 1
        else:
            fs.lastattackdown = -1
    if fs.priority < 5 and ps.powers.get(PW_INVULN):
        fs.priority, fs.faceindex, fs.facecount = 4, 40, 1
    if not fs.facecount:
        fs.faceindex = _pain_offset(fs, health) + m_random() % 3
        fs.facecount = TICRATE // 2
        fs.priority = 0
    fs.facecount -= 1
    fs.oldhealth = health
    return face_lump(fs.faceindex)
