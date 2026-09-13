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

NUMCOLORMAPS = 32
COLORMAP_BYTES = NUMCOLORMAPS * 256
PALETTE_BYTES = 256 * 3

__all__ = [
    "COLORMAP_BYTES",
    "NUMCOLORMAPS",
    "PALETTE_BYTES",
    "colormap_lut",
    "palette_lut",
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
