"""Sky surface (milestone H, phase 3, sky slice).

Camera-following cylinder (64 segments) with engine-exact U: one
point_to_angle2 lookup anchors vertex 0, then +16 columns per
segment (a full turn is 1024 columns over 64 segments, exactly).
V comes from the fragment row like the software sky column
(v = SKYTEXTUREMID + (row - cy) * 200/H, iscale is 1 texel/row),
so at 320x200 it reproduces _draw_sky_plane texel for texel up to
bearing-interpolation dust. Pure CPU, no GL imports.
"""

from __future__ import annotations

import math

import numpy as np

from pydoom.angles import point_to_angle2

__all__ = [
    "SKY_HALF",
    "SKY_MID",
    "SKY_RADIUS",
    "SKY_SEGS",
    "build_sky_verts",
    "sky_index",
]

SKY_SEGS = 64
SKY_RADIUS = 20000.0  # world units (inside far plane, depth-tested)
SKY_HALF = 25000.0  # covers screen corners with margin
SKY_MID = 100.0  # SKYTEXTUREMID texels (renderer.py, r_sky idea)


def build_sky_verts(camx: int, camy: int, viewz: int) -> np.ndarray:
    """Cylinder verts (130, 4) float32 [x, y, z, u]: top ring then
    bottom ring, vertex 64 duplicating 0 with u + 4.0 (seam stays
    continuous under REPEAT). camx/camy fixed-point ints."""
    n = SKY_SEGS
    verts = np.zeros(((n + 1) * 2, 4), dtype=np.float32)
    cx, cy = camx / 65536.0, camy / 65536.0
    vz = viewz / 65536.0
    x0 = int((cx + SKY_RADIUS) * 65536.0)
    base = (point_to_angle2(camx, camy, x0, camy) >> 22) / 256.0
    for i in range(n + 1):
        ang = 2.0 * math.pi * i / n
        # NOTE: CCW ring matches the BAM sense, so columns grow by
        # exactly 16 per segment with no wrap jump to repair.
        vx = cx + SKY_RADIUS * math.cos(ang)
        vy = cy + SKY_RADIUS * math.sin(ang)
        u = base + i * 16.0 / 256.0
        verts[i] = (vx, vy, vz + SKY_HALF, u)
        verts[n + 1 + i] = (vx, vy, vz - SKY_HALF, u)
    # NOTE: exact seam duplicate (cos/sin dust would crack it).
    verts[n][:3] = verts[0][:3]
    verts[2 * n + 1][:3] = verts[n + 1][:3]
    return verts


def sky_index() -> np.ndarray:
    """Static index (one per cylinder, uploaded once)."""
    n = SKY_SEGS
    index = np.zeros(n * 6, dtype=np.uint32)
    for s in range(n):
        t0, b0, t1, b1 = s, n + 1 + s, s + 1, n + 1 + s + 1
        index[s * 6:s * 6 + 6] = (t0, b0, b1, t0, b1, t1)
    return index
