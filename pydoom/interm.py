"""Level-end tally (wi_stuff.c lite): counts, time, par, next map.

Shareware has no INTERPIC, so the tally draws on black with the WI*
patches (level names, digits, %, colon) plus STCFN text. Numbers count
up with a pistol tick, then hold; any key fast-forwards, like vanilla.
"""

COUNT_TICS = 70  # count-up sweep
HOLD_TICS = 140  # readable pause before the next map

E1_PARS = (0, 30, 75, 120, 90, 165, 180, 180, 30, 165)  # g_game.c pars


def level_patch(marker: str) -> str:
    """E1M1 -> WILV00 (episode-1, map-1)."""
    ep = int(marker[1]) - 1
    num = int(marker[3:]) - 1
    return f"WILV{ep}{num}"


class Intermission:
    """One finished map: tally targets, count-up clock, skip logic."""

    def __init__(self, finished: str, entering: str | None,
                 kills: int, totalkills: int, items: int, totalitems: int,
                 secrets: int, totalsecrets: int, time_tics: int,
                 par_tics: int) -> None:
        self.finished = finished
        self.entering = entering
        self.targets = (kills, items, secrets)
        self.totals = (totalkills, totalitems, totalsecrets)
        self.time_tics = time_tics
        self.par_tics = par_tics
        self.tic = 0

    def tick(self) -> None:
        """WI_Ticker lite: advance the count-up sweep."""
        from pydoom import audio
        if self.tic < COUNT_TICS and self.tic % 4 == 0:
            audio.play("pistol")
        self.tic += 1

    def keypress(self) -> None:
        """Any key hurries the tally (vanilla skips the sweep first)."""
        if self.tic < COUNT_TICS:
            self.tic = COUNT_TICS
        else:
            self.tic = COUNT_TICS + HOLD_TICS

    def finished_tally(self) -> bool:
        """Hold over: the viewer advances (wipe into the next map)."""
        return self.tic >= COUNT_TICS + HOLD_TICS

    def shown(self) -> tuple[int, int, int]:
        """Counted-up values for this tic (clamped at the targets)."""
        k = min(1.0, self.tic / COUNT_TICS) if COUNT_TICS else 1.0
        return tuple(min(t, int(t * k)) for t in self.targets)

    def percent(self, value: int, total: int) -> int:
        """WI_DrawPercent: share of total (all of nothing reads 100)."""
        if total <= 0:
            return 100
        return value * 100 // total

    def draw(self, fb, menu) -> None:
        """Full tally screen onto the index frame."""
        shown = self.shown()
        menu._blit(level_patch(self.finished), fb, 100, 8)
        menu._text_block(fb, "FINISHED", 44)
        labels = ("KILLS", "ITEMS", "SECRET")
        y = 70
        for label, val, total in zip(labels, shown, self.totals):
            menu._text_block(fb, label, y, 40)
            pct = self.percent(val, total)
            self._number(fb, menu, val, 200, y)
            self._number(fb, menu, pct, 268, y)
            menu._blit("WIPCNT", fb, 272, y)
            y += 20
        menu._text_block(fb, "TIME", y, 40)
        self._time(fb, menu, self.time_tics, 200, y)
        y += 20
        menu._text_block(fb, "PAR", y, 40)
        self._time(fb, menu, self.par_tics, 200, y)
        if self.entering is not None:
            menu._blit("WIENTER", fb, 60, 160)
            menu._blit(level_patch(self.entering), fb, 170, 158)

    def _number(self, fb, menu, value: int, x_right: int, y: int) -> None:
        for digit in reversed(str(max(0, value))):
            x_right -= 12  # NOTE: WINUM glyphs are 12px stepping
            menu._blit(f"WINUM{digit}", fb, x_right, y)

    def _time(self, fb, menu, tics: int, x: int, y: int) -> None:
        secs = max(0, tics) // 35
        self._number(fb, menu, secs // 60, x + 24, y)
        menu._blit("WICOLON", fb, x + 26, y)
        tens = (secs % 60) // 10
        ones = (secs % 60) % 10
        menu._blit(f"WINUM{tens}", fb, x + 34, y)
        menu._blit(f"WINUM{ones}", fb, x + 46, y)
