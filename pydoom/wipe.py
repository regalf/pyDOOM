"""Screen wipes (f_wipe.c): melt between two 320x200 index frames.

Only the melt is implemented (vanilla's other two wipes are a color
xform nobody misses). Columns slide the new frame down over the old
one at staggered speeds; tick() returns each step, None when done.
"""

import numpy as np

W, H = 320, 200


class MeltWipe:
    """F_Wipe melt on paletted index frames (palette LUT applies after)."""

    def __init__(self, seed: int = 0) -> None:
        self._rng = np.random.default_rng(seed)
        self.old: np.ndarray | None = None
        self.new: np.ndarray | None = None
        self.y = np.zeros(W, dtype=np.int32)
        self.done = True

    def start(self, old: np.ndarray, new: np.ndarray) -> None:
        """Begin melting from old to new (both (200, 320) uint8)."""
        self.old = np.ascontiguousarray(old, dtype=np.uint8).copy()
        self.new = np.ascontiguousarray(new, dtype=np.uint8).copy()
        # NOTE: staggered heads (some columns lag behind) and speeds
        # of 2-5 px/tic melt a screen in about a second at 35Hz.
        self.y = -self._rng.integers(0, 32, size=W).astype(np.int32)
        self._dy = self._rng.integers(2, 6, size=W).astype(np.int32)
        self.done = False

    def tick(self) -> np.ndarray | None:
        """Advance one step; None once the new frame fully covers."""
        if self.done:
            return None
        assert self.old is not None and self.new is not None
        self.y += self._dy
        if bool((self.y >= H).all()):
            self.done = True
            return self.new.copy()
        out = self.old.copy()
        rows, cols = np.indices((H, W))
        revealed = rows < self.y[None, :]
        out[revealed] = self.new[rows[revealed], cols[revealed]]
        return out
