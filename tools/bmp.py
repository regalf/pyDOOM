"""Minimal BMP screenshot helpers (stdlib only).

pygame.image.save needs SDL_image, which headless/dummy-driver
environments may lack, so screenshots use this instead.
"""

from __future__ import annotations

import os
import struct
import zlib

__all__ = ["save_rgb", "save_surface", "save_png_rgb", "save_png_surface"]


def save_rgb(path: str, width: int, height: int, rgb: bytes) -> None:
    """Save a top-down RGB buffer as 24-bit BMP."""
    if len(rgb) != width * height * 3:
        raise ValueError("RGB buffer size does not match dimensions")
    stride = (width * 3 + 3) & ~3
    pixels = bytearray()
    for y in range(height - 1, -1, -1):  # BMP rows are bottom-up, BGR order
        row = rgb[y * width * 3 : (y + 1) * width * 3]
        for i in range(0, len(row), 3):
            r, g, b = row[i], row[i + 1], row[i + 2]
            pixels += bytes((b, g, r))
        pixels += b"\x00" * (stride - width * 3)
    info = struct.pack(
        "<IIIHHIIIIII", 40, width, height, 1, 24, 0, len(pixels), 0, 0, 0, 0
    )
    file_size = 14 + len(info) + len(pixels)
    with open(path, "wb") as f:
        f.write(struct.pack("<2sIHHI", b"BM", file_size, 0, 0, 14 + len(info)))
        f.write(info)
        f.write(pixels)
    # Guard against silent truncation (full disks, flaky filesystems):
    # a short file would otherwise look like a valid but black image.
    actual = os.path.getsize(path)
    if actual != file_size:
        raise IOError(f"short write: {actual} of {file_size} bytes to {path}")


def save_surface(path: str, surface) -> None:
    """Save a pygame Surface as 24-bit BMP."""
    import pygame

    w, h = surface.get_size()
    save_rgb(path, w, h, pygame.image.tobytes(surface, "RGB"))


def _png_chunk(chunk_type: bytes, data: bytes) -> bytes:
    body = chunk_type + data
    return (
        struct.pack(">I", len(data)) + body
        + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)
    )


def save_png_rgb(path: str, width: int, height: int, rgb: bytes) -> None:
    """Save a top-down RGB buffer as PNG (stdlib zlib only)."""
    if len(rgb) != width * height * 3:
        raise ValueError("RGB buffer size does not match dimensions")
    raw = bytearray()
    for y in range(height):
        raw.append(0)  # filter type 0 (None)
        raw += rgb[y * width * 3 : (y + 1) * width * 3]
    png = [b"\x89PNG\r\n\x1a\n"]
    png.append(
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
    )
    png.append(_png_chunk(b"IDAT", zlib.compress(bytes(raw))))
    png.append(_png_chunk(b"IEND", b""))
    blob = b"".join(png)
    with open(path, "wb") as f:
        f.write(blob)
    actual = os.path.getsize(path)
    if actual != len(blob):
        raise IOError(f"short write: {actual} of {len(blob)} bytes to {path}")


def save_png_surface(path: str, surface) -> None:
    """Save a pygame Surface as PNG (stdlib zlib only)."""
    import pygame

    w, h = surface.get_size()
    save_png_rgb(path, w, h, pygame.image.tobytes(surface, "RGB"))
