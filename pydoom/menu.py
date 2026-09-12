"""In-game menus (m_menu.c): defs, skull cursor, STCFN text, M_* art.

Everything draws onto the 320x200 index frame before the palette LUT,
so menu art uses PLAYPAL like the game scene. Load/Save entries show
a placeholder until the savegame milestone lands; detail/screensize
are omitted (fixed renderer); End Game waits for a title state.
"""

from dataclasses import dataclass, field

from pydoom.textures import decode_patch

LINEHEIGHT = 16
SKULL_XOFF = -32
SKULL_YOFF = -5

SKILLS = ("baby", "easy", "normal", "hard", "nightmare")

QUITMSG = "are you sure you want to\nquit this great game?"
ENDGAME = "are you sure you want to\nend the game?"
SWSTRING = ("this is the shareware version of doom.\n\n"
            "you need to order the entire trilogy.")
NIGHTMARE = ("are you sure? this skill level\n"
             "isn't even remotely fair.")
NOTYET = "not yet implemented"

# value ranges behind the thermo bars (m_menu.c clamps).
SFX_MAX, MUS_MAX, SENS_MAX = 15, 15, 8


@dataclass
class Settings:
    """Viewer-owned values the menu mutates (volumes, sens, messages)."""

    sfx_vol: int = 8
    mus_vol: int = 8
    mouse_sens: int = 4
    messages: bool = True
    # NOTE: vanilla demo compat (.lmp playback/record, attract loop)
    # is experimental and OFF by default (needs more development for
    # full demo parity); set `demos 1` in pydoom.cfg to enable it.
    demos: bool = False


CONFIG_PATH = "pydoom.cfg"


