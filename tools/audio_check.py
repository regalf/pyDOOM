"""Manual audio check: play raw DS lumps in sequence, no gameplay.

Usage: .venv/bin/python tools/audio_check.py
Each lump plays full-volume with gaps; if these sound wrong, the bug
is in decode/mixer setup, not in game logic.
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom import audio
from pydoom.wad import WadFile

NAMES = ["pistol", "shotgn", "doropn", "itemup", "telept", "posit1",
         "plpain", "firsht"]


def main() -> int:
    wad = WadFile(os.path.join(os.path.dirname(__file__), "..",
                               "DOOM1.WAD"))
    if not audio.init(wad):
        print("mixer unavailable, silent")
        return 1
    for name in NAMES:
        ok = audio.play(name)
        print(f"{name}: {'playing' if ok else 'SKIPPED'}")
        time.sleep(0.9)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
