"""Tests for pyDOOM.py: WAD discovery and viewer argv building."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pyDOOM import accept_wad_drop, build_viewer_args, find_wads, maps_in


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


def test_drop_ignores_non_wad(tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_bytes(b"hello")
    picked, note = accept_wad_drop(str(txt), str(tmp_path))
    assert picked is None and "not a .WAD" in note
    assert [p.name for p in tmp_path.iterdir()] == ["notes.txt"]


def test_drop_rejects_bad_magic(tmp_path):
    fake = tmp_path / "fake.wad"
    fake.write_bytes(b"NOPE" + b"\x00" * 100)
    picked, note = accept_wad_drop(str(fake), str(tmp_path))
    assert picked is None and "not a WAD" in note


def test_drop_copies_pwad_and_selects(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    pw = src / "extra.wad"
    pw.write_bytes(b"PWAD" + b"\x00" * 12)  # NOTE: magic is all we check
    picked, note = accept_wad_drop(str(pw), str(tmp_path))
    assert picked == "extra.wad" and "added" in note
    assert (tmp_path / "extra.wad").read_bytes() == pw.read_bytes()


def test_drop_from_launcher_dir_just_selects():
    picked, note = accept_wad_drop("DOOM1.WAD", os.path.dirname(
        os.path.abspath("DOOM1.WAD")))
    assert picked == "DOOM1.WAD" and "selected" in note


def test_drop_existing_name_never_overwrites(tmp_path):
    (tmp_path / "extra.wad").write_bytes(b"IWAD")
    src = tmp_path / "src"
    src.mkdir()
    other = src / "extra.wad"
    other.write_bytes(b"IWAD" + b"\x01" * 10)
    picked, note = accept_wad_drop(str(other), str(tmp_path))
    assert picked == "extra.wad" and "already listed" in note
    assert (tmp_path / "extra.wad").read_bytes() == b"IWAD"


def test_video_tab_rows_conditional():
    from pyDOOM import video_tab_rows
    assert video_tab_rows("opengl") == ("api", "resolution")
    assert video_tab_rows("software") == ("api", "scale")
    assert video_tab_rows("bogus") == ("api", "scale")


def test_res_scale_label_mapping():
    from pyDOOM import res_label_to_value, scale_label_to_value
    assert res_label_to_value("960X600") == "960x600"
    assert res_label_to_value("1920X1200") == "1920x1200"
    assert res_label_to_value("bogus") is None
    assert res_label_to_value("") is None
    assert scale_label_to_value("100%") == 1
    assert scale_label_to_value("300%") == 3
    assert scale_label_to_value("500%") is None
    assert scale_label_to_value("x") is None
