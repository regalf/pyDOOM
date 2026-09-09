"""Render one player-view frame headless and save it as PNG or BMP.

Usage: python tools/render_frame.py [MAP] [OUT] [--x N] [--y N] [--angle DEG]

The output format follows the OUT extension (.png default, .bmp also
supported). Default viewpoint: player-1 start of the map, with viewz
41 units above the floor (VIEWHEIGHT), like a standing player.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))  # sibling tool modules (bmp)

from bmp import save_png_rgb, save_rgb
from pydoom.angles import thing_degrees_to_bam
from pydoom.mapdata import Map
from pydoom.palette import load_playpal
from pydoom.renderer import SCREENHEIGHT, SCREENWIDTH, Renderer
from pydoom.textures import TextureManager
from pydoom.wad import WadFile


def _usage(msg: str) -> int:
    print(f"error: {msg}\n"
          f"usage: render_frame.py [MAP] [OUT] "
          f"[--x N] [--y N] [--angle DEG] (also --x=N form)")
    return 2


def main() -> int:
    argv = sys.argv[1:]
    pos: list[str] = []
    opts: dict[str, str] = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--"):
            if "=" in a:
                k, v = a[2:].split("=", 1)
            else:
                k = a[2:]
                if i + 1 < len(argv) and not argv[i + 1].startswith("--"):
                    v = argv[i + 1]
                    i += 1
                else:
                    return _usage(f"option --{k} needs a value")
            if k not in ("x", "y", "angle"):
                return _usage(f"unknown option --{k}")
            opts[k] = v
        else:
            pos.append(a)
        i += 1
    map_name = pos[0].upper() if len(pos) > 0 else "E1M1"
    out = pos[1] if len(pos) > 1 else "/tmp/opencode/e1m1_frame.png"
    if os.path.isdir(out):
        out = os.path.join(out, f"{map_name.lower()}_frame.png")
    root = os.path.join(os.path.dirname(__file__), "..")

    try:
        wad = WadFile(os.path.join(root, "DOOM1.WAD"))
    except FileNotFoundError:
        return _usage("DOOM1.WAD not found next to the project root")
    texman = TextureManager(wad)
    try:
        game_map = Map.from_wad(wad, map_name)
    except KeyError:
        return _usage(f"map {map_name} not found "
                      f"(available: {' '.join(wad.list_maps())})")
    texman.resolve_map(game_map)
    palette = load_playpal(wad.read_lump("PLAYPAL"))

    start = next(t for t in game_map.things if t.type == 1)
    try:
        x = int(opts.get("x", start.x)) << 16
        y = int(opts.get("y", start.y)) << 16
        angle = thing_degrees_to_bam(int(opts.get("angle", start.angle)))
    except ValueError:
        return _usage("x, y and angle must be integers")

    renderer = Renderer(wad, texman)
    fb = renderer.render_view(game_map, x, y, angle)

    rgb = bytearray(SCREENWIDTH * SCREENHEIGHT * 3)
    for i, idx in enumerate(fb.flat):
        rgb[i * 3 : i * 3 + 3] = bytes(palette[int(idx)])
    ext = os.path.splitext(out)[1].lower()
    if ext == ".png":
        save_png_rgb(out, SCREENWIDTH, SCREENHEIGHT, bytes(rgb))
    elif ext == ".bmp":
        save_rgb(out, SCREENWIDTH, SCREENHEIGHT, bytes(rgb))
    else:
        return _usage(f"unknown image extension {ext!r} (use .png or .bmp)")
    import numpy as np

    print(f"saved {out}")
    print(f"drawsegs: {len(renderer.drawsegs)}, "
          f"lit pixels: {int(np.count_nonzero(fb))}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
