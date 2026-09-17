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
    assert video_tab_rows("openglv1") == ("api", "resolution")
    assert video_tab_rows("openglv2") == ("api", "resolution")
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


def test_build_viewer_args_mods_byte_stable():
    base = build_viewer_args("DOOM1.WAD", "E1M1", "normal")
    assert not [a for a in base if "mod" in a]  # NOTE: no mods, no flags
    off = build_viewer_args("DOOM1.WAD", "E1M1", "normal",
                            mods_enabled=False)
    assert off[-1] == "--no-mods"
    picks = build_viewer_args("DOOM1.WAD", "E1M1", "normal",
                              mods_on=("b",), mods_off=("a",))
    assert "--mod-on=b" in picks and "--mod-off=a" in picks


def test_mod_tab_rows_master_and_reasons():
    from pyDOOM import mod_tab_rows
    status = [("hi", "0.1.0", "on", ""),
              ("gl", "0.2.0", "refused", "NEEDS opengl"),
              ("bad", "?", "error", "BAD MANIFEST: x")]
    ids, labels = mod_tab_rows(status, True)
    assert ids == [None, "hi", "gl", "bad"]
    assert labels[0] == "MOD LOADER: ON"
    assert labels[1] == "hi 0.1.0 ON "
    assert labels[2] == "gl 0.2.0 OFF NEEDS opengl"
    assert labels[3] == "bad ? OFF BAD MANIFEST: x"
    ids, labels = mod_tab_rows([], False)
    assert labels == ["MOD LOADER: OFF", "(no mods found)"]
    assert ids[1] == ""  # NOTE: placeholder never toggles


def test_mod_overrides_only_deviations():
    from pyDOOM import mod_overrides
    states = [("a", True, True), ("b", False, True), ("c", True, False),
              ("d", False, False)]
    on, off = mod_overrides(states)
    assert (on, off) == ({"c"}, {"b"})


def test_mod_overrides_apply_off_wins(tmp_path):
    from pydoom.ext import ModManager
    for mid in ("a", "b"):
        d = tmp_path / mid
        d.mkdir()
        (d / "mod.toml").write_text(
            f'id = "{mid}"\nversion = "0.1.0"\napi_version = 1\n')
        (d / "mod.py").write_text(
            "from pydoom.ext import Mod\n\n\n"
            f"class M(Mod):\n    id = \"{mid}\"\n    version = \"0.1.0\"\n\n\n"
            "MOD = M()\n")
    mgr = ModManager(str(tmp_path))
    mgr.discover()
    mgr.apply_overrides({"a", "zzz"}, {"a", "b"})  # NOTE: off wins, zzz ignored
    states = {mid: s for mid, _v, s, _r in mgr.status()}
    assert states == {"a": "off", "b": "off"}


def test_cfg_roundtrips_mod_picks(tmp_path):
    from pydoom.menu import Settings, settings_load, settings_save
    cfg = Settings()
    cfg.mods_enabled = False
    cfg.mods_on = {"c"}
    cfg.mods_off = {"b"}
    path = str(tmp_path / "pydoom.cfg")
    settings_save(path, cfg)
    back = Settings()
    settings_load(path, back)
    assert back.mods_enabled is False
    assert (back.mods_on, back.mods_off) == ({"c"}, {"b"})
    plain = Settings()  # NOTE: old cfgs without mod lines keep defaults
    settings_save(path, plain)
    text = open(path).read()
    assert "mods_enabled 1" in text and "mod_on" not in text
