"""Frame profiler for glrenderer2 (P0 profiling slice).

CPU scopes only (GL timer queries come later, if the CPU scopes show
the driver thread as the bottleneck): named nested-tolerant timers
aggregated per frame, plus a text table for --profile runs. Pure
Python, no GL imports, fully unit tested.
"""

from __future__ import annotations

import time
from contextlib import contextmanager


class Profiler:
    """Collect ms per named scope; report() renders the table."""

    def __init__(self) -> None:
        self.total: dict = {}  # name -> [ms, calls]
        self.counters: dict = {}  # name -> count (stalls, fences, ...)
        self.frames = 0

    @contextmanager
    def scope(self, name: str):
        """Time one pass (scopes may nest; inner time counts twice,
        like gprof: keep scopes sibling-level per pass)."""
        t0 = time.perf_counter()
        try:
            yield
        finally:
            dt = (time.perf_counter() - t0) * 1000.0
            cell = self.total.get(name)
            if cell is None:
                self.total[name] = [dt, 1]
            else:
                cell[0] += dt
                cell[1] += 1

    def frame(self) -> None:
        """Close one presented frame (drives per-frame averages)."""
        self.frames += 1

    def count(self, name: str) -> None:
        """Bump a counter (fence stalls, etc.)."""
        self.counters[name] = self.counters.get(name, 0) + 1

    def reset(self) -> None:
        """Drop all samples (benchmark reruns, map switches)."""
        self.total.clear()
        self.counters.clear()
        self.frames = 0

    def report(self, width: int = 44) -> str:
        """Table sorted by total ms desc; empty profiler one-liner."""
        if not self.total or not self.frames:
            return "profile: no samples"
        rows = sorted(self.total.items(), key=lambda kv: -kv[1][0])
        head = f"profile: {self.frames} frames"
        lines = [head, f"{'pass':<{width}} {'total ms':>9} "
                 f"{'ms/frame':>9} {'calls':>7}"]
        for name, (ms, calls) in rows:
            lines.append(f"{name:<{width}} {ms:9.2f} "
                         f"{ms / self.frames:9.3f} {calls:7d}")
        for name in sorted(self.counters):
            lines.append(f"{name:<{width}} {self.counters[name]:9d} "
                         f"{'total':>9} {'':>7}")
        return "\n".join(lines)
