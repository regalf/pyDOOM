"""mouselook: look up/down with the mouse (OpenGL only).

The camera that looks in all four directions: X turns the body (engine),
Y pitches the look. The eye stays glued to the body height at all times
(viewz untouched): stairs, lifts and gravity always behave like vanilla.
Depends on no_mouse_forward (without it Y would still drive the stride).
Refused on the software backend (no pitch in the column renderer).
"""

from pydoom.ext import Mod

# NOTE: fallback when an older engine omits sens_rad_per_px from
# mouse_motion (matches the default slider notch).
_PITCH_SENS_FALLBACK = 0.0028
_PITCH_MAX = 1.2  # look clamp (radians)


class Mouselook(Mod):
    id = "mouselook"
    version = "0.1.0"
    depends = ("no_mouse_forward",)
    backend = "opengl"
    description = "Look up/down with the mouse"

    def __init__(self):
        self._dy = 0
        self._sens = _PITCH_SENS_FALLBACK

    def on_enable(self, api):
        self._dy = 0
        self._sens = _PITCH_SENS_FALLBACK
        api.on("mouse_motion", self._grab, priority=100)
        api.on("camera", self._look, priority=0)
        api.on("settings", self._cfg, priority=0)

    def _grab(self, ev):
        # NOTE: runs before no_mouse_forward (higher priority): reads dy
        # and lets the other mod consume it for forwardmove.
        self._dy += ev.dy
        if getattr(ev, "sens_rad_per_px", None) is not None:
            self._sens = ev.sens_rad_per_px

    def _cfg(self, ev):
        # NOTE: slider edits land here (change-detected by the viewer).
        self._sens = ev.mouse_rad_per_px

    def _look(self, ev):
        # NOTE: pitch only, viewz never touched: the eye rides the body.
        if self._dy:
            ev.pitch = max(-_PITCH_MAX, min(_PITCH_MAX,
                                            ev.pitch + self._dy * self._sens))
            self._dy = 0
        ev.handled = True


MOD = Mouselook()
