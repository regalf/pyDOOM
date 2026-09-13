"""Build tag for the HUD and launcher (screenshots name their code).

Single editable source: the VERSION file at the repo root (bump it
for releases). Dev checkouts without one fall back to the git short
hash; frozen bundles and bare trees report FALLBACK.
"""

from __future__ import annotations

import os
import subprocess

FALLBACK = "nogit"


def get_version(root: str | None = None) -> str:
    """Release tag from VERSION, else git short HEAD, else FALLBACK."""
    if root is None:
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        with open(os.path.join(root, "VERSION"), encoding="utf-8") as f:
            tag = f.read().strip()
            if tag:
                return tag
    except OSError:
        pass
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, cwd=root, timeout=5).stdout.strip()
        if out:
            return out
    except Exception:
        pass
    return FALLBACK
