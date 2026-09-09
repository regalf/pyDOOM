"""One-shot generator: extract game data tables into pydoom/info.py.

Parses the SPR_/S_ enums (info.h), MF_ flags (p_mobj.h) and the states[]
/ mobjinfo[] tables (info.c). Only the fields needed to spawn and render
things are kept: sprite names, per-state (sprite, frame) and per-mobjtype
(doomednum, spawnstate, flags).

Usage: python tools/gen_info.py
"""

import os
import re
import sys

BASE = os.path.join(os.path.dirname(__file__), "..", "DOOM-master", "linuxdoom-1.10")
DST = os.path.join(os.path.dirname(__file__), "..", "pydoom", "info.py")


def read(name: str) -> str:
    with open(os.path.join(BASE, name)) as f:
        return f.read()


def parse_enum(src: str, end_marker: str, prefix: str) -> list[str]:
    """Names of an enum in order, up to (excluding) end_marker."""
    names = []
    for line in src.splitlines():
        line = line.split("//")[0]
        if end_marker in line:
            break
        m = re.match(r"\s*(%s\w+)\s*,?" % prefix, line)
        if m:
            names.append(m.group(1))
    return names


def parse_mf_values(src: str) -> dict[str, int]:
    values = {}
    for m in re.finditer(r"\b(MF_\w+)\s*=\s*(0x[0-9a-fA-F]+|\d+)", src):
        values[m.group(1)] = int(m.group(2), 0)
    return values


def top_level_blocks(src: str) -> list[str]:
    """Split a {...} initializer body into top-level {...} entries."""
    blocks, depth, current = [], 0, ""
    for ch in src:
        if ch == "{":
            if depth == 0:
                current = ""
            else:
                current += ch
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                blocks.append(current)
            else:
                current += ch
        elif depth >= 1:
            current += ch
    return blocks


def array_body(src: str, name: str) -> str:
    m = re.search(r"%s\s*\[\w+\]\s*=\s*\{" % name, src)
    assert m, f"array {name} not found"
    start = m.end() - 1
    depth = 0
    for i in range(start, len(src)):
        if src[i] == "{":
            depth += 1
        elif src[i] == "}":
            depth -= 1
            if depth == 0:
                return src[start + 1 : i]
    raise ValueError(f"array {name}: unbalanced braces")


def _parse_fixed(expr: str) -> int:
    """Parse values like 16*FRACUNIT or plain ints."""
    expr = expr.strip()
    if "*" in expr:
        a, b = expr.split("*")
        scale = 65536 if b.strip() == "FRACUNIT" else int(b.strip(), 0)
        return int(a.strip(), 0) * scale
    return int(expr, 0)


def _state_ref(expr: str, s_index: dict) -> int:
    """S_ symbol or a bare state number (0 used for none in spots)."""
    expr = expr.strip()
    return s_index[expr] if expr in s_index else int(expr, 0)


