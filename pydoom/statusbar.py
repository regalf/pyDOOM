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
MINI_Y = {AM_CLIP: 173, AM_SHELL: 179, AM_CELL: 185, AM_MISL: 191}
# NOTE: right edges recomputed from measured digit widths so the mini
# pairs fit on screen (STYSNUM digits are 4px; 314+24 would clip).
MINI_R, MAX_R = 306, 319


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
    """Right-aligned decimal in patch digits; returns the left edge."""
    names = [f"{prefix}{d}" for d in str(max(0, value))]
    widths = [_patch_width(renderer, name) for name in names]
    x = x_right - sum(widths)
    for name, w in zip(names, widths):
        _blit(renderer, fb, name, x, y)
        x += w
    return x_right - sum(widths)


def draw_status_bar(renderer, fb, ps, health: int) -> None:
    """ST_Ticker lite: background, face, ammo/health/armor/arms/keys."""
    _blit(renderer, fb, "STBAR", 0, ST_Y)
    _blit(renderer, fb, "STARMS", ARMSBG_X, ARMSBG_Y)
    # NOTE: big current-ammo readout (fists count bullets, like vanilla).
    ammo = WEAPON_AMMO[ps.readyweapon]
    if ammo is None or ammo < 0:
        ammo = AM_CLIP
    _draw_number(renderer, fb, ps.ammo[ammo], AMMO_X + 40, AMMO_Y, "STTNUM")
    _draw_number(renderer, fb, max(0, health), HEALTH_X + 40,
                 HEALTH_Y, "STTNUM")
    _blit(renderer, fb, "STTPRCNT", HEALTH_X + 40, HEALTH_Y)
    _draw_number(renderer, fb, ps.armorpoints, ARMOR_X + 40, ARMOR_Y,
                 "STTNUM")
    _blit(renderer, fb, "STTPRCNT", ARMOR_X + 40, ARMOR_Y)
    _blit(renderer, fb, "STFST00", FACE_X, FACE_Y)
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
        _draw_number(renderer, fb, ps.ammo[ammo], MINI_R, y, "STYSNUM")
        _draw_number(renderer, fb, ps.maxammo[ammo], MAX_R, y, "STYSNUM")
