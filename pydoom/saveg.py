"""Savegames (p_saveg.c idea, pickle format, not vanilla-compatible).

A bundle is one pickle blob holding the whole live level graph
(sectors, thinkers, mobjs, player state) plus scalars, so shared
references survive the round trip. Restoring remaps sector links onto
a freshly loaded map; everything transient (blockmap index, renderer,
thinker clock is kept) is rebuilt by the viewer.
"""

import os
import pickle

SAVE_VERSION = 1
SLOT_COUNT = 6
SAVE_DIR = "savegames"
EMPTY = "EMPTY"


def slot_path(slot: int) -> str:
    """pydoom3.pkl beside the working directory (vanilla doomsavN)."""
    return os.path.join(SAVE_DIR, f"pydoom{slot}.pkl")


def write_slot(slot: int, bundle: dict) -> None:
    """Persist one snapshot bundle (creates the dir on demand)."""
    os.makedirs(SAVE_DIR, exist_ok=True)
    with open(slot_path(slot), "wb") as f:
        pickle.dump(bundle, f)


def read_slot(slot: int) -> dict | None:
    """Load one bundle, or None when missing/corrupt."""
    try:
        with open(slot_path(slot), "rb") as f:
            bundle = pickle.load(f)
    except (OSError, pickle.PickleError, EOFError, ValueError):
        return None
    return bundle if isinstance(bundle, dict) else None


def slot_name(slot: int) -> str:
    """Savegame string for the load menu (vanilla M_ReadSaveStrings)."""
    bundle = read_slot(slot)
    if bundle is None:
        return EMPTY
    name = bundle.get("name", "")
    return name if isinstance(name, str) and name else EMPTY


def validate(bundle: dict | None, maps) -> str | None:
    """Reject foreign/corrupt bundles with a menu message, else None."""
    if bundle is None:
        return "EMPTY SLOT"
    if bundle.get("version") != SAVE_VERSION:
        return "WRONG VERSION"
    if bundle.get("marker") not in maps:
        return "UNKNOWN MAP"
    for key in ("blob", "skill", "cam", "time", "rng", "player"):
        if key not in bundle:
            return "CORRUPT SAVE"
    return None


def build_blob(sectors, thinkers, mobjs, ps) -> bytes:
    """One pickle holding the shared level graph (identity preserved)."""
    return pickle.dumps((list(sectors), list(thinkers), list(mobjs), ps))


def unpack_blob(blob: bytes, fresh_sectors):
    """Split the graph and repoint every sector link at fresh sectors.

    Returns (sectors_old, thinkers, mobjs, ps). Targets/attackers stay
    valid: they point inside the same mobjs list, which is reused as-is.
    """
    sectors_old, thinkers, mobjs, ps = pickle.loads(blob)
    pos = {id(s): i for i, s in enumerate(sectors_old)}
    for th in thinkers:
        sec = getattr(th, "sector", None)
        if sec is not None and id(sec) in pos:
            th.sector = fresh_sectors[pos[id(sec)]]
    for mo in mobjs:
        if mo.sector is not None and id(mo.sector) in pos:
            mo.sector = fresh_sectors[pos[id(mo.sector)]]
    return sectors_old, thinkers, mobjs, ps


def sector_state(sectors_old, fresh_sectors) -> None:
    """Copy mid-level dynamics (heights, light, secret state) over."""
    for old, fresh in zip(sectors_old, fresh_sectors):
        fresh.floorheight = old.floorheight
        fresh.ceilingheight = old.ceilingheight
        fresh.lightlevel = old.lightlevel
        fresh.special = old.special
