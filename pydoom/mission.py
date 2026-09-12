"""Game mission detection (d_main.c CheckIWAD lite).

Counts the ExMy map lumps: 9+ E4 maps is retail (Ultimate), 18+
E2+E3 maps is registered, 9 E1 maps is shareware. The engine plays
shareware/registered today; retail wires up when its IWAD lands.
"""

from __future__ import annotations

SHAREWARE = "shareware"
REGISTERED = "registered"
RETAIL = "retail"

EPISODES = {SHAREWARE: 1, REGISTERED: 3, RETAIL: 4}


def detect(wad) -> str:
    """Mission of the loaded IWAD, from its map lump census."""
    try:
        names = {lump.name for lump in wad.lumps}
    except AttributeError:
        names = set(wad)
    e1 = sum(1 for m in range(1, 10) if f"E1M{m}" in names)
    e23 = sum(1 for e in (2, 3) for m in range(1, 10)
              if f"E{e}M{m}" in names)
    e4 = sum(1 for m in range(1, 10) if f"E4M{m}" in names)
    if e4 >= 9:
        return RETAIL
    if e23 >= 18:
        return REGISTERED
    if e1 >= 9:
        return SHAREWARE
    return SHAREWARE  # partial PWADs boot as the smallest mission


def episode_count(mission: str) -> int:
    """Playable episodes for the mission (menu gating, demo clamp)."""
    return EPISODES.get(mission, 1)


def episode_start(episode: int, maps) -> str:
    """First map of the chosen episode (G_DeferedInitNew), or E1M1
    when the lump is missing (shareware picking beyond episode 1)."""
    dest = f"E{episode + 1}M1"
    return dest if dest in maps else "E1M1"
