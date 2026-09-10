"""Tests for saveg.py: validation, slots, blob round-trip remap."""

import os

import pytest

from pydoom import saveg
from pydoom.doors import World
from pydoom.mapdata import Map
from pydoom.mobjs import ThingIndex, spawn_map
from pydoom.physics import Physics
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def test_validate_rejects_garbage():
    assert saveg.validate(None, ["E1M1"]) == "EMPTY SLOT"
    assert saveg.validate({}, ["E1M1"]) == "WRONG VERSION"
    assert saveg.validate({"version": 1, "marker": "E9M9"},
                           ["E1M1"]) == "UNKNOWN MAP"
    assert saveg.validate({"version": 1, "marker": "E1M1"},
                           ["E1M1"]) == "CORRUPT SAVE"


def test_slot_names_empty_then_saved(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert saveg.slot_name(0) == "EMPTY"
    saveg.write_slot(2, {"version": 1, "name": "HANGAR"})
    assert saveg.slot_name(2) == "HANGAR"
    assert saveg.slot_name(0) == "EMPTY"
    assert saveg.read_slot(0) is None
    assert saveg.read_slot(2)["name"] == "HANGAR"


@requires_wad
def test_blob_round_trip_remaps_sectors():
    from pydoom.player import PlayerState
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    played = Map.from_wad(wad, "E1M1")
    phys = Physics(played)
    index = ThingIndex(played)
    phys.things = index
    world = World(played, texman)
    mobjs = spawn_map(played, phys, index, "normal")
    ps = PlayerState()
    ps.ammo[0] = 123
    # NOTE: mid-level dynamics: a moved ceiling, a dead monster.
    sec = played.sectors[10]
    sec.ceilingheight -= 1000
    sec.lightlevel = 99
    mobjs[3].health = -50
    mobjs[3].dead = True
    blob = saveg.build_blob(played.sectors, world.thinkers, mobjs, ps)
    fresh = Map.from_wad(wad, "E1M1")
    sectors_old, thinkers, mobjs_new, ps_new = saveg.unpack_blob(
        blob, fresh.sectors)
    saveg.sector_state(sectors_old, fresh.sectors)
    assert fresh.sectors[10].ceilingheight == sec.ceilingheight
    assert fresh.sectors[10].lightlevel == 99
    assert all(mo.sector is None or mo.sector in fresh.sectors
               for mo in mobjs_new)
    assert mobjs_new[3].health == -50
    assert ps_new.ammo[0] == 123
    assert len(mobjs_new) == len(mobjs)
