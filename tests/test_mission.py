"""Tests for mission detection (d_main.c CheckIWAD lite)."""

import os

import pytest

from pydoom.demo import DemoHeader
from pydoom.mission import detect, episode_count
from pydoom.wad import WadFile

ROOT = os.path.join(os.path.dirname(__file__), "..")
SHAREWARE_WAD = os.path.join(ROOT, "DOOM1.WAD")
DOOM_WAD = os.path.join(ROOT, "doom.wad")

requires_doom_wad = pytest.mark.skipif(
    not os.path.exists(DOOM_WAD), reason="doom.wad not found")


def test_detect_shareware():
    assert detect(WadFile(SHAREWARE_WAD)) == "shareware"
    assert episode_count("shareware") == 1


@requires_doom_wad
def test_detect_registered():
    assert detect(WadFile(DOOM_WAD)) == "registered"
    assert episode_count("registered") == 3


def test_demo_marker_mission_clamp():
    assert DemoHeader(episode=2, map=5).marker() == "E1M5"  # shareware
    assert DemoHeader(episode=2, map=5).marker("registered") == "E2M5"
    assert DemoHeader(episode=9, map=9).marker("registered") == "E3M9"
