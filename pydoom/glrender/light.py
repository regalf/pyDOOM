"""GL lighting LUTs (milestone H, phase 1, LUT slice).

Pure-CPU byte builders (no GL imports): the 32 COLORMAP rows and the
base PLAYPAL palette upload as LUT textures, so the fragment shader
evaluates colormap(lightlevel, index) then palette(lit) exactly like
the software drawer (cmap[texel] in fastdraw.py, PLAYPAL at present).

The software clamps light rows to NUMCOLORMAPS - 1 (renderer.py), so
only the first 32 maps upload even though the lump carries 34.
Damage/bonus flash palettes (1-13) stay on the overlay path
(Phase 4); only palette 0 rides the world shader.
"""

from __future__ import annotations

from pydoom.fixed import FRACUNIT, fixed_div
from pydoom.renderer import (
    DISTMAP,
    LIGHTLEVELS,
    LIGHTSCALESHIFT,
    LIGHTZSHIFT,
    MAXLIGHTSCALE,
    MAXLIGHTZ,
    NUMCOLORMAPS,
    SCREENWIDTH,
)

# NOTE: NUMCOLORMAPS stays imported (shader-side sizing via __all__).
SCALELIGHT_W, SCALELIGHT_H = MAXLIGHTSCALE, LIGHTLEVELS
ZLIGHT_W, ZLIGHT_H = MAXLIGHTZ, LIGHTLEVELS
COLORMAP_BYTES = NUMCOLORMAPS * 256
PALETTE_BYTES = 256 * 3

__all__ = [
    "COLORMAP_BYTES",
    "NUMCOLORMAPS",
    "PALETTE_BYTES",
    "SCALELIGHT_H",
    "SCALELIGHT_W",
    "ZLIGHT_H",
    "ZLIGHT_W",
    "colormap_lut",
    "palette_lut",
    "scalelight_lut",
    "zlight_lut",
]


def colormap_lut(data: bytes) -> bytes:
    """First 32 COLORMAP rows (256 bytes each) for the LUT texture."""
    if len(data) < COLORMAP_BYTES:
        raise ValueError(f"COLORMAP lump too short: {len(data)} bytes")
    return bytes(data[:COLORMAP_BYTES])


def palette_lut(data: bytes, index: int = 0) -> bytes:
    """One PLAYPAL palette (768 bytes RGB) for the LUT texture."""
    base = index * PALETTE_BYTES
    if len(data) < base + PALETTE_BYTES:
        raise ValueError(f"PLAYPAL lump too short for index {index}")
    return bytes(data[base:base + PALETTE_BYTES])


def _startmap(i: int) -> int:
    """R_InitLightTables row base (mirrors renderer verbatim)."""
    return ((LIGHTLEVELS - 1 - i) * 2) * NUMCOLORMAPS // LIGHTLEVELS


def scalelight_lut() -> bytes:
    """48x16 scalelight table (row = lightnum, col = scale index):
    byte-identical to Renderer._init_scalelight (pinned by test)."""
    out = bytearray()
    # NOTE: renderer divides by self.viewwidth, which is always
    # SCREENWIDTH (fixed 320x200 view), so j * 320 // 320 == j here.
    for i in range(LIGHTLEVELS):
        startmap = _startmap(i)
        for j in range(MAXLIGHTSCALE):
            level = (startmap
                     - j * SCREENWIDTH // SCREENWIDTH // DISTMAP)
            out.append(min(max(level, 0), NUMCOLORMAPS - 1))
    return bytes(out)


def zlight_lut() -> bytes:
    """128x16 zlight table (row = lightnum, col = distance index):
    byte-identical to Renderer._init_zlight (pinned by test)."""
    out = bytearray()
    for i in range(LIGHTLEVELS):
        startmap = _startmap(i)
        for j in range(MAXLIGHTZ):
            scale = fixed_div((SCREENWIDTH // 2 * FRACUNIT),
                              (j + 1) << LIGHTZSHIFT)
            scale >>= LIGHTSCALESHIFT
            level = startmap - scale // DISTMAP
            out.append(min(max(level, 0), NUMCOLORMAPS - 1))
    return bytes(out)
