"""no_mouse_forward: unplug mouse Y from forwardmove.

Vanilla feeds mousey into forwardmove (ticcmd): here Y is consumed so the
mouse only turns. Sim-safe: it zeroes an input, never changes the sim for
equal inputs (same as never touching the mouse vertically).
"""

from pydoom.ext import Mod


class NoMouseForward(Mod):
    id = "no_mouse_forward"
    version = "0.1.0"
    description = "Mouse no longer walks forward/back"

    def on_enable(self, api):
        api.on("mouse_motion", self._cut, priority=0)

    def _cut(self, ev):
        ev.consume_y = True


MOD = NoMouseForward()
