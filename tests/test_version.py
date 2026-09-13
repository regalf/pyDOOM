"""Tests for version.py: VERSION file wins, git/fallback cover the rest."""

from pydoom.version import FALLBACK, get_version


def test_version_file_wins(tmp_path):
    (tmp_path / "VERSION").write_text("9.9.9\n", encoding="utf-8")
    assert get_version(str(tmp_path)) == "9.9.9"


def test_missing_everything_falls_back(tmp_path, monkeypatch):
    import subprocess
    def _boom(*a, **k):
        raise FileNotFoundError("no git here")
    monkeypatch.setattr(subprocess, "run", _boom)
    assert get_version(str(tmp_path)) == FALLBACK


def test_repo_root_reports_file():
    import os
    root = os.path.join(os.path.dirname(__file__), "..")
    assert get_version(root) == open(
        os.path.join(root, "VERSION"), encoding="utf-8").read().strip()
