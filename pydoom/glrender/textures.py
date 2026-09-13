"""GL texture data (milestone H, phase 1, textures slice).

CPU-side assembly of GPU-ready blobs in palette-index space (pure
numpy, no GL imports): the fragment shader does colormap-then-
palette exactly like the software column/span drawers, so textures
upload unmapped.

Formats and contracts (the upload slice must honor these):

* Walls: one RG8 blob per texture, row-major top-down (row 0 is the
  texture top: software frac>>16 == 0 addresses it, so normalized
  v = v_texels / height with v = 0 at the first memory row needs no
  flip). R = palette index, G = opaque mask (0/255 from
  get_column: masked mids keep real holes, e.g. BRNBIGC).
* U wrap is (widthmask + 1), NOT width: software samples col &
  widthmask, so non-power-of-two widths can never address their tail
  columns (the manifest carries wrap per texture).
* Flats: uniform 64x64, stacked R8 (fully opaque, no mask channel).
* Upload with GL_UNPACK_ALIGNMENT = 1 (row strides are arbitrary).

Out of scope (documented, not silent):

* No flat/texture animation: the software engine has none either
  (texturetranslation/flattranslation are identity), so static blobs
  are parity, not a gap. Animated switches MUTATE sidedef texnums at
  runtime (doors.py switchlist): static quads cache texnum, so Phase
  3 must invalidate/re-resolve quads on switch flips.
* Packing (atlas vs array vs per-texture upload) is the upload
  slice's decision: this module hands it blobs + manifest.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from pydoom.textures import FLAT_SIZE, TextureManager

__all__ = [
    "FlatTextureSet",
    "WallTextureSet",
    "build_flat_textures",
    "build_wall_textures",
    "flatnums_used",
    "wall_texnums_used",
]


@dataclass
class WallTextureSet:
    """Per-texture RG8 blobs (index + opaque mask) plus manifest."""

    order: list[int] = field(default_factory=list)  # texnums, sorted
    index_of: dict[int, int] = field(default_factory=dict)
    blobs: list[bytes] = field(default_factory=list)  # RG8 each
    sizes: list[tuple[int, int]] = field(default_factory=list)
    wraps: list[int] = field(default_factory=list)  # widthmask + 1


@dataclass
class FlatTextureSet:
    """Stacked 64x64 R8 flats plus manifest (sky excluded upstream)."""

    order: list[int] = field(default_factory=list)  # flatnums, sorted
    index_of: dict[int, int] = field(default_factory=dict)
    blob: bytes = b""


def wall_texnums_used(wall_geometry) -> list:
    """Sorted texnums referenced by wall quads (0 = "-" never stored:
    build_walls omits those quads, like the software `if midtexture`
    tiers)."""
    return sorted({q.texnum for q in wall_geometry.quads})


def flatnums_used(plane_geometry) -> list:
    """Sorted flatnums referenced by plane tris (sky tagged -1 and
    excluded: the sky surface is textured at draw time)."""
    return sorted({t.flat for t in plane_geometry.tris if t.flat >= 0})


def build_wall_textures(texman: TextureManager,
                        texnums) -> WallTextureSet:
    """Assemble RG8 blobs for wall textures (columns via get_column:
    the exact bytes-and-mask the software drawer samples)."""
    out = WallTextureSet()
    for texnum in sorted(texnums):
        tex = texman.textures[texnum]
        cols = [texman.get_column(texnum, c) for c in range(tex.width)]
        blob = np.zeros((tex.height, tex.width, 2), dtype=np.uint8)
        for x, (pixels, mask) in enumerate(cols):
            blob[:, x, 0] = np.frombuffer(pixels, dtype=np.uint8)
            blob[:, x, 1] = np.frombuffer(mask, dtype=np.uint8) * 255
        out.order.append(texnum)
        out.index_of[texnum] = len(out.blobs)
        out.blobs.append(blob.tobytes())
        out.sizes.append((tex.width, tex.height))
        out.wraps.append(tex.widthmask + 1)
    return out


def build_flat_textures(texman: TextureManager,
                        flatnums) -> FlatTextureSet:
    """Stack raw 64x64 flats (get_flat bytes, verbatim)."""
    out = FlatTextureSet()
    parts = []
    for flatnum in sorted(flatnums):
        flat = texman.get_flat(flatnum)
        assert len(flat) == FLAT_SIZE, (flatnum, len(flat))
        out.order.append(flatnum)
        out.index_of[flatnum] = len(parts)
        parts.append(flat)
    out.blob = b"".join(parts)
    return out
