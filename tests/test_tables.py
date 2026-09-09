"""Tests for tables.py: the generated tables must match tables.c verbatim."""

import os
import sys

import pytest

from pydoom import tables

TABLES_C = os.path.join(
    os.path.dirname(__file__),
    "..",
    "DOOM-master",
    "linuxdoom-1.10",
    "tables.c",
)

requires_tables_c = pytest.mark.skipif(
    not os.path.exists(TABLES_C), reason="tables.c not found"
)


@requires_tables_c
def test_generated_tables_match_c():
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
    import gen_tables

    parsed = gen_tables.parse_tables(TABLES_C)
    assert list(tables.finesine) == parsed["finesine"]
    assert list(tables.finetangent) == parsed["finetangent"]
    assert list(tables.tantoangle) == parsed["tantoangle"]


def test_table_sizes():
    assert len(tables.finesine) == 5 * tables.FINEANGLES // 4
    assert len(tables.finetangent) == tables.FINEANGLES // 2
    assert len(tables.tantoangle) == tables.SLOPERANGE + 1


def test_finecosine_offset():
    # finecosine = &finesine[FINEANGLES/4]: cos(0) == sin(PI/2).
    assert tables.finecosine(0) == tables.finesine[tables.FINEANGLES // 4]


def test_slope_div():
    assert tables.slope_div(123, 0) == tables.SLOPERANGE
    assert tables.slope_div(123, 511) == tables.SLOPERANGE
    assert tables.slope_div(512, 512) == tables.SLOPERANGE
    assert tables.slope_div(100, 1000) == (100 << 3) // (1000 >> 8)
