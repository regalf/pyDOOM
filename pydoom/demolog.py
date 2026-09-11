"""Demo event narrator (milestone F debugging aid): timestamped,
point-by-point log of what the demo player does and what happens to
them (shots, kills, damage, pickups, doors, teleports, secrets).

Zero-cost when disabled (one boolean per call site); pygame-free.
"""
from __future__ import annotations

enabled = False
leveltime = 0  # world.time of the current tic (set by the viewer)
streamtic = -1  # demo stream position, -1 when playing live


def stamp() -> str:
    """Vanilla intermission clock (m:ss) plus stream tic."""
    return f"{leveltime // 2100}:{(leveltime // 35) % 60:02d} t{streamtic}"


def emit(msg: str) -> None:
    """One narrator line (stdout, greppable)."""
    if enabled:
        print(f"[{stamp()}] {msg}", flush=True)
