"""One-shot generator: extract lookup tables from tables.c into pydoom/tables.py.

The C tables are the ground truth (bit-exact values, including the exact
rounding the original generator used), so instead of recomputing them with
sin()/tan() formulas we parse tables.c verbatim.

Usage: python tools/gen_tables.py
"""

import os
import re
import sys

SRC = os.path.join(
    os.path.dirname(__file__), "..", "DOOM-master", "linuxdoom-1.10", "tables.c"
)
DST = os.path.join(os.path.dirname(__file__), "..", "pydoom", "tables.py")

TABLES = (
    ("finetangent", 4096),
    ("finesine", 10240),
    ("tantoangle", 2049),
)


def parse_tables(path: str) -> dict[str, list[int]]:
    with open(path) as f:
        src = f.read()
    out: dict[str, list[int]] = {}
    for name, expected in TABLES:
        m = re.search(
            rf"(?:int|angle_t)\s+{name}\s*\[\d+\]\s*=\s*\{{(.*?)\}};",
            src,
            re.DOTALL,
        )
        if m is None:
            raise ValueError(f"table {name} not found in {path}")
        values = [int(v) for v in re.findall(r"-?\d+", m.group(1))]
        if len(values) != expected:
            raise ValueError(
                f"table {name}: got {len(values)} values, expected {expected}"
            )
        out[name] = values
    return out


def emit(tables: dict[str, list[int]]) -> str:
    lines = [
        '"""Trigonometric lookup tables (tables.h / tables.c).',
        "",
        "THIS FILE IS GENERATED - do not edit by hand.",
        "Regenerate with: python tools/gen_tables.py",
        "",
        "Values are parsed verbatim from the original tables.c, so they are",
        "bit-exact, including whatever rounding the id generator used.",
        '"""',
        "",
        "from __future__ import annotations",
        "",
        "from pydoom.fixed import fixed_mul",
        "",
        "__all__ = [",
        '    "FINEANGLES",',
        '    "FINEMASK",',
        '    "ANGLETOFINESHIFT",',
        '    "SLOPERANGE",',
        '    "SLOPEBITS",',
        '    "DBITS",',
        '    "FINECOSINE_OFFSET",',
        '    "finesine",',
        '    "finetangent",',
        '    "tantoangle",',
        '    "finecosine",',
        '    "slope_div",',
        "]",
        "",
        "# tables.h",
        "FINEANGLES = 8192",
        "FINEMASK = FINEANGLES - 1",
        "",
        "# 0x100000000 to 0x2000",
        "ANGLETOFINESHIFT = 19",
        "",
        "SLOPERANGE = 2048",
        "SLOPEBITS = 11",
        "DBITS = 16 - SLOPEBITS  # FRACBITS - SLOPEBITS",
        "",
        "# finecosine is &finesine[FINEANGLES/4] in C (PI/2 phase shift);",
        "# here it is a function applying that offset.",
        "FINECOSINE_OFFSET = FINEANGLES // 4  # 2048",
        "",
    ]
    for name in ("finetangent", "finesine", "tantoangle"):
        values = tables[name]
        lines.append(f"{name} = (")
        for i in range(0, len(values), 8):
            lines.append(
                "    " + ", ".join(str(v) for v in values[i : i + 8]) + ","
            )
        lines.append(")")
        lines.append("")
    lines += [
        "def finecosine(i: int) -> int:",
        '    """finesine table read with the cosine phase offset."""',
        "    return finesine[FINECOSINE_OFFSET + i]",
        "",
        "",
        "def slope_div(num: int, den: int) -> int:",
        '    """Port of SlopeDiv() from tables.c (unsigned 32-bit semantics)."""',
        "    num &= 0xFFFFFFFF",
        "    den &= 0xFFFFFFFF",
        "    if den < 512:",
        "        return SLOPERANGE",
        "    # NOTE: (num << 3) wraps at 32 bits in C unsigned arithmetic.",
        "    ans = ((num << 3) & 0xFFFFFFFF) // (den >> 8)",
        "    return ans if ans <= SLOPERANGE else SLOPERANGE",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    tables = parse_tables(SRC)
    with open(DST, "w") as f:
        f.write(emit(tables))
    print(f"wrote {DST}")


if __name__ == "__main__":
    sys.exit(main())
