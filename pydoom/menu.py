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

# NOTE: renderer backend (milestone H): software is the reference raster,
# opengl is the native-res GL port (auto-falls back to software when the
# GL context cannot come up: missing PyOpenGL, dummy video, headless).
VIDEO_APIS = ("software", "openglv1", "openglv2")

# NOTE: graphics settings (Options -> Video, live-applied, cfg-persisted).
# GL renders natively at the window size (16:10 steps like 320x200);
# software always renders 320x200 and only scales the window.
GL_RESOLUTIONS = ("640x400", "960x600", "1280x800", "1600x1000",
                  "1920x1200")
# NOTE: 0 means uncapped (clock.tick(0)); the 35Hz sim accumulator is
# frame-rate independent, so high fps never speeds up the game.
FPS_LIMITS = (30, 60, 120, 144, 180, 240, 0)
# NOTE: exclusive fullscreen needs a real mode switch (Windows only);
# everywhere else it is hidden and borderless covers fullscreen duty.
import sys as _sys
DISPLAY_MODES = ("windowed", "fullscreen", "borderless") \
    if _sys.platform == "win32" else ("windowed", "borderless")
SW_SCALES = (1, 2, 3)  # window magnification over 320x200
SW_BASE_W, SW_BASE_H = 320, 200


def parse_resolution(raw: str) -> tuple | None:
    """'960x600' -> (960, 600), else None (pure, test-covered)."""
    try:
        w, h = raw.lower().split("x")
        w, h = int(w), int(h)
    except (ValueError, AttributeError):
        return None
    if w <= 0 or h <= 0 or w > 7680 or h > 4320:
        return None
    return w, h


def sw_window_size(scale: int) -> tuple:
    """Software window for a magnification step (pure, test-covered)."""
    s = scale if scale in SW_SCALES else 3
    return SW_BASE_W * s, SW_BASE_H * s


