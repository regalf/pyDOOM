"""Episode flow (g_game.c lite): exit routing and level carry-over.

Registered Doom 1: E1M1..E3M9. Secret exits ride the exit linedef
(kind "secret", any map); by map design those live on E1M3, E2M5 and
E3M6 (E4M2 arrives with Ultimate). A secret level returns to its
episode track (E1M4/E2M6/E3M7); M8 exits win the episode (victory).
"""

from __future__ import annotations

NEXT_MAP = {"E1M1": "E1M2", "E1M2": "E1M3", "E1M3": "E1M4",
            "E1M4": "E1M5", "E1M5": "E1M6", "E1M6": "E1M7",
            "E1M7": "E1M8", "E1M9": "E1M4",
            "E2M1": "E2M2", "E2M2": "E2M3", "E2M3": "E2M4",
            "E2M4": "E2M5", "E2M5": "E2M6", "E2M6": "E2M7",
            "E2M7": "E2M8", "E2M9": "E2M6",
            "E3M1": "E3M2", "E3M2": "E3M3", "E3M3": "E3M4",
            "E3M4": "E3M5", "E3M5": "E3M6", "E3M6": "E3M7",
            "E3M7": "E3M8", "E3M9": "E3M7"}
SECRET_MAP = {"E1M3": "E1M9", "E2M5": "E2M9", "E3M6": "E3M9"}


def next_map(marker: str, secret: bool) -> str | None:
    """Map to load after an exit, or None for episode complete."""
    if secret:
        # NOTE: vanilla routes every secret exit at E?M9 (only the
        # SECRET_MAP levels carry one by map design).
        return SECRET_MAP.get(marker, f"E{marker[1]}M9")
    return NEXT_MAP.get(marker)  # M8 falls out: victory


def strip_for_next_level(ps) -> None:
    """G_PlayerFinishLevel: keep guns/ammo/armor/health, drop keys and
    powers (pending settles to ready)."""
    ps.keys = 0
    ps.powers = {}
    ps.pendingweapon = ps.readyweapon
    ps.switchtics = 0
    ps.killcount = ps.itemcount = ps.secretcount = 0  # fresh tally


# NOTE: G_InitNew fast/nightmare tables (sergeant run/pain tics halved,
# bruiser/head/troop shots at 20). Pristine copies make the switch
# idempotent (vanilla shifts live values, which double-halves on
# repeated fast starts; every demo does a single InitNew from boot).
_PRISTINE: tuple | None = None


def init_new(skill: str, fast: bool) -> None:
    """G_InitNew sim part: M_ClearRandom + fast/nightmare adjustments.

    Runs on fresh runs only (boot, menu new game, demo start), never on
    level transitions: the demo RNG stream spans levels unbroken.
    """
    from pydoom.fixed import FRACUNIT
    from pydoom.info import MOBJ_TYPES, MT_INDEX, STATES, STATE_INDEX
    from pydoom.m_random import clear_random
    global _PRISTINE
    first = STATE_INDEX["S_SARG_RUN1"]
    last = STATE_INDEX["S_SARG_PAIN2"]
    shots = ("BRUISERSHOT", "HEADSHOT", "TROOPSHOT")
    if _PRISTINE is None:
        _PRISTINE = (
            {i: STATES[i][2] for i in range(first, last + 1)},
            {name: MOBJ_TYPES[MT_INDEX[name]][10] for name in shots},
        )
    clear_random()
    enable = bool(fast) or skill == "nightmare"
    tics, speeds = _PRISTINE
    for i, pristine in tics.items():
        sprite, frame, _t, nxt, action = STATES[i]
        STATES[i] = (sprite, frame, pristine >> 1 if enable else pristine,
                     nxt, action)
    for name, pristine in speeds.items():
        rec = list(MOBJ_TYPES[MT_INDEX[name]])
        rec[10] = 20 * FRACUNIT if enable else pristine
        MOBJ_TYPES[MT_INDEX[name]] = tuple(rec)
