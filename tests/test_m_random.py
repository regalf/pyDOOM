"""Tests for the deterministic random tables (m_random.c)."""

import os
import re

import pytest

from pydoom.m_random import _RNDTABLE, clear_random, m_random as menu_rand
from pydoom.m_random import p_random

TABLES_C = os.path.join(
    os.path.dirname(__file__),
    "..",
    "DOOM-master",
    "linuxdoom-1.10",
    "m_random.c",
)

requires_c = pytest.mark.skipif(
    not os.path.exists(TABLES_C), reason="m_random.c not found"
)


@requires_c
def test_table_matches_c():
    with open(TABLES_C) as f:
        src = f.read()
    body = src.split("rndtable[256] = {", 1)[1].split("};", 1)[0]
    values = [int(v) for v in re.findall(r"\d+", body)]
    assert len(values) == 256
    assert list(_RNDTABLE) == values


def test_streams_cycle_independently():
    clear_random()
    assert [p_random() for _ in range(3)] == [8, 109, 220]
    assert [menu_rand() for _ in range(2)] == [8, 109]
    # 256 calls wrap around to the start (index 0 reads table[1] first).
    clear_random()
    first = [p_random() for _ in range(256)]
    assert first[0] == 8 and first[-1] == 0
    assert p_random() == 8  # wrapped
    clear_random()
    assert p_random() == 8 and menu_rand() == 8