def main() -> None:
    info_h = read("info.h")
    info_c = read("info.c")
    p_mobj_h = read("p_mobj.h")

    spr = parse_enum(info_h.split("spritenum_t")[0], "NUMSPRITES", "SPR_")
    assert spr and spr[0] == "SPR_TROO", spr[:3]
    spr_names = [name[4:] for name in spr]  # bare 4-char lump prefixes
    s_names = parse_enum(info_h.split("spritenum_t")[1], "NUMSTATES", "S_")
    assert s_names and s_names[0] == "S_NULL", s_names[:3]
    s_index = {name: i for i, name in enumerate(s_names)}
    mt_section = info_h.split("} mobjtype_t;")[0].rsplit("typedef enum", 1)[-1]
    mt_names = parse_enum(mt_section, "NUMMOBJTYPES", "MT_")
    assert mt_names and mt_names[0] == "MT_PLAYER", mt_names[:3]
    mt_index = {name: i for i, name in enumerate(mt_names)}
    mf = parse_mf_values(p_mobj_h)
    assert "MF_SHADOW" in mf and "MF_TRANSLATION" in mf

    # states[]: {SPR_X, frame, tics, {action}, nextstate, ...}.
    states = []
    for block in top_level_blocks(array_body(info_c, "states")):
        m = re.match(
            r"\s*(SPR_\w+)\s*,\s*(-?0x[0-9a-fA-F]+|-?\d+)"
            r"\s*,\s*(-?0x[0-9a-fA-F]+|-?\d+)\s*,"
            r"\s*\{([^}]*)\}\s*,\s*(S_\w+)",
            block,
        )
        assert m, f"bad states entry: {block[:80]!r}"
        sprite = spr.index(m.group(1))
        action = m.group(4).strip() or None
        if action == "NULL":
            action = None
        states.append((sprite, int(m.group(2), 0), int(m.group(3), 0),
                       s_index[m.group(5)], action))
    assert len(states) == len(s_names), (len(states), len(s_names))

    # mobjinfo[]: flat scalars, 23 fields (see mobjinfo_t in info.h).
    mobj = []
    for block in top_level_blocks(array_body(info_c, "mobjinfo")):
        block = "\n".join(
            line.split("//")[0] for line in block.splitlines()
        )
        fields = [f.strip() for f in block.split(",")]
        fields = [f for f in fields if f]
        assert len(fields) == 23, f"mobjinfo entry has {len(fields)} fields"
        doomednum = int(fields[0])
        spawnstate = s_index[fields[1]]
        spawnhealth = int(fields[2])
        reactiontime = int(fields[5])
        radius = _parse_fixed(fields[16])
        height = _parse_fixed(fields[17])
        seestate = _state_ref(fields[3], s_index)
        meleestate = _state_ref(fields[10], s_index)
        missilestate = _state_ref(fields[11], s_index)
        painstate = _state_ref(fields[7], s_index)
        deathstate = _state_ref(fields[12], s_index)
        xdeathstate = _state_ref(fields[13], s_index)
        raisestate = _state_ref(fields[22], s_index)
        painchance = int(fields[8], 0)
        mass = _parse_fixed(fields[18])
        damage = int(fields[19], 0)
        speed = _parse_fixed(fields[15])
        total = 0
        for term in fields[21].split("|"):
            term = term.strip()
            total |= mf[term] if term in mf else int(term, 0)
        mobj.append((doomednum, spawnstate, spawnhealth, reactiontime,
                     radius, height, total, seestate, meleestate,
                     missilestate, speed, painstate, deathstate,
                     xdeathstate, raisestate, painchance, mass, damage))
    assert len(mobj) == len(mt_names), (len(mobj), len(mt_names))

    mf_names = sorted(mf)
    lines = [
        '"""Spawn/render/simulation data tables (info.h / info.c / p_mobj.h).',
        "",
        "THIS FILE IS GENERATED - do not edit by hand.",
        "Regenerate with: python tools/gen_info.py",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "__all__ = [",
        '    "FF_FRAMEMASK",',
        '    "FF_FULLBRIGHT",',
        '    "MF_FLAGS",',
        '    "MF_SHADOW",',
        '    "MF_TRANSLATION",',
        '    "MF_TRANSSHIFT",',
        '    "SPRITE_NAMES",',
        '    "SPRITE_INDEX",',
        '    "STATES",',
        '    "STATE_INDEX",',
        '    "MT_NAMES",',
        '    "MT_INDEX",',
        '    "MOBJ_TYPES",',
        '    "type_record",',
        '    "spawn_visual",',
        "]",
        "",
        "# p_pspr.h",
        "FF_FRAMEMASK = 0x7FFF",
        "FF_FULLBRIGHT = 0x8000",
        "",
        "# p_mobj.h mobjflag_t (full set, for the gamesim).",
        f"MF_FLAGS = {mf!r}",
        f"MF_SHADOW = {mf['MF_SHADOW']:#x}",
        f"MF_TRANSLATION = {mf['MF_TRANSLATION']:#x}",
        "MF_TRANSSHIFT = 26",
        "",
        f"SPRITE_NAMES = {spr_names!r}",
        "",
        "SPRITE_INDEX = {name: i for i, name in enumerate(SPRITE_NAMES)}",
        "",
        f"STATES = {states!r}",
        "  # (sprite, frame, tics, nextstate, action) per state;",
        "  # frame may carry FF_FULLBRIGHT, tics -1 means infinite,",
        "  # action is an A_* name or None.",
        "",
        f"STATE_INDEX = {s_index!r}",
        "",
        f"MT_NAMES = {[n[3:] for n in mt_names]!r}",
        "",
        "MT_INDEX = {name: i for i, name in enumerate(MT_NAMES)}",
        "",
        f"MOBJ_TYPES = {mobj!r}",
        "  # (doomednum, spawnstate, spawnhealth, reactiontime, radius,",
        "  #  height, flags, seestate, meleestate, missilestate, speed,",
        "  #  painstate, deathstate, xdeathstate, raisestate, painchance,",
        "  #  mass, damage) per mobjtype, in MT_ order.",
        "",
        "",
        "def type_record(doomednum: int) -> dict | None:",
        '    """Full spawn record for a map thing type, or None."""',
        "    for mt, rec in enumerate(MOBJ_TYPES):",
        "        if rec[0] == doomednum:",
        "            return {",
        '                "mt": mt, "spawnstate": rec[1],',
        '                "spawnhealth": rec[2], "reactiontime": rec[3],',
        '                "radius": rec[4], "height": rec[5],',
        '                "flags": rec[6], "seestate": rec[7],',
        '                "meleestate": rec[8], "missilestate": rec[9],',
        '                "speed": rec[10], "painstate": rec[11],',
        '                "deathstate": rec[12], "xdeathstate": rec[13],',
        '                "raisestate": rec[14], "painchance": rec[15],',
        '                "mass": rec[16], "damage": rec[17],',
        "            }",
        "    return None",
        "",
        "",
        "def spawn_visual(doomednum: int) -> tuple[int, int, int] | None:",
        '    """(sprite, frame, flags) a map thing spawns with, or None."""',
        "    rec = type_record(doomednum)",
        "    if rec is None:",
        "        return None",
        "    sprite, frame, _tics, _next, _act = STATES[rec['spawnstate']]",
        "    return sprite, frame, rec['flags']",
        "",
    ]
    with open(DST, "w") as f:
        f.write("\n".join(lines))
    print(f"wrote {DST} ({len(spr)} sprites, {len(states)} states, "
          f"{len(mobj)} mobjtypes)")


if __name__ == "__main__":
    sys.exit(main())