def letterbox(dst_w: int, dst_h: int) -> tuple:
    """Integer-scale 320x200 fit centered in dst (pure, test-covered).

    Returns (w, h, x, y): fullscreen/borderless keep chunky pixels
    with black bars instead of stretching; windowed sizes match
    exactly so the offset is (0, 0)."""
    s = max(1, min(dst_w // SW_BASE_W, dst_h // SW_BASE_H))
    w, h = SW_BASE_W * s, SW_BASE_H * s
    return w, h, (dst_w - w) // 2, (dst_h - h) // 2


def display_count() -> int:
    """Detected screens, guarded (headless-safe 1; pygame-ce only)."""
    try:
        import pygame
        sizes = pygame.display.get_desktop_sizes()
        if sizes:
            return max(1, len(sizes))
    except Exception:  # noqa: BLE001 - dummy video/headless
        pass
    return 1


def display_origins() -> list:
    """Best-effort (x, y) origin per screen (pure layout guess).

    pygame-ce exposes sizes but not bounds, so origins assume a
    left-to-right strip (correct for the common layout; otherwise the
    window still opens, possibly on the wrong screen — SDL ultimately
    decides via the display index)."""
    try:
        import pygame
        sizes = pygame.display.get_desktop_sizes()
    except Exception:  # noqa: BLE001 - dummy video/headless
        sizes = None
    if not sizes:
        return [(0, 0)]
    out, x = [], 0
    for w, _h in sizes:
        out.append((x, 0))
        x += int(w)
    return out


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
    # NOTE: launcher prefs (pyDOOM.py writes these on Launch).
    last_wad: str = "DOOM1.WAD"
    last_skill: str = "normal"
    last_map: str = "E1M1"
    # NOTE: renderer backend, see VIDEO_APIS (default software).
    video_api: str = "software"
    # NOTE: graphics settings (Options -> Video, live-applied): GL
    # renders natively at gl_resolution; software always renders
    # 320x200 and sw_scale only magnifies the window. fps_limit 0 is
    # uncapped; vsync is real on GL, best-effort on software.
    gl_resolution: str = "960x600"
    fps_limit: int = 60
    vsync: bool = False
    display_mode: str = "windowed"
    display_index: int = 0  # NOTE: fullscreen/borderless target screen
    sw_scale: int = 3
    show_fps: bool = False
    # NOTE: extension mods (launcher MODS tab owns these; the in-game
    # Extension menu only flips the live session). mods_on/mods_off are
    # user deviations from each manifest's enabled_default.
    mods_enabled: bool = True
    mods_on: set = field(default_factory=set)
    mods_off: set = field(default_factory=set)


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
        if key in ("last_wad", "last_skill", "last_map"):
            # NOTE: launcher strings (validated at launch, not here).
            setattr(settings, key, raw[:32])
            continue
        if key == "video_api":
            # NOTE: string-validated like the launcher prefs above;
            # legacy "opengl" means v1 (pre-v2 cfgs keep working).
            if raw == "opengl":
                raw = "openglv1"
            if raw in VIDEO_APIS:
                settings.video_api = raw
            continue
        if key == "gl_resolution":
            if parse_resolution(raw) is not None:
                settings.gl_resolution = raw.lower()
            continue
        if key == "display_mode":
            if raw in DISPLAY_MODES:
                settings.display_mode = raw
            continue
        if key == "mod_on":
            settings.mods_on.add(raw[:64])
            settings.mods_off.discard(raw[:64])
            continue
        if key == "mod_off":
            settings.mods_off.add(raw[:64])
            settings.mods_on.discard(raw[:64])
            continue
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
        elif key == "fps_limit":
            settings.fps_limit = val if val in FPS_LIMITS else 60
        elif key == "vsync":
            settings.vsync = bool(val)
        elif key == "sw_scale":
            settings.sw_scale = val if val in SW_SCALES else 3
        elif key == "show_fps":
            settings.show_fps = bool(val)
        elif key == "display_index":
            settings.display_index = max(0, val)
        elif key == "mods_enabled":
            settings.mods_enabled = bool(val)


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
                    f"demos {int(settings.demos)}\n"
                    f"last_wad {settings.last_wad}\n"
                    f"last_skill {settings.last_skill}\n"
                    f"last_map {settings.last_map}\n"
                    f"video_api {settings.video_api}\n"
                    f"gl_resolution {settings.gl_resolution}\n"
                    f"fps_limit {settings.fps_limit}\n"
                    f"vsync {int(settings.vsync)}\n"
                    f"display_mode {settings.display_mode}\n"
                    f"display_index {settings.display_index}\n"
                    f"sw_scale {settings.sw_scale}\n"
                    f"show_fps {int(settings.show_fps)}\n"
                    f"mods_enabled {int(settings.mods_enabled)}\n")
            for mid in sorted(settings.mods_on):
                f.write(f"mod_on {mid}\n")
            for mid in sorted(settings.mods_off):
                f.write(f"mod_off {mid}\n")
    except OSError:
        pass


@dataclass
class MenuItem:
    """One row: action id, patch, slider/toggle/choice wiring, shortcut."""

    action: str
    patch: str | None = None
    kind: str = "action"  # action | slider | toggle | choice | gap
    shortcut: str = ""
    # NOTE: ext rows (Options -> Extension) have no M_* patch: the viewer
    # fills label at runtime ("id ver ON/OFF (reason)"), small font.
    label: str | None = None


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
            MenuItem("video", None, "action", "v"),
            MenuItem("extensions", None, "action", "x"),
            MenuItem("sound", "M_SVOL", shortcut="s"),
        ], 60, 37, "main", 0),
        # NOTE: Options -> Extension (mod list, runtime-filled by the
        # viewer from ModManager.status(); Enter toggles, Esc back).
        "extensions": MenuDef("extensions", None, [], 24, 53, "options", 0),
        # NOTE: graphics settings (labels draw big like menu art, see
        # draw_text_big: no M_* patches exist for these rows). Backend
        # and sizes live in the launcher VIDEO tab (window recreation
        # proved unreliable live on some drivers); the in-game rows are
        # all live-safe. APPLY recreates the window once for the staged
        # rows (leaving via esc restores the entry snapshot).
        "video": MenuDef("video", None, [
            MenuItem("fps_limit", None, "choice", "f"),
            MenuItem("vsync", None, "choice", "v"),
            MenuItem("display_mode", None, "choice", "d"),
            MenuItem("display_index", None, "choice", "c"),
            MenuItem("show_fps", None, "choice", "p"),
            MenuItem("apply_video", None, "action", "y"),
        ], 36, 53, "options", 0),
        "sound": MenuDef("sound", None, [
            MenuItem("sfx", "M_SFXVOL", "slider", "s"),
            MenuItem("mus", "M_MUSVOL", "slider", "m"),
        ], 80, 64, "options", 0),
    }


