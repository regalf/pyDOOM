"""Color palette (PLAYPAL) loading.

PLAYPAL is a lump of 14 palettes x 256 colors x 3 bytes (RGB).
Palette 0 is the base one; the others are damage/bonus/item pickup
flashes and are used by the renderer and status bar later.
"""

from __future__ import annotations

__all__ = ["NUM_PALETTES", "load_playpal"]


NUM_PALETTES = 14


def load_playpal(data: bytes) -> list[tuple[int, int, int]]:
    """Parse the first (base) palette of a PLAYPAL lump into 256 RGB tuples."""
    if len(data) < 768:
        raise ValueError(f"PLAYPAL lump too short: {len(data)} bytes")
    return [
        (data[i], data[i + 1], data[i + 2]) for i in range(0, 768, 3)
    ]
