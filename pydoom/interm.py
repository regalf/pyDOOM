"""Animated intermission (wi_stuff.c): staged tally, map, entering.

E1 single-player flow over the WIMAP0 world map: STATS counts climb
kills, items, secrets (+2/tic with pistol ticks and explosion thumps,
1 s pauses between stages), then NEXTLOC shows taken-map splats, the
blinking YOU ARE HERE arrow and the ENTERING name for 4 s. Any key
hurries each stage (vanilla uses attack/use; menus eat every key).
"""

from pydoom.m_random import m_random

SHOWNEXTLOCDELAY = 4  # seconds on the entering screen
PAUSE_TICS = 35  # 1 s breather between tally stages

E1_PARS = (0, 30, 75, 120, 90, 165, 180, 180, 30, 165)  # g_game.c pars

# lnodes[0]: E1 world-map spots, maps 1-9 (vanilla coordinates).
LNODES = ((185, 164), (148, 143), (69, 122), (209, 102), (116, 89),
          (166, 55), (71, 56), (135, 29), (71, 24))
# epsd0animinfo: 10 ambient splat flickers, 3 frames each.
ANIM_LOCS = ((224, 104), (184, 160), (112, 136), (72, 112), (88, 96),
             (64, 48), (192, 40), (136, 16), (80, 16), (64, 24))
ANIM_PERIOD = 35 // 3  # TICRATE/3 between flickers


def level_patch(marker: str) -> str:
    """E1M1 -> WILV00 (episode-1, map-1)."""
    ep = int(marker[1]) - 1
    num = int(marker[3:]) - 1
    return f"WILV{ep}{num}"


def map_index(marker: str) -> int:
    """E1M1 -> 0 (0-based, like wbs->last/next)."""
    return int(marker[3:]) - 1


