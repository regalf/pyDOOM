"""Screen wipes (f_wipe.c): melt between two 320x200 index frames.

Only the melt is implemented (vanilla's other two wipes are a color
xform nobody misses). The new frame slides down over the old one
behind a staggered wavy front; tick() advances whole 35Hz steps and
returns each composed frame, None when done.
"""

import numpy as np

from pydoom.m_random import m_random

W, H = 320, 200
CRUISE = H // 25  # NOTE: 8 px/tic at 320x200 (f_wipe.c melt rate)


class MeltWipe:
    """F_Wipe melt on paletted index frames (palette LUT applies after)."""

    def __init__(self) -> None:
        self.old: np.ndarray | None = None
        self.new: np.ndarray | None = None
        self.frame: np.ndarray | None = None  # last composed (shown idle)
        self.y = np.zeros(W, dtype=np.int32)
        self.done = True

    def start(self, old: np.ndarray, new: np.ndarray) -> None:
        """Begin melting from old to new (both (200, 320) uint8)."""
        self.old = np.ascontiguousarray(old, dtype=np.uint8).copy()
        self.new = np.ascontiguousarray(new, dtype=np.uint8).copy()
        # NOTE: random-walk heads on the menu stream (M_Random): the
        # first sits in [-15, 0] and each next steps -1/0/+1, clamped
        # back, so the front melts as one wavy curtain (f_wipe.c).
        ys = np.zeros(W, dtype=np.int32)
        ys[0] = -(m_random() % 16)
        for i in range(1, W):
            ys[i] = ys[i - 1] + (m_random() % 3) - 1
            if ys[i] > 0:
                ys[i] = 0
            elif ys[i] == -16:
                ys[i] = -15
        self.y = ys
        self.frame = self.old.copy()
        self.done = False

    def tick(self, steps: int = 1) -> np.ndarray | None:
        """Advance steps 35Hz tics; None once the new frame covers all."""
        if self.done:
            return None
        assert self.old is not None and self.new is not None
        for _ in range(max(0, steps)):
            # NOTE: lagging heads emerge 1 px/tic; melted columns
            # accelerate (y+1 while y < 16) then cruise (f_wipe.c).
            self.y = np.where(self.y < 0, self.y + 1,
                              np.where(self.y < 16, 2 * self.y + 1,
                                       self.y + CRUISE))
            self.y = np.minimum(self.y, H)
        if bool((self.y >= H).all()):
            self.done = True
            self.frame = self.new.copy()
            return self.new.copy()
        pos = np.clip(self.y, 0, H)[None, :]
        rows = np.arange(H, dtype=np.int32)[:, None]
        # NOTE: new frame above the front, old frame sliding down
        # below it (rows r show old row r - y), like vanilla.
        old_idx = np.clip(rows - pos, 0, H - 1)
        self.frame = np.where(rows < pos, self.new,
                              self.old[old_idx, np.arange(W)[None, :]])
        return self.frame
