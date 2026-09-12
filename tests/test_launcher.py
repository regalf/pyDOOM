"""Tests for pyDOOM.py: WAD discovery and viewer argv building."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyDOOM import build_viewer_args, find_wads, maps_in


def test_find_wads_prefers_fallback(tmp_path):
    (tmp_path / "doom.wad").write_bytes(b"x")
    (tmp_path / "DOOM1.WAD").write_bytes(b"x")
    (tmp_path / "extra.wad").write_bytes(b"x")
    assert find_wads(str(tmp_path))[0] == "DOOM1.WAD"


def test_build_viewer_args():
    args = build_viewer_args("DOOM1.WAD", "E1M1", "normal")
    assert args[1].endswith(os.path.join("tools", "doom_view.py"))
    assert args[2] == "E1M1"
    assert args[3].endswith("DOOM1.WAD")
    assert args[4] == "--skill=normal"
    full = build_viewer_args("doom.wad", "E2M1", "hard", True, True,
                             True, True, True, True)
    assert full[2] == "E2M1" and full[4] == "--skill=hard"
    for flag in ("--debug", "--fast", "--respawn", "--nomonsters",
                 "--kinematic", "--extra-hud"):
        assert flag in full


def test_maps_in_real_wad():
    maps = maps_in("DOOM1.WAD")
    assert maps[0] == "E1M1" and "E1M9" in maps