def settings_load(path: str, settings: Settings) -> None:
    """Read back an options file (vanilla default.cfg idea, tolerant:
    bad lines and out-of-range values never crash the boot)."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return
    for line in lines:
        parts = line.split()
        if len(parts) != 2:
            continue
        key, raw = parts
        try:
            val = int(raw)
        except ValueError:
            continue
        if key == "sfx_vol":
            settings.sfx_vol = max(0, min(SFX_MAX, val))
        elif key == "mus_vol":
            settings.mus_vol = max(0, min(MUS_MAX, val))
        elif key == "mouse_sens":
            settings.mouse_sens = max(0, min(SENS_MAX, val))
        elif key == "messages":
            settings.messages = bool(val)
        elif key == "demos":
            settings.demos = bool(val)


def settings_save(path: str, settings: Settings) -> None:
    """Persist options (written on real exits only, never by --frames
    smoke runs, so headless testing stays side-effect free)."""
    try:
        with open(path, "w") as f:
            f.write(f"sfx_vol {settings.sfx_vol}\n"
                    f"mus_vol {settings.mus_vol}\n"
                    f"mouse_sens {settings.mouse_sens}\n"
                    f"messages {int(settings.messages)}\n"
                    "# demos 1 enables vanilla demo compat (.lmp "
                    "playback/record, attract loop); experimental, "
                    "off by default, needs more development\n"
                    f"demos {int(settings.demos)}\n")
    except OSError:
        pass


@dataclass
class MenuItem:
    """One row: action id, patch, slider/toggle wiring, shortcut key."""

    action: str
    patch: str | None = None
    kind: str = "action"  # action | slider | toggle | gap
    shortcut: str = ""


@dataclass
class MenuDef:
    """menu_t: title art, rows, origin, previous menu, opener selection."""

    name: str
    title: str | None
    items: list
    x: int
    y: int
    prev: str | None
    last_on: int = 0


def build_menus() -> dict:
    """The vanilla tree, minus save/load behavior and title-dependent
    End Game (see module docstring)."""
    return {
        "main": MenuDef("main", None, [
            MenuItem("episode", "M_NGAME", shortcut="n"),
            MenuItem("options", "M_OPTION", shortcut="o"),
            MenuItem("load", "M_LOADG", shortcut="l"),
            MenuItem("save", "M_SAVEG", shortcut="s"),
            MenuItem("readthis", "M_RDTHIS", shortcut="r"),
            MenuItem("quit", "M_QUITG", shortcut="q"),
        ], 97, 64, None),
        "episode": MenuDef("episode", "M_EPISOD", [
            MenuItem("ep0", "M_EPI1", shortcut="k"),
            MenuItem("ep1", "M_EPI2", shortcut="t"),
            MenuItem("ep2", "M_EPI3", shortcut="i"),
        ], 48, 63, "main", 0),
        "skill": MenuDef("skill", "M_SKILL", [
            MenuItem("skill0", "M_JKILL", shortcut="i"),
            MenuItem("skill1", "M_ROUGH", shortcut="h"),
            MenuItem("skill2", "M_HURT", shortcut="h"),
            MenuItem("skill3", "M_ULTRA", shortcut="u"),
            MenuItem("skill4", "M_NMARE", shortcut="n"),
        ], 48, 63, "episode", 2),
        "options": MenuDef("options", "M_OPTTTL", [
            MenuItem("endgame", "M_ENDGAM", shortcut="e"),
            MenuItem("messages", "M_MESSG", "toggle", "m"),
            MenuItem("sens", "M_MSENS", "slider", "m"),
            MenuItem("sound", "M_SVOL", shortcut="s"),
        ], 60, 37, "main", 0),
        "sound": MenuDef("sound", None, [
            MenuItem("sfx", "M_SFXVOL", "slider", "s"),
            MenuItem("mus", "M_MUSVOL", "slider", "m"),
        ], 80, 64, "options", 0),
    }


class Menu:
    """M_Responder/M_Ticker/M_Drawer: key() returns viewer events."""

    def __init__(self, wad, settings: Settings,
                 skill_index: int = 2, max_episode: int = 0) -> None:
        self.wad = wad
        self.settings = settings
        # NOTE: highest selectable episode (0 shareware, 2 registered,
        # 3 retail); beyond it vanilla scolds and shows Read This!
        self.max_episode = max_episode
        self.menus = build_menus()
        self.menus["skill"].last_on = max(
            0, min(4, skill_index))  # NOTE: CLI --skill preselects
        self.current = "main"
        self.mode = "menu"  # menu|confirm|message|readthis|slots|savename
        self.slot_kind = "load"  # slots mode picks load/save behavior
        self.slot_idx = 0
        self.slot_names: list = []
        self.name_buf = ""
        self.confirm_text = ""
        self.confirm_yes = None  # "quit" or ("new_game", ep, skill)
        self.message_text = ""
        self.message_then = "back"  # or "readthis" (shareware episode)
        self.readpage = 0
        self.episode = 0
        self.skull_tic = 0
        self.which_skull = 0
        self._patches: dict = {}
        self._font: dict = {}

    # -- data helpers --

    def patch(self, name: str):
        """Decoded patch plus numpy pixels/mask (statusbar-style)."""
        import numpy as np
        hit = self._patches.get(name)
        if hit is None:
            p = decode_patch(self.wad.read_lump(name))
            cols = [p.column_pixels(sx) for sx in range(p.width)]
            mat = np.stack([np.frombuffer(c[0], dtype=np.uint8)
                            for c in cols], axis=1)
            msk = np.stack([np.frombuffer(c[1], dtype=np.uint8)
                            for c in cols], axis=1)
            hit = (p, mat, msk)
            self._patches[name] = hit
        return hit

    def glyph(self, ch: str):
        """hu_font: STCFN033..STCFN096, uppercase only like vanilla."""
        ch = ch.upper()
        if ch not in self._font:
            code = ord(ch)
            if not 33 <= code <= 96:
                return None  # NOTE: no lowercase/glyph, skip the char
            self._font[ch] = self.patch(f"STCFN{code:03d}")
        return self._font[ch]

    # -- per-tic --

    def open(self) -> None:
        """M_StartControlPanel: always lands on Main (lastOn kept)."""
        self.current = "main"
        self.mode = "menu"

    def open_readthis(self) -> None:
        """F1 help: straight to the Read This! screens."""
        self.current = "main"
        self.mode = "readthis"
        self.readpage = 0

    def enter_slots(self, kind: str) -> None:
        """Jump straight to the load/save slots (quicksave needs one)."""
        from pydoom import saveg
        self.slot_kind = kind
        self.slot_idx = 0
        self.slot_names = [saveg.slot_name(i)
                           for i in range(saveg.SLOT_COUNT)]
        self.mode = "slots"

    def tick(self) -> None:
        """Skull animation (whichSkull flips every 8 tics)."""
        self.skull_tic += 1
        if self.skull_tic >= 8:
            self.skull_tic = 0
            self.which_skull ^= 1

    # -- input: returns [(event, ...)] for the viewer --

    def key(self, k: str) -> list:
        """Feed one key: arrows/enter/esc, shortcuts, y/n, or any."""
        from pydoom import audio
        if self.mode == "slots":
            if k == "up":
                self.slot_idx = (self.slot_idx - 1) % 6
                audio.play("pstop")
            elif k == "down":
                self.slot_idx = (self.slot_idx + 1) % 6
                audio.play("pstop")
            elif k == "enter":
                audio.play("swtchn")
                if self.slot_kind == "load":
                    if self.slot_names[self.slot_idx] == "EMPTY":
                        audio.play("oof")
                    else:
                        self.mode = "menu"
                        return [("load_game", self.slot_idx)]
                else:
                    self.mode = "savename"
                    self.name_buf = ""
            elif k == "esc":
                self.mode = "menu"
            return []
        if self.mode == "savename":
            if k == "enter":
                audio.play("swtchn")
                self.mode = "menu"
                return [("save_game", self.slot_idx,
                          self.name_buf.strip() or "UNTITLED")]
            if k == "esc":
                self.mode = "slots"
            elif k == "backspace":
                self.name_buf = self.name_buf[:-1]
            elif len(k) == 1 and 33 <= ord(k) <= 126 and len(
                    self.name_buf) < 24:
                self.name_buf += k.upper()
            return []
        if self.mode == "readthis":
            audio.play("swtchn")
            if self.readpage == 0:
                self.readpage = 1
            else:
                self.mode = "menu"
                return ["close"]
            return []
        if self.mode == "message":
            audio.play("swtchn")
            if self.message_then == "readthis":
                self.mode = "readthis"
                self.readpage = 0
            else:
                self.mode = "menu"
            return []
        if self.mode == "confirm":
            if k == "y":
                audio.play("swtchn")
                self.mode = "menu"
                return [self.confirm_yes] if self.confirm_yes else []
            if k in ("n", "esc"):
                self.mode = "menu"
            return []
        mdef = self.menus[self.current]
        if k == "up":
            self._move(-1)
        elif k == "down":
            self._move(1)
        elif k in ("left", "right"):
            self._adjust(mdef, 1 if k == "right" else -1)
        elif k == "enter":
            return self._activate(mdef)
        elif k == "esc":
            audio.play("swtchn")
            if mdef.prev is None:
                return ["close"]
            self.current = mdef.prev
        elif len(k) == 1:
            for i, item in enumerate(mdef.items):
                if item.kind != "gap" and item.shortcut == k:
                    mdef.last_on = i
                    return self._activate(mdef)
        return []

    def _move(self, delta: int) -> None:
        from pydoom import audio
        mdef = self.menus[self.current]
        n = len(mdef.items)
        i = mdef.last_on
        for _ in range(n):
            i = (i + delta) % n
            if mdef.items[i].kind != "gap":
                break
        mdef.last_on = i
        audio.play("pstop")

    def _adjust(self, mdef, delta: int) -> None:
        item = mdef.items[mdef.last_on]
        if item.kind == "slider":
            self.slider_adjust(item.action, delta)

    def _activate(self, mdef) -> list:
        from pydoom import audio
        item = mdef.items[mdef.last_on]
        audio.play("swtchn")
        act = item.action
        if act == "episode":
            self.current = "episode"
        elif act == "options":
            self.current = "options"
        elif act in ("load", "save"):
            from pydoom import saveg
            self.slot_kind = act
            self.slot_idx = 0
            self.slot_names = [saveg.slot_name(i)
                               for i in range(saveg.SLOT_COUNT)]
            self.mode = "slots"
        elif act == "readthis":
            self.mode = "readthis"
            self.readpage = 0
        elif act == "quit":
            self._ask(QUITMSG, "quit")
        elif act == "endgame":
            self._ask(ENDGAME, "endgame")
        elif act.startswith("ep"):
            ep = int(act[2:])
            if ep > self.max_episode:  # NOTE: scolds, shows Read This!
                audio.play("oof")
                self._say(SWSTRING, then="readthis")
            else:
                self.episode = ep
                self.current = "skill"
        elif act.startswith("skill"):
            idx = int(act[5:])
            if idx == 4:
                self._ask(NIGHTMARE, ("new_game", self.episode,
                                      SKILLS[idx]))
            else:
                self.mode = "menu"
                return [("new_game", self.episode, SKILLS[idx])]
        elif act == "messages":
            self.settings.messages = not self.settings.messages
        elif act == "sound":
            self.current = "sound"
        return []

    def _ask(self, text: str, on_yes) -> None:
        self.mode = "confirm"
        self.confirm_text = text
        self.confirm_yes = on_yes

    def _say(self, text: str, then: str = "back") -> None:
        self.mode = "message"
        self.message_text = text
        self.message_then = then

    # -- drawing onto the index frame --

    def draw_title(self, fb) -> None:
        """TITLESCREEN: fullscreen TITLEPIC art (music stays stubbed)."""
        fb[:] = 0
        self._blit("TITLEPIC", fb, 0, 0)

    def draw(self, fb) -> None:
        """Current menu state over the (frozen) game scene."""
        if self.mode == "readthis":
            self._blit("HELP1" if self.readpage == 0 else "HELP2", fb,
                       0, 0)
            return
        if self.mode == "slots":
            self._blit("M_SGTTL" if self.slot_kind == "save"
                       else "M_LGTTL", fb, 80, 20)
            y = 60
            for i, name in enumerate(self.slot_names):
                self._text_block(fb, name, y, 80)
                if i == self.slot_idx:
                    skull = ("M_SKULL1" if self.which_skull == 0
                             else "M_SKULL2")
                    self._blit(skull, fb, 80 + SKULL_XOFF, y + SKULL_YOFF)
                y += LINEHEIGHT
            return
        if self.mode == "savename":
            self._blit("M_SGTTL", fb, 80, 20)
            self._text_block(fb, self.name_buf + "_", 100, 80)
            return
        if self.mode in ("confirm", "message"):
            text = (self.confirm_text if self.mode == "confirm"
                    else self.message_text)
            self._text_block(fb, text, 100)
            return
        mdef = self.menus[self.current]
        if self.current == "main":
            self._blit("M_DOOM", fb, 94, 2)
        if mdef.title is not None:
            self._blit(mdef.title, fb, mdef.x, mdef.y - 25)
        y = mdef.y
        for i, item in enumerate(mdef.items):
            if item.kind == "gap":
                y += LINEHEIGHT
                continue
            if item.patch is not None:
                self._blit(item.patch, fb, mdef.x, y)
            if item.kind == "toggle" and item.action == "messages":
                self._blit("M_MSGON" if self.settings.messages
                           else "M_MSGOFF", fb, mdef.x + 175, y)
            if item.kind == "slider":
                val, top = self._slider_value(item.action)
                self._thermo(fb, mdef.x + self._patch_w(item.patch) + 8,
                             y + 2, val, top)
            if i == mdef.last_on:
                skull = "M_SKULL1" if self.which_skull == 0 else "M_SKULL2"
                self._blit(skull, fb, mdef.x + SKULL_XOFF,
                           y + SKULL_YOFF)
            y += LINEHEIGHT

    def _slider_value(self, action: str) -> tuple:
        s = self.settings
        if action == "sens":
            return s.mouse_sens, SENS_MAX
        if action == "sfx":
            return s.sfx_vol, SFX_MAX
        return s.mus_vol, MUS_MAX

    def slider_adjust(self, action: str, delta: int) -> None:
        """Left/right on a slider row (M_ChangeSensitivity/vol)."""
        from pydoom import audio
        s = self.settings
        if action == "sens":
            s.mouse_sens = max(0, min(SENS_MAX, s.mouse_sens + delta))
        elif action == "sfx":
            s.sfx_vol = max(0, min(SFX_MAX, s.sfx_vol + delta))
        elif action == "mus":
            s.mus_vol = max(0, min(MUS_MAX, s.mus_vol + delta))
        else:
            return
        audio.play("stnmov")

    def _thermo(self, fb, x: int, y: int, val: int, top: int,
                slots: int = 10) -> None:
        """M_DrawThermo: caps plus lit/unlit boxes (layout simplified:
        the bar sits right of the label, not below it)."""
        fill = round(val / top * slots) if top else 0
        cx = x + self._blit("M_THERML", fb, x, y)
        for i in range(slots):
            cx += self._blit("M_THERMO" if i < fill else "M_THERMM",
                             fb, cx, y)
        self._blit("M_THERMR", fb, cx, y)

    def _text_block(self, fb, text: str, y: int, x: int | None = None) -> None:
        for line in text.split("\n"):
            w = sum(self._glyph_w(ch) for ch in line)
            cx = (320 - w) // 2 if x is None else x
            for ch in line:
                cx += self._draw_glyph(fb, ch, cx, y)
            y += 12

    def _glyph_w(self, ch: str) -> int:
        g = self.glyph(ch)
        return (g[0].width + 1) if g is not None else 4

    def _draw_glyph(self, fb, ch: str, x: int, y: int) -> int:
        if ch == " ":
            return 4
        g = self.glyph(ch)
        if g is None:
            return 4
        patch, mat, msk = g
        h, w = patch.height, patch.width
        ox, oy = x - patch.leftoffset, y - patch.topoffset
        x0, y0 = max(ox, 0), max(oy, 0)
        x1, y1 = min(ox + w, 320), min(oy + h, 200)
        if x0 >= x1 or y0 >= y1:
            return w + 1
        region = msk[y0 - oy:y1 - oy, x0 - ox:x1 - ox].astype(bool)
        sub = fb[y0:y1, x0:x1]
        sub[region] = mat[y0 - oy:y1 - oy, x0 - ox:x1 - ox][region]
        return w + 1

    def _patch_w(self, name: str | None) -> int:
        if name is None:
            return 0
        try:
            return self.patch(name)[0].width
        except Exception:
            return 0

    def _blit(self, name: str, fb, x: int, y: int) -> int:
        """V_DrawPatchDirect 1:1, clipped (missing lumps are silent)."""
        try:
            patch, mat, msk = self.patch(name)
        except Exception:
            return 0
        h, w = patch.height, patch.width
        ox, oy = x - patch.leftoffset, y - patch.topoffset
        x0, y0 = max(ox, 0), max(oy, 0)
        x1, y1 = min(ox + w, 320), min(oy + h, 200)
        if x0 >= x1 or y0 >= y1:
            return w
        region = msk[y0 - oy:y1 - oy, x0 - ox:x1 - ox].astype(bool)
        sub = fb[y0:y1, x0:x1]
        sub[region] = mat[y0 - oy:y1 - oy, x0 - ox:x1 - ox][region]
        return w
