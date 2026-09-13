"""Tests for statusbar.py: vanilla ammo row layout and readout mapping."""

from pydoom.player import (
    AM_CELL,
    AM_CLIP,
    AM_MISL,
    AM_SHELL,
    WEAPON_AMMO,
    WP_BFG,
    WP_MISSILE,
    WP_PLASMA,
)
from pydoom.statusbar import MINI_Y


def test_mini_ammo_rows_run_bull_shel_rokt_cell():
    """Right-column rows follow the STBAR labels top-down: rockets
    above cells (a swap here reads as guns eating the wrong ammo)."""
    by_y = [ammo for ammo, _y in sorted(MINI_Y.items(), key=lambda kv: kv[1])]
    assert by_y == [AM_CLIP, AM_SHELL, AM_MISL, AM_CELL]
    assert sorted(MINI_Y.values()) == [173, 179, 185, 191]


def test_big_readout_follows_weapon_ammo():
    """The big current-ammo number tracks each gun's own ammo type."""
    assert WEAPON_AMMO[WP_MISSILE] == AM_MISL
    assert WEAPON_AMMO[WP_PLASMA] == AM_CELL
    assert WEAPON_AMMO[WP_BFG] == AM_CELL
