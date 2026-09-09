"""Dump the wall textures and flats used by a map as BMP montages.

Usage: python tools/dump_textures.py [MAP] [OUTDIR]

Transparent texels are shown in magenta.
"""

import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))  # sibling tool modules (bmp)

from bmp import save_rgb
from pydoom.mapdata import Map
from pydoom.palette import load_playpal
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

MAGENTA = (255, 0, 255)


def blit_textured(
    canvas: bytearray,
    stride: int,
    dx: int,
    dy: int,
    pixels: bytes,
    mask: bytes,
    width: int,
    height: int,
    palette: list[tuple[int, int, int]],
) -> None:
    for y in range(height):
        for x in range(width):
            i = y * width + x
            rgb = palette[pixels[i]] if mask[i] else MAGENTA
            o = (dy + y) * stride + (dx + x) * 3
            canvas[o : o + 3] = bytes(rgb)


def montage(
    cells: list[tuple[bytes, bytes, int, int]],
    palette: list[tuple[int, int, int]],
    cols: int,
    pad: int,
) -> tuple[int, int, bytes]:
    cw = max(w for _, _, w, _ in cells) + pad
    ch = max(h for _, _, _, h in cells) + pad
    rows = math.ceil(len(cells) / cols)
    w, h = cols * cw + pad, rows * ch + pad
    canvas = bytearray(b"\x00" * (w * h * 3))
    for i, (pixels, mask, tw, th) in enumerate(cells):
        blit_textured(
            canvas, w * 3, pad + (i % cols) * cw, pad + (i // cols) * ch,
            pixels, mask, tw, th, palette,
        )
    return w, h, bytes(canvas)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    map_name = args[0].upper() if len(args) > 0 else "E1M1"
    outdir = args[1] if len(args) > 1 else "/tmp/opencode"
    os.makedirs(outdir, exist_ok=True)
    root = os.path.join(os.path.dirname(__file__), "..")

    wad = WadFile(os.path.join(root, "DOOM1.WAD"))
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, map_name)
    texman.resolve_map(game_map)
    palette = load_playpal(wad.read_lump("PLAYPAL"))

    # Wall textures referenced by name (skips the "-" NoTexture marker,
    # which also maps to index 0).
    names = sorted(
        {
            n
            for s in game_map.sides
            for n in (s.top_name, s.mid_name, s.bottom_name)
            if n and n != "-"
        }
    )
    print(f"{map_name}: {len(names)} wall textures, "
          f"{texman.numflats} flats in WAD")
    tcells = []
    for n in names:
        t = texman.textures[texman.texture_num_for_name(n)]
        pixels = bytearray(t.width * t.height)
        mask = bytearray(t.width * t.height)
        for x in range(t.width):
            col, m = texman.get_column(texman.texture_num_for_name(n), x)
            pixels[x :: t.width] = col
            mask[x :: t.width] = m
        print(f"  {t.name:8s} {t.width:3d}x{t.height:<3d} "
              f"{len(t.patches)} patch(es)")
        tcells.append((bytes(pixels), bytes(mask), t.width, t.height))
    w, h, rgb = montage(tcells, palette, cols=8, pad=4)
    out = os.path.join(outdir, f"{map_name.lower()}_textures.bmp")
    save_rgb(out, w, h, rgb)
    print(f"saved {out}")

    fnames = sorted(
        {s.floorpic_name for s in game_map.sectors}
        | {s.ceilingpic_name for s in game_map.sectors}
    )
    fcells = []
    for n in fnames:
        flat = texman.get_flat(texman.flat_num_for_name(n))
        fcells.append(
            (flat, b"\x01" * len(flat), 64, 64)
        )
    w, h, rgb = montage(fcells, palette, cols=8, pad=4)
    out = os.path.join(outdir, f"{map_name.lower()}_flats.bmp")
    save_rgb(out, w, h, rgb)
    print(f"saved {out} ({len(fnames)} flats: {' '.join(fnames)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
