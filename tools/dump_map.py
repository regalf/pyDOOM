"""Print a summary of a map from the WAD. Usage: python tools/dump_map.py [MAP]."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.mapdata import Map
from pydoom.wad import WadFile

ROOT = os.path.join(os.path.dirname(__file__), "..")


def main() -> None:
    marker = sys.argv[1].upper() if len(sys.argv) > 1 else "E1M1"
    wad = WadFile(os.path.join(ROOT, "DOOM1.WAD"))
    print(f"WAD: {len(wad)} lumps, maps: {' '.join(wad.list_maps())}")
    m = Map.from_wad(wad, marker)
    print(m.summary())
    print(
        f"blockmap org=({m.blockmap.orgx >> 16},{m.blockmap.orgy >> 16}) "
        f"size={m.blockmap.width}x{m.blockmap.height}"
    )
    p1 = [t for t in m.things if t.type == 1]
    print(f"player1 starts: {[(t.x, t.y, t.angle) for t in p1]}")


if __name__ == "__main__":
    main()
