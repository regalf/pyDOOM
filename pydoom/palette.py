"""Color palette (PLAYPAL) loading.

PLAYPAL is a lump of 14 palettes x 256 colors x 3 bytes (RGB).
Palette 0 is the base one; the others are damage/bonus/item pickup
flashes and are used by the renderer and status bar later.
"""

from __future__ import annotations

__all__ = ["NUM_PALETTES", "load_playpal", "load_playpal_index"]


NUM_PALETTES = 14


def load_playpal(data: bytes) -> list[tuple[int, int, int]]:
    """Parse the first (base) palette of a PLAYPAL lump into 256 RGB tuples."""
    return load_playpal_index(data, 0)


def load_playpal_index(data: bytes, index: int) -> list[tuple[int, int, int]]:
    """Parse one palette (0 base, 1-8 red, 9-12 gold, 13 suit)."""
    base = index * 768
    if len(data) < base + 768:
        raise ValueError(f"PLAYPAL lump too short for index {index}")
    return [
        (data[base + i], data[base + i + 1], data[base + i + 2])
        for i in range(0, 768, 3)
    ]
