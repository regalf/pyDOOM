"""Episode flow (g_game.c lite): exit routing and level carry-over.

Shareware episode: E1M1..E1M8, plus E1M9 reached from the E1M3 secret
exit (and returning to E1M4). E1M8 has no next map: its exit wins.
"""

from __future__ import annotations

NEXT_MAP = {"E1M1": "E1M2", "E1M2": "E1M3", "E1M3": "E1M4",
            "E1M4": "E1M5", "E1M5": "E1M6", "E1M6": "E1M7",
            "E1M7": "E1M8", "E1M9": "E1M4"}
SECRET_MAP = {"E1M3": "E1M9"}


def next_map(marker: str, secret: bool) -> str | None:
    """Map to load after an exit, or None for episode complete."""
    if secret and marker in SECRET_MAP:
        return SECRET_MAP[marker]
    return NEXT_MAP.get(marker)  # E1M8 falls out: victory


def strip_for_next_level(ps) -> None:
    """G_PlayerFinishLevel: keep guns/ammo/armor/health, drop keys and
    powers (pending settles to ready)."""
    ps.keys = 0
    ps.powers = {}
    ps.pendingweapon = ps.readyweapon
    ps.switchtics = 0
