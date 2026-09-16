"""Crosshair concept mod: a centered dot that swaps art while the exact
center line points at something shootable (vanilla auto-aim spread does
not count: the dot shows what you point at)."""

from pydoom.ext import Mod

# NOTE: 4x4 art on a 320x200 frame: top-left keeps it centered.
DOT = "hud/greendotted.png"
DOT_HOT = "hud/greendotted_target.png"
DOT_X, DOT_Y = 160 - 2, 100 - 2


class Crosshair(Mod):
    id = "crosshair"
    version = "0.1.0"

    def on_enable(self, api):
        self._api = api
        self._target = False
        self._screen = "level"
        api.on("aim", self._aim)
        api.on("gamestate", self._screen_changed)
        api.on("post_overlay", self._draw)

    def _aim(self, ev):
        self._target = bool(ev.target)

    def _screen_changed(self, ev):
        self._screen = ev.new

    def _draw(self, ev):
        if self._screen != "level":
            return
        art = DOT_HOT if self._target else DOT
        self._api.image(ev.fb, art, DOT_X, DOT_Y)


MOD = Crosshair()
