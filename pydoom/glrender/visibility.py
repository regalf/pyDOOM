"""Sector PVS via portal flood (milestone H, pvs slice).

Software-culling analog for the GL mesh: the GL world draws every
wall quad + plane tri unconditionally and relies on depth, while the
software BSP closes screen columns behind solid walls. Mappers add
margin geometry that is never reachable through a portal (the E1M1
secret-garden outsides); depth alone still rasterizes it when it
peeks over a low wall or through a window frame.

This module computes the conservatively-visible sector set: every
sector reachable from the camera sector through two-sided lines
with a live opening (min(ceilings) > max(floors)). Closed doors
(ceiling <= floor) block the flood, so opening a door widens the
set on the next frame with no extra bookkeeping. Pure CPU, no GL
imports; per-frame cost is O(sectors + lines).
"""

from __future__ import annotations

__all__ = ["visible_mask", "visible_set"]


def visible_set(game_map, start_idx: int) -> set:
    """Indices reachable from start_idx through open portals.

    A line is a portal only if it has distinct front/back sectors
    and a live opening. Single-sided lines and closed movers never
    propagate. Unknown start (None/out of range) returns every
    sector (no culling: void/noclip views stay safe).
    """
    sectors = game_map.sectors
    n = len(sectors)
    if start_idx is None or not (0 <= start_idx < n):
        return set(range(n))
    seen = {start_idx}
    stack = [start_idx]
    while stack:
        si = stack.pop()
        sector = sectors[si]
        for line in sector.lines:
            front = line.frontsector
            back = line.backsector
            if front is None or back is None or front is back:
                continue
            if front is sector:
                other = back
            elif back is sector:
                other = front
            else:
                continue
            # NOTE: live opening test (fixed-point ints, no float):
            # closed doors/platforms (no Z overlap) are solid.
            if (min(front.ceilingheight, back.ceilingheight)
                    <= max(front.floorheight, back.floorheight)):
                continue
            try:
                oi = sectors.index(other)
            except ValueError:
                continue
            if oi not in seen:
                seen.add(oi)
                stack.append(oi)
    return seen


def visible_mask(game_map, start_idx: int) -> list:
    """0/1 per sector (1 = reachable, for upload_visible)."""
    vis = visible_set(game_map, start_idx)
    return [1 if i in vis else 0 for i in range(len(game_map.sectors))]