class Intermission:
    """One finished map: staged tally, then the entering screen."""

    def __init__(self, finished: str, entering: str | None,
                 kills: int, totalkills: int, items: int, totalitems: int,
                 secrets: int, totalsecrets: int, time_tics: int,
                 par_tics: int) -> None:
        self.finished = finished
        self.entering = entering
        self.targets = (self.percent(kills, totalkills),
                        self.percent(items, totalitems),
                        self.percent(secrets, totalsecrets))
        self.time_target = max(0, time_tics) // 35
        self.par_target = max(0, par_tics) // 35
        self.state = "stats"
        self.stage = 1  # NOTE: vanilla sp_state (odd = 1 s pauses)
        self.pause = PAUSE_TICS
        self.bcnt = 0
        self.shown = [-1, -1, -1]  # NOTE: counts start at -1, like vanilla
        self.shown_time = -1
        self.shown_par = -1
        self.next_count = SHOWNEXTLOCDELAY * 35
        self.skip_next = False
        # NOTE: ANIM_ALWAYS flickers stagger on the menu RNG stream.
        self.anims = [{"ctr": -1, "next": 1 + m_random() % ANIM_PERIOD}
                      for _ in ANIM_LOCS]

    # -- per-tic --

    def tick(self) -> None:
        """WI_Ticker lite: background anims plus the stage machine."""
        from pydoom import audio
        self.bcnt += 1
        for anim in self.anims:
            if self.bcnt == anim["next"]:
                anim["ctr"] += 1
                if anim["ctr"] >= 3:
                    anim["ctr"] = 0
                anim["next"] = self.bcnt + ANIM_PERIOD
        if self.state == "nextloc":
            if self.next_count > 0:
                self.next_count -= 1
            return
        if self.stage in (1, 3, 5, 7, 9):
            self.pause -= 1
            if self.pause <= 0:
                self.stage += 1
                self.pause = PAUSE_TICS
        elif self.stage in (2, 4, 6):
            idx = self.stage // 2 - 1
            self.shown[idx] += 2
            if not self.bcnt & 3:
                audio.play("pistol")
            if self.shown[idx] >= self.targets[idx]:
                self.shown[idx] = self.targets[idx]
                audio.play("barexp")
                self.stage += 1
        elif self.stage == 8:
            if not self.bcnt & 3:
                audio.play("pistol")
            self.shown_time += 3
            if self.shown_time >= self.time_target:
                self.shown_time = self.time_target
            self.shown_par += 3
            if self.shown_par >= self.par_target:
                self.shown_par = self.par_target
                if self.shown_time >= self.time_target:
                    audio.play("barexp")
                    self.stage += 1
        # NOTE: stage 10 waits for a key (keypress moves it along).

    def keypress(self) -> None:
        """Any key hurries the tally, then dismisses the entering map."""
        from pydoom import audio
        if self.state == "nextloc":
            self.skip_next = True
            return
        if self.stage < 10:
            self.shown = list(self.targets)
            self.shown_time = self.time_target
            self.shown_par = self.par_target
            audio.play("barexp")
            self.stage = 10
        else:
            audio.play("sgcock")
            self.state = "nextloc"
            self.next_count = SHOWNEXTLOCDELAY * 35
            self.skip_next = False

    def finished_tally(self) -> bool:
        """Hold over: the viewer advances (wipe into the next map)."""
        return (self.state == "nextloc"
                and (self.skip_next or self.next_count <= 0))

    def pointer_on(self) -> bool:
        """Blinking YOU ARE HERE arrow ((cnt & 31) < 20)."""
        return (self.next_count & 31) < 20

    @staticmethod
    def percent(value: int, total: int) -> int:
        """WI tally percent (all of nothing reads 100 instead of
        faulting like vanilla would on a kill-less map)."""
        if total <= 0:
            return 100
        return value * 100 // total

    # -- drawing onto the index frame --

    def draw(self, fb, menu) -> None:
        """Background map, flickers, then the state screen."""
        menu._blit("WIMAP0", fb, 0, 0)
        for j, ((x, y), anim) in enumerate(zip(ANIM_LOCS, self.anims)):
            if anim["ctr"] >= 0:
                menu._blit(f"WIA00{j}{anim['ctr']:02d}", fb, x, y)
        if self.state == "nextloc":
            self._draw_nextloc(fb, menu)
        else:
            self._draw_stats(fb, menu)

    def _centered(self, fb, menu, name: str, y: int) -> None:
        menu._blit(name, fb, (320 - menu._patch_w(name)) // 2, y)

    def _draw_stats(self, fb, menu) -> None:
        try:
            lh = 3 * menu.patch("WINUM0")[0].height // 2
        except Exception:
            lh = 16
        self._centered(fb, menu, level_patch(self.finished), 2)
        self._centered(fb, menu, "WIF", 24)
        for i, patch in enumerate(("WIOSTK", "WIOSTI", "WIOSTS")):
            y = 50 + i * lh
            menu._blit(patch, fb, 50, y)
            self._percent(fb, menu, 270, y, self.shown[i])
        menu._blit("WITIME", fb, 16, 168)
        self._time(fb, menu, self.shown_time, 144, 168)
        menu._blit("WIPAR", fb, 176, 168)
        self._time(fb, menu, self.shown_par, 304, 168)

    def _draw_nextloc(self, fb, menu) -> None:
        last = map_index(self.finished)
        nxt = map_index(self.entering) if self.entering else 8
        if last == 8:
            last = nxt - 1
        for i in range(last + 1):
            self._on_lnode(fb, menu, i, ["WISPLAT"])
        if self.finished == "E1M3" and self.entering == "E1M9":
            self._on_lnode(fb, menu, 8, ["WISPLAT"])  # NOTE: secret pad
        if self.pointer_on():
            self._on_lnode(fb, menu, nxt, ["WIURH0", "WIURH1"])
        self._centered(fb, menu, "WIENTER", 2)
        if self.entering is not None:
            self._centered(fb, menu, level_patch(self.entering), 26)

    def _on_lnode(self, fb, menu, node: int, names: list) -> None:
        """WI_drawOnLnode: first fitting frame wins (else skipped)."""
        x, y = LNODES[node]
        for name in names:
            try:
                patch, _, _ = menu.patch(name)
            except Exception:
                continue
            left = x - patch.leftoffset
            top = y - patch.topoffset
            if (left >= 0 and left + patch.width < 320
                    and top >= 0 and top + patch.height < 200):
                menu._blit(name, fb, x, y)
                return

    def _percent(self, fb, menu, x_right: int, y: int, value: int) -> None:
        if value < 0:
            return
        menu._blit("WIPCNT", fb, x_right, y)
        self._number(fb, menu, value, x_right, y)

    def _number(self, fb, menu, value: int, x_right: int, y: int) -> None:
        for digit in reversed(str(max(0, value))):
            x_right -= 12  # NOTE: WINUM glyphs are 12px stepping
            menu._blit(f"WINUM{digit}", fb, x_right, y)

    def _time(self, fb, menu, tics: int, x_right: int, y: int) -> None:
        """WI_drawTime groups (m:ss, h:mm:ss) plus the sucks patch."""
        if tics < 0:
            return
        if tics > 61 * 59:
            menu._blit("WISUCKS", fb, x_right - 60, y)
            return
        div = 1
        while True:
            group = (tics // div) % 60
            tens, ones = group // 10, group % 10
            x_right -= 12
            menu._blit(f"WINUM{ones}", fb, x_right, y)
            x_right -= 12
            menu._blit(f"WINUM{tens}", fb, x_right, y)
            div *= 60
            if not tics // div:
                break
            x_right -= 8
            menu._blit("WICOLON", fb, x_right, y)
