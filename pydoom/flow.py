"""Episode flow (g_game.c lite): exit routing and level carry-over.

Shareware episode: E1M1..E1M8, plus E1M9 reached from the E1M3 secret
exit (and returning to E1M4). E1M8 has no next map: its exit wins.
"""

from __future__ import annotations

NEXT_MAP = {"E1M1": "E1M2", "E1M2": "E1M3", "E1M3": "E1M4",
            "E1M4": "E1M5", "E1M5": "E1M6", "E1M6": "E1M7",
            "E1M7": "E1M8", "E1M9": "E1M4"}
SECRET_MAP = {"E1M3": "E1M9"}


def next_map(marker: str, secret: bool) -> str | None:
    """Map to load after an exit, or None for episode complete."""
    if secret and marker in SECRET_MAP:
        return SECRET_MAP[marker]
    return NEXT_MAP.get(marker)  # E1M8 falls out: victory


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