def remove_extensions_entry(menus: dict) -> None:
    """Loader off (--no-mods): drop Options -> Extension, nothing to
    manage. Shortcuts only match current-menu rows, so 'x' dies too."""
    optdef = menus.get("options")
    if optdef is None:
        return
    optdef.items = [it for it in optdef.items
                    if it.action != "extensions"]
    optdef.last_on = min(optdef.last_on, max(len(optdef.items) - 1, 0))


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
        self._video_snapshot = None  # staged video values on menu entry
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

    def draw_text(self, fb, text: str, x: int, y: int) -> int:
        """HU_DrawTextLine: red STCFN string onto the index frame."""
        for ch in text.upper():
            if ch == " ":
                x += 4  # NOTE: vanilla space advance
                continue
            got = self.glyph(ch)
            if got is None:
                continue
            self._blit(f"STCFN{ord(ch):03d}", fb, x, y)
            x += got[0].width
        return x

    def draw_text_big(self, fb, text: str, x: int, y: int) -> int:
        """2x STCFN string: menu-sized rows where no M_* patch exists
        (VIDEO row, video submenu labels). Glyphs top out at 8px, so
        doubled rows are exactly LINEHEIGHT tall."""
        for ch in text.upper():
            if ch == " ":
                x += 8
                continue
            got = self.glyph(ch)
            if got is None:
                continue
            _patch, mat, msk = got
            h, w = mat.shape
            for sy in range(h):
                row = msk[sy]
                dy = y + sy * 2
                if dy < 0 or dy + 1 >= 200:
                    continue
                for sx in range(w):
                    if not row[sx]:
                        continue
                    dx = x + sx * 2
                    if dx < 0 or dx + 1 >= 320:
                        continue
                    fb[dy:dy + 2, dx:dx + 2] = mat[sy, sx]
            x += _patch.width * 2
        return x

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
            return self._adjust(mdef, 1 if k == "right" else -1)
        elif k == "enter":
            return self._activate(mdef)
        elif k == "esc":
            audio.play("swtchn")
            if mdef.prev is None:
                return ["close"]
            if self.current == "video":
                # NOTE: leaving without APPLY restores staged values.
                self._restore_video()
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

    def _adjust(self, mdef, delta: int) -> list:
        item = mdef.items[mdef.last_on]
        if item.kind == "slider":
            self.slider_adjust(item.action, delta)
        elif item.kind == "choice":
            # NOTE: video rows only stage values (APPLY commits them).
            self.choice_adjust(item.action, delta)
        return []

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
        elif act == "video":
            self._video_snapshot = self._staged_video()
            self.current = "video"
        elif act == "extensions":
            # NOTE: items are rebuilt by the viewer (owns ModManager);
            # it flips current itself after the rebuild.
            return [("ext_open",)]
        elif act.startswith("ext:"):
            mid = act[4:]
            if mid:
                return [("ext_toggle", mid)]
            return []
        elif act == "apply_video":
            self._video_snapshot = self._staged_video()
            return [("video_changed",)]
        elif item.kind == "choice":
            self.choice_adjust(act, 1)
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
            if item.action.startswith("ext:") and item.label is not None:
                # NOTE: ext rows are small text (id ver STATE + reason
                # would overflow big glyphs); skull still marks selection.
                self.draw_text(fb, item.label, mdef.x, y + 4)
            elif item.patch is not None:
                self._blit(item.patch, fb, mdef.x, y)
            elif item.kind == "choice":
                # NOTE: menu-sized label plus the staged value small
                # and right-aligned (a big value would overflow rows
                # like RESOLUTION/FULLSCREEN).
                self.draw_text_big(fb, self._choice_label(item.action),
                                   mdef.x, y)
                opts = self._choice_options(item.action)
                if opts:
                    val = opts[self._choice_index(item.action)]
                    vw = sum(self._glyph_w(ch) for ch in val)
                    self.draw_text(fb, val, 312 - vw, y + 4)
            elif item.patch is None:
                # NOTE: text-only action rows (VIDEO in options, APPLY
                # in the video menu) draw menu-sized.
                label = ("APPLY" if item.action == "apply_video"
                         else item.action.upper())
                self.draw_text_big(fb, label, mdef.x, y)
            if item.kind == "toggle" and item.action == "messages":
                self._blit("M_MSGON" if self.settings.messages
                           else "M_MSGOFF", fb, mdef.x + 175, y)
            if item.kind == "slider":
                val, top = self._slider_value(item.action)
                self._thermo(fb, mdef.x, y + LINEHEIGHT, val, top)
            if i == mdef.last_on:
                skull = "M_SKULL1" if self.which_skull == 0 else "M_SKULL2"
                self._blit(skull, fb, mdef.x + SKULL_XOFF,
                           y + SKULL_YOFF)
            y += LINEHEIGHT
            if item.kind == "slider":
                # NOTE: vanilla M_DrawOptions/M_DrawSound put the thermo
                # bar in the gap row below the label (m_menu.c), so a
                # slider row costs two line heights.
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

    # -- video choice rows (Options -> Video, staged till APPLY) --

    _VIDEO_KEYS = ("video_api", "gl_resolution", "sw_scale",
                   "fps_limit", "vsync", "display_mode",
                   "display_index", "show_fps")

    def _staged_video(self) -> dict:
        """Snapshot the apply-relevant video settings (esc restores)."""
        return {k: getattr(self.settings, k) for k in self._VIDEO_KEYS}

    def _restore_video(self) -> None:
        """Drop staged video changes (leaving the menu without APPLY)."""
        if self._video_snapshot is None:
            return
        for k, v in self._video_snapshot.items():
            setattr(self.settings, k, v)
        self._video_snapshot = None

    def _choice_options(self, action: str) -> tuple:
        """Display strings cycled by a choice row (pure order)."""
        if action == "video_api":
            return tuple(v.upper() for v in VIDEO_APIS)
        if action == "gl_resolution":
            return tuple(r.upper() for r in GL_RESOLUTIONS)
        if action == "sw_scale":
            return tuple(f"{s * 100}%" for s in SW_SCALES)
        if action == "fps_limit":
            return tuple("UNLIMITED" if v == 0 else str(v)
                         for v in FPS_LIMITS)
        if action in ("vsync", "show_fps"):
            return ("OFF", "ON")
        if action == "display_mode":
            return tuple(v.upper() for v in DISPLAY_MODES)
        if action == "display_index":
            return tuple(str(i + 1) for i in range(display_count()))
        return ()

    def _choice_index(self, action: str) -> int:
        """Current option index (unknown cfg values show first)."""
        s = self.settings
        opts = self._choice_options(action)
        if action == "video_api":
            cur = s.video_api.upper()
        elif action == "gl_resolution":
            cur = s.gl_resolution.upper()
        elif action == "sw_scale":
            cur = f"{s.sw_scale * 100}%"
        elif action == "fps_limit":
            cur = "UNLIMITED" if s.fps_limit == 0 else str(s.fps_limit)
        elif action == "vsync":
            cur = "ON" if s.vsync else "OFF"
        elif action == "show_fps":
            cur = "ON" if s.show_fps else "OFF"
        elif action == "display_mode":
            cur = s.display_mode.upper()
        elif action == "display_index":
            cur = str(min(s.display_index, display_count() - 1) + 1)
        else:
            return 0
        return opts.index(cur) if cur in opts else 0

    def choice_adjust(self, action: str, delta: int) -> None:
        """Cycle a staged video choice (APPLY commits; no event)."""
        from pydoom import audio
        opts = self._choice_options(action)
        if not opts:
            return
        nxt = opts[(self._choice_index(action) + delta) % len(opts)]
        s = self.settings
        if action == "video_api":
            s.video_api = nxt.lower()
        elif action == "gl_resolution":
            s.gl_resolution = nxt.lower()
        elif action == "sw_scale":
            s.sw_scale = SW_SCALES[opts.index(nxt)]
        elif action == "fps_limit":
            s.fps_limit = 0 if nxt == "UNLIMITED" else int(nxt)
        elif action == "vsync":
            s.vsync = nxt == "ON"
        elif action == "show_fps":
            s.show_fps = nxt == "ON"
        elif action == "display_mode":
            s.display_mode = nxt.lower()
        elif action == "display_index":
            s.display_index = int(nxt) - 1
        else:
            return
        audio.play("stnmov")

    def _choice_label(self, action: str) -> str:
        return {"video_api": "VIDEO API",
                "gl_resolution": "RESOLUTION",
                "sw_scale": "WINDOW SCALE",
                "fps_limit": "FPS LIMIT",
                "vsync": "VSYNC",
                "display_mode": "DISPLAY",
                "display_index": "SCREEN",
                "show_fps": "SHOW FPS"}.get(action, action.upper())

    def _thermo(self, fb, x: int, y: int, val: int, top: int,
                slots: int = 10) -> None:
        """M_DrawThermo: caps plus lit/unlit boxes (vanilla puts the bar
        in the row below the label, m_menu.c)."""
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
