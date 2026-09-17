"""pyDOOM launcher: picker plus settings, on separate tabs.

PLAY tab holds real scrollable lists (IWADs always; maps only when
DEBUG MODE is on, otherwise the game boots its first map). The
SETTINGS tab holds the skill list and the inline viewer flags. The
VIDEO tab holds the render backend plus its backend-specific picks
(GL resolution vs software window scale, shown conditionally).
LAUNCH/QUIT stay pinned in the bottom bar on every tab; LAUNCH
saves the picks to pydoom.cfg and runs tools/doom_view.py with the
matching argv. File-path commands (--playdemo/...) stay CLI-only.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if getattr(sys, "frozen", False):  # NOTE: PyInstaller onedir layout:
    # datas (DOOM1.WAD) live under _internal; cfg/log land with them
    # (portable build: keep everything inside the bundle dir).
    ROOT = os.path.abspath(getattr(sys, "_MEIPASS", os.path.dirname(
        os.path.abspath(sys.executable))))
sys.path.insert(0, ROOT)

if len(sys.argv) > 1 and sys.argv[1] == "--viewer":
    # NOTE: frozen child mode (subprocess can't re-run a script from
    # inside the bundle, so the exe re-enters here and jumps straight
    # into the viewer with the remaining argv).
    sys.argv = [sys.argv[0]] + sys.argv[2:]
    if getattr(sys, "frozen", False) and len(sys.argv) > 2:
        # NOTE: manual bundle runs pass bare names (DOOM1.WAD):
        # resolve them inside the bundle before the viewer opens.
        cand = os.path.join(ROOT, sys.argv[2])
        if not os.path.isabs(sys.argv[2]) and os.path.exists(cand):
            sys.argv[2] = cand
    from tools.doom_view import main as viewer_main
    raise SystemExit(viewer_main())

SKILLS = ("baby", "easy", "normal", "hard", "nightmare")
FALLBACK_WAD = "DOOM1.WAD"
FLAGS = ("debug", "fast", "respawn", "nomonsters", "kinematic", "demos",
         "extra_hud", "dynlights", "linear_filter")
FLAG_LABELS = {"debug": "DEBUG MODE", "fast": "FAST",
               "respawn": "RESPAWN", "nomonsters": "NO MONSTERS",
               "kinematic": "KINEMATIC", "demos": "DEMO COMPAT",
               "extra_hud": "EXTRA HUD", "dynlights": "DYNAMIC LIGHTS",
               "linear_filter": "LINEAR FILTER"}


def find_wads(root: str = ROOT) -> list:
    """*.WAD/*.wad next to the launcher, fallback first (vanilla)."""
    found = sorted(f for f in os.listdir(root)
                   if f.lower().endswith(".wad")
                   and os.path.isfile(os.path.join(root, f)))
    found.sort(key=lambda f: (f != FALLBACK_WAD, f.lower()))
    return found


def maps_in(wad_name: str, root: str = ROOT) -> list:
    """Map lumps of the picked WAD (E1M1 when unreadable)."""
    try:
        from pydoom.wad import WadFile
        return WadFile(os.path.join(root, wad_name)).list_maps()
    except Exception:
        return ["E1M1"]


def accept_wad_drop(path: str, root: str = ROOT) -> tuple:
    """File a dropped WAD next to the launcher (unit tested, no GUI).

    Returns (name, note): name selects the list entry, note reports.
    Non-.WAD files and bad magic are ignored, existing entries are
    never overwritten, drops from the launcher dir just select.
    """
    name = os.path.basename(path or "")
    if not name.lower().endswith(".wad") or not os.path.isfile(path):
        return None, f"ignored {name or path}: not a .WAD"
    try:
        with open(path, "rb") as f:
            if f.read(4) not in (b"IWAD", b"PWAD"):
                return None, f"ignored {name}: not a WAD file"
    except OSError:
        return None, f"ignored {name}: unreadable"
    dest = os.path.join(root, name)
    if os.path.abspath(path) == os.path.abspath(dest):
        return name, f"{name} selected"
    if os.path.exists(dest):
        return name, f"{name} already listed"
    try:
        import shutil
        shutil.copy2(path, dest)
    except OSError as exc:
        return None, f"copy failed: {exc}"
    return name, f"{name} added"


def build_viewer_args(wad: str, map_name: str, skill: str,
                      debug: bool = False, fast: bool = False,
                      respawn: bool = False, nomonsters: bool = False,
                      kinematic: bool = False,
                      extra_hud: bool = False,
                      video_api: str = "software",
                      mods_enabled: bool = True,
                      mods_on: tuple = (),
                      mods_off: tuple = (),
                      dynlights: bool = False,
                      texture_filter: str = "nearest") -> list:
    """Viewer argv for a launcher selection (unit tested, no GUI)."""
    if getattr(sys, "frozen", False):
        args = [sys.executable, "--viewer", map_name,
                os.path.join(ROOT, wad), f"--skill={skill}"]
    else:
        args = [sys.executable, os.path.join(ROOT, "tools",
                                             "doom_view.py"),
                map_name, os.path.join(ROOT, wad), f"--skill={skill}"]
    if debug:
        args.append("--debug")
    if fast:
        args.append("--fast")
    if respawn:
        args.append("--respawn")
    if nomonsters:
        # NOTE: vanilla -nomonsters rides the demo header; the viewer
        # reads the same flag name off argv.
        args.append("--nomonsters")
    if kinematic:
        args.append("--kinematic")
    if extra_hud:
        args.append("--extra-hud")
    if dynlights:
        args.append("--dynlights")
    if texture_filter == "linear":
        args.append("--texture-filter=linear")
    if video_api in ("opengl", "openglv1", "openglv2"):
        # NOTE: software stays the default, so only a GL backend rides
        # argv (keeps argv byte-stable with no GL picked).
        args.append(f"--video-api={video_api}")
    if not mods_enabled:
        args.append("--no-mods")
    else:
        # NOTE: per-mod deviations only (manifest defaults need no argv,
        # keeps old argv byte-stable with no mods installed).
        args += [f"--mod-on={mid}" for mid in mods_on]
        args += [f"--mod-off={mid}" for mid in mods_off]
    return args


def video_tab_rows(video_api: str) -> tuple:
    """Visible VIDEO-tab rows for a backend (pure, unit tested).

    The backend row always shows; the size row is conditional (GL
    resolution vs software window scale), so each backend only offers
    settings that apply to it.
    """
    if video_api in ("opengl", "openglv1", "openglv2"):
        return ("api", "resolution")
    return ("api", "scale")


def mod_tab_rows(status, mods_enabled: bool = True) -> tuple:
    """(ids, labels) for the launcher MODS list (pure, unit tested).

    Row 0 (id None) is the master loader switch; the rest mirror the
    in-game Extension menu via ext.row_label. status is
    ModManager.status().
    """
    from pydoom.ext import row_label
    ids = [None]
    labels = [f"MOD LOADER: {'ON' if mods_enabled else 'OFF'}"]
    for mid, ver, state, reason in status:
        ids.append(mid)
        labels.append(row_label(mid, ver, state, reason))
    if len(ids) == 1:
        ids.append("")
        labels.append("(no mods found)")
    return ids, labels


def mod_overrides(states) -> tuple:
    """Per-mod deviations from manifest defaults (pure, unit tested).

    states: iterable of (mid, user_on, enabled_default). Returns
    (on_ids, off_ids) sets for cfg/argv; mods at default stay out.
    """
    on, off = set(), set()
    for mid, user_on, default in states:
        if user_on and not default:
            on.add(mid)
        elif not user_on and default:
            off.add(mid)
    return on, off


def res_label_to_value(label: str) -> str | None:
    """'960X600' -> '960x600' cfg value (None when invalid)."""
    from pydoom.menu import parse_resolution
    parsed = parse_resolution(label or "")
    if parsed is None:
        return None
    return f"{parsed[0]}x{parsed[1]}"


def scale_label_to_value(label: str) -> int | None:
    """'200%' -> 2 cfg value (None when invalid)."""
    from pydoom.menu import SW_SCALES
    try:
        scale = int((label or "").strip().rstrip("%")) // 100
    except ValueError:
        return None
    return scale if scale in SW_SCALES else None


class PickList:
    """Scrollable single-column list (click or arrows to select)."""

    def __init__(self, items: list, row_h: int = 24, visible: int = 8):
        self.items = list(items)
        self.row_h = row_h
        self.visible = visible
        self.index = 0
        self.top = 0
        self.rect = None

    def move(self, direction: int) -> None:
        if not self.items:
            return
        self.index = (self.index + direction) % len(self.items)
        if self.index < self.top:
            self.top = self.index
        elif self.index >= self.top + self.visible:
            self.top = self.index - self.visible + 1

    def click(self, pos) -> bool:
        if self.rect is None or not self.items:
            return False
        x, y = pos
        if not self.rect.collidepoint(x, y):
            return False
        hit = (y - self.rect.y) // self.row_h + self.top
        if 0 <= hit < len(self.items):
            self.index = hit
            return True
        return False

    def selected(self):
        if not self.items:
            return None
        return self.items[self.index]

    def draw(self, screen, font, x: int, y: int, w: int,
            active: bool) -> int:
        """Draw title-less rows; returns the height consumed."""
        self.rect = __import__("pygame").Rect(
            x, y, w, self.row_h * min(self.visible, max(len(self.items),
                                                        1)))
        for slot in range(min(self.visible, len(self.items))):
            i = self.top + slot
            ry = y + slot * self.row_h
            if i == self.index:
                color = (255, 220, 120) if active else (140, 130, 110)
                screen.fill((50, 30, 20), (x, ry, w, self.row_h))
            else:
                color = (160, 160, 160)
            screen.blit(font.render(str(self.items[i]), True, color),
                        (x + 8, ry + 3))
        return self.rect.height


def main() -> int:
    import pygame
    from pydoom.menu import (CONFIG_PATH, VIDEO_APIS, GL_RESOLUTIONS,
                             SW_SCALES, Settings, settings_load,
                             settings_save)
    cfg = Settings()
    settings_load(os.path.join(ROOT, CONFIG_PATH), cfg)
    from pydoom import ext
    modmgr = ext.ModManager(ext.default_mods_dir())
    modmgr.discover()
    mods_enabled = cfg.mods_enabled
    # NOTE: cfg per-mod picks (launcher owns persistence; the in-game
    # menu only flips the live session). Backend resolves on the MODS
    # tab against the VIDEO pick, and at boot in the viewer.
    modmgr.apply_overrides(set(cfg.mods_on), set(cfg.mods_off))
    wads = find_wads()
    if not wads:
        print("pyDOOM: no .WAD next to pyDOOM.py (want DOOM1.WAD)")
        return 1
    wad = cfg.last_wad if cfg.last_wad in wads else wads[0]
    maps = maps_in(wad)
    skill = cfg.last_skill if cfg.last_skill in SKILLS else "normal"
    flags = {name: ((name == "demos" and cfg.demos)
                     or (name == "dynlights" and cfg.dynlights)
                     or (name == "linear_filter"
                         and cfg.texture_filter == "linear"))
             for name in FLAGS}

    pygame.init()
    screen = pygame.display.set_mode((560, 420))
    from pydoom.version import get_version
    pygame.display.set_caption(f"pyDOOM launcher v{get_version(ROOT)}")
    font = pygame.font.SysFont("monospace", 18)
    big = pygame.font.SysFont("monospace", 26, bold=True)
    small = pygame.font.SysFont("monospace", 14)

    tabs = ("PLAY", "SETTINGS", "VIDEO", "MODS")
    tab = 0
    wad_list = PickList(wads, visible=6)
    if wad in wads:
        wad_list.index = wads.index(wad)
    map_list = PickList(maps, visible=9)
    if cfg.last_map in maps:
        map_list.index = maps.index(cfg.last_map)
    skill_list = PickList([s.upper() for s in SKILLS], visible=5)
    skill_list.index = SKILLS.index(skill)
    video_list = PickList([v.upper() for v in VIDEO_APIS], visible=3)
    # NOTE: render backend lives on the VIDEO tab now (was SETTINGS);
    # resolution/scale rows show conditionally per backend below.
    video_list.index = VIDEO_APIS.index(cfg.video_api) \
        if cfg.video_api in VIDEO_APIS else 0
    res_labels = [r.upper() for r in GL_RESOLUTIONS]
    res_list = PickList(res_labels, visible=5)
    res_list.index = res_labels.index(cfg.gl_resolution.upper()) \
        if cfg.gl_resolution.upper() in res_labels \
        else res_labels.index("960X600")
    scale_labels = [f"{s * 100}%" for s in SW_SCALES]
    scale_list = PickList(scale_labels, visible=3)
    scale_list.index = scale_labels.index(f"{cfg.sw_scale * 100}%") \
        if f"{cfg.sw_scale * 100}%" in scale_labels else 2
    flag_list = PickList([FLAG_LABELS[n] for n in FLAGS], visible=9)
    mod_list = PickList([], visible=8)  # NOTE: MODS rows rebuilt per draw
    focus = 0  # NOTE: which list owns up/down on the PLAY tab
    sfocus = 0  # NOTE: 0 skill, 1 flags on the SETTINGS tab
    vfocus = 0  # NOTE: 0 api, 1 size on the VIDEO tab
    launch_rect = pygame.Rect(24, 420 - 44, 180, 30)
    quit_rect = pygame.Rect(560 - 204, 420 - 44, 180, 30)
    tab_rects = [pygame.Rect(24 + i * 130, 44, 120, 26) for i in
                 range(len(tabs))]

    def sync_wad() -> None:
        nonlocal wad, maps
        picked = wad_list.selected()
        if picked is not None and picked != wad:
            wad = picked
            maps = maps_in(wad)
            map_list.items = maps
            map_list.index = 0
            map_list.top = 0

    def rescan_wads(select: str | None = None) -> None:
        """Re-read the launcher dir after a drop; keep or take selection."""
        nonlocal wad, wads, maps
        wads = find_wads()
        wad_list.items = wads
        if select in wads:
            wad_list.index = wads.index(select)
        else:
            wad_list.index = min(wad_list.index, max(len(wads) - 1, 0))
        wad_list.top = 0
        sync_wad()

    note = ""  # NOTE: last drop result, under the IWAD list

    def launch() -> None:
        nonlocal running
        sync_wad()
        # NOTE: map and skill only matter in debug (direct boot);
        # otherwise the game boots E1M1/normal and its menu decides.
        picked_skill = SKILLS[skill_list.index] if flags["debug"] \
            else "normal"
        start_map = map_list.selected() if flags["debug"] else maps[0]
        cfg.last_wad, cfg.last_skill = wad, SKILLS[skill_list.index]
        cfg.last_map = start_map
        cfg.demos = flags["demos"]
        cfg.dynlights = flags["dynlights"]
        cfg.texture_filter = ("linear" if flags["linear_filter"]
                              else "nearest")
        cfg.video_api = VIDEO_APIS[video_list.index]
        if cfg.video_api != "software":
            picked_res = res_label_to_value(res_list.selected())
            if picked_res is not None:
                cfg.gl_resolution = picked_res
        else:
            picked_scale = scale_label_to_value(scale_list.selected())
            if picked_scale is not None:
                cfg.sw_scale = picked_scale
        dev_on, dev_off = mod_overrides(
            (mid, rec.user_on, rec.meta.get("enabled_default", True))
            for mid, rec in modmgr.records.items())
        cfg.mods_enabled = mods_enabled
        cfg.mods_on, cfg.mods_off = set(dev_on), set(dev_off)
        settings_save(os.path.join(ROOT, CONFIG_PATH), cfg)
        args = build_viewer_args(
            wad, start_map, picked_skill,
            flags["debug"], flags["fast"], flags["respawn"],
            flags["nomonsters"], flags["kinematic"],
            flags["extra_hud"], VIDEO_APIS[video_list.index],
            mods_enabled, sorted(dev_on), sorted(dev_off),
            flags["dynlights"],
            "linear" if flags["linear_filter"] else "nearest")
        print("pyDOOM:", " ".join(args[1:]))
        # NOTE: detached child (new session, own stdio): closing the
        # terminal or Ctrl+C here never reaches the game afterwards.
        log_path = os.path.join(ROOT, "pydoom-viewer.log")
        try:
            log = open(log_path, "wb")
        except OSError:
            log = subprocess.DEVNULL
        subprocess.Popen(args, cwd=ROOT, start_new_session=True,
                         stdin=subprocess.DEVNULL, stdout=log,
                         stderr=subprocess.STDOUT, close_fds=True)
        print(f"pyDOOM: detached, log at {log_path}")
        running = False  # NOTE: LAUNCH closes the launcher for good

    def toggle_flag() -> None:
        name = FLAGS[flag_list.index]
        flags[name] = not flags[name]

    def toggle_mod() -> None:
        """MODS tab flip: row 0 is the master switch, the rest are mods
        (refused ones stay refused: toggle() only flips user_on)."""
        nonlocal mods_enabled
        ids, _labels = mod_tab_rows(modmgr.status(), mods_enabled)
        if mod_list.index >= len(ids):
            return
        if mod_list.index == 0:
            mods_enabled = not mods_enabled
        elif ids[mod_list.index]:
            modmgr.toggle(ids[mod_list.index])

    running = True
    while running:
        screen.fill((10, 10, 18))
        screen.blit(big.render("pyDOOM", True, (220, 30, 30)), (24, 8))
        for i, name in enumerate(tabs):
            color = (255, 220, 120) if i == tab else (110, 110, 110)
            pygame.draw.rect(screen, (30, 30, 40), tab_rects[i])
            screen.blit(font.render(name, True, color),
                        (tab_rects[i].x + 12, tab_rects[i].y + 3))
        if tab == 0:
            screen.blit(small.render("IWAD", True, (90, 90, 90)),
                        (24, 78))
            wad_list.draw(screen, font, 24, 96, 240, focus == 0)
            hint_y = (wad_list.rect.bottom + 6 if wad_list.rect is not None
                      else 246)
            screen.blit(small.render("(or drag and drop the .WAD)",
                                     True, (90, 90, 90)), (24, hint_y))
            if note:
                screen.blit(small.render(note[:40], True, (150, 150,
                                                             150)),
                            (24, hint_y + 18))
            if flags["debug"]:
                screen.blit(small.render("MAP (debug)", True, (90, 90,
                                                                90)),
                            (296, 78))
                map_list.draw(screen, font, 296, 96, 240, focus == 1)
            else:
                screen.blit(small.render("MAP list appears with",
                                         True, (90, 90, 90)), (296, 96))
                screen.blit(small.render("DEBUG MODE on.",
                                         True, (90, 90, 90)), (296, 114))
        elif tab == 1:
            if flags["debug"]:
                screen.blit(small.render("SKILL", True, (90, 90, 90)),
                            (24, 78))
                skill_list.draw(screen, font, 24, 96, 200, sfocus == 0)
            else:
                screen.blit(small.render("SKILL list appears with",
                                         True, (90, 90, 90)), (24, 96))
                screen.blit(small.render("DEBUG MODE on (the game",
                                         True, (90, 90, 90)), (24, 114))
                screen.blit(small.render("menu picks it otherwise).",
                                         True, (90, 90, 90)), (24, 132))
            screen.blit(small.render("FLAGS (enter toggles)", True,
                                     (90, 90, 90)), (296, 78))
            y0 = 96
            for slot, name in enumerate(FLAGS):
                ry = y0 + slot * flag_list.row_h
                picked = slot == flag_list.index and sfocus == 1
                color = ((255, 220, 120) if picked else (160, 160, 160))
                if picked:
                    screen.fill((50, 30, 20),
                                (296, ry, 240, flag_list.row_h))
                screen.blit(font.render(FLAG_LABELS[name], True, color),
                            (304, ry + 3))
                state = "ON" if flags[name] else "OFF"
                img = small.render(state, True, color)
                screen.blit(img, (536 - 24 - img.get_width(), ry + 4))
            flag_list.rect = pygame.Rect(296, y0, 240,
                                         flag_list.row_h * len(FLAGS))
        elif tab == 2:
            # NOTE: VIDEO tab (render backend + conditional size row:
            # resolution for OpenGL, window scale for software).
            api = VIDEO_APIS[video_list.index]
            screen.blit(small.render("RENDER API", True, (90, 90, 90)),
                        (24, 78))
            video_list.draw(screen, font, 24, 96, 200, vfocus == 0)
            if api != "software":
                label, size_list = f"RESOLUTION ({api.upper()})", res_list
            else:
                label, size_list = "WINDOW SCALE (SOFTWARE)", scale_list
            screen.blit(small.render(label, True, (90, 90, 90)),
                        (24, 168))
            size_list.draw(screen, font, 24, 186, 200, vfocus == 1)
        else:
            # NOTE: MODS tab (loader switch + per-mod rows, same labels
            # as the in-game Extension menu; backend warnings resolve
            # against the VIDEO tab pick).
            want_be = VIDEO_APIS[video_list.index]
            if modmgr.backend != want_be:
                modmgr.set_backend(want_be)
            _ids, mod_labels = mod_tab_rows(modmgr.status(),
                                            mods_enabled)
            mod_list.items = mod_labels
            if mod_list.index >= len(mod_labels):
                mod_list.index = max(len(mod_labels) - 1, 0)
            screen.blit(small.render("MODS (enter toggles)", True,
                                     (90, 90, 90)), (24, 78))
            mod_list.draw(screen, font, 24, 96, 512, True)
            foot = (mod_list.rect.bottom + 6
                    if mod_list.rect is not None else 300)
            if not mods_enabled:
                screen.blit(small.render(
                    "(loader off: the game starts without mods)",
                    True, (150, 150, 150)), (24, foot))
                foot += 18
            screen.blit(small.render(
                "refused mods (NEEDS/BAD) stay off until fixed",
                True, (90, 90, 90)), (24, foot))
        for rect, label in ((launch_rect, "LAUNCH"), (quit_rect, "QUIT")):
            pygame.draw.rect(screen, (40, 90, 40) if label == "LAUNCH"
                             else (90, 40, 40), rect)
            img = font.render(label, True, (240, 240, 240))
            screen.blit(img, (rect.x + (rect.width - img.get_width())
                              // 2, rect.y + 4))
        screen.blit(small.render("tab switch - up/down move - left/right "
                                 "cycle - enter toggle - L launch",
                                 True, (90, 90, 90)), (24, 420 - 62))
        pygame.display.flip()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == getattr(pygame, "DROPFILE", None):
                # NOTE: a dropped *.WAD lands next to the launcher and
                # selects itself (SDL sends one event per file).
                picked, note = accept_wad_drop(getattr(ev, "file", ""))
                if picked is not None:
                    rescan_wads(picked)
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_TAB or ev.key in (
                        pygame.K_1, pygame.K_2, pygame.K_3, pygame.K_4):
                    tab = (tab + 1) % len(tabs) \
                        if ev.key == pygame.K_TAB else (ev.key - pygame.K_1)
                elif ev.key == pygame.K_l:
                    launch()
                elif tab == 0:
                    if ev.key == pygame.K_UP:
                        (map_list if focus and flags["debug"]
                         else wad_list).move(-1)
                        sync_wad()
                    elif ev.key == pygame.K_DOWN:
                        (map_list if focus and flags["debug"]
                         else wad_list).move(1)
                        sync_wad()
                    elif ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        if flags["debug"]:
                            focus = 1 - focus
                    elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        launch()
                elif tab == 1:
                    order = (0, 1) if flags["debug"] else (1,)
                    if sfocus not in order:
                        sfocus = order[0]  # NOTE: skill hides w/o debug
                    focus_lists = (skill_list, flag_list)
                    if ev.key == pygame.K_UP:
                        focus_lists[sfocus].move(-1)
                    elif ev.key == pygame.K_DOWN:
                        focus_lists[sfocus].move(1)
                    elif ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        step = 1 if ev.key == pygame.K_RIGHT else -1
                        sfocus = order[(order.index(sfocus) + step)
                                       % len(order)]
                    elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER) \
                            and sfocus == 1:
                        toggle_flag()
                elif tab == 2:
                    # NOTE: VIDEO tab (backend + conditional size row).
                    api = VIDEO_APIS[video_list.index]
                    vis = [video_list] + (
                        [res_list] if api != "software" else [scale_list])
                    if vfocus >= len(vis):
                        vfocus = 0
                    if ev.key == pygame.K_UP:
                        vis[vfocus].move(-1)
                    elif ev.key == pygame.K_DOWN:
                        vis[vfocus].move(1)
                    elif ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        step = 1 if ev.key == pygame.K_RIGHT else -1
                        vfocus = (vfocus + step) % len(vis)
                else:
                    # NOTE: MODS tab (master switch + mod rows).
                    if ev.key == pygame.K_UP:
                        mod_list.move(-1)
                    elif ev.key == pygame.K_DOWN:
                        mod_list.move(1)
                    elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        toggle_mod()
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                if launch_rect.collidepoint(ev.pos):
                    launch()
                elif quit_rect.collidepoint(ev.pos):
                    running = False
                elif any(r.collidepoint(ev.pos) for r in tab_rects):
                    tab = next(i for i, r in enumerate(tab_rects)
                               if r.collidepoint(ev.pos))
                elif tab == 0:
                    if wad_list.click(ev.pos):
                        focus = 0
                        sync_wad()
                    elif flags["debug"] and map_list.click(ev.pos):
                        focus = 1
                elif tab == 1:
                    if flags["debug"] and skill_list.click(ev.pos):
                        sfocus = 0
                    elif flag_list.click(ev.pos):
                        sfocus = 1
                        toggle_flag()
                elif tab == 2:
                    # NOTE: VIDEO tab clicks (size row follows backend).
                    if video_list.click(ev.pos):
                        vfocus = 0
                    elif (res_list if VIDEO_APIS[video_list.index]
                            != "software" else scale_list).click(ev.pos):
                        vfocus = 1
                else:
                    # NOTE: MODS tab clicks (master row + mod rows).
                    if mod_list.click(ev.pos):
                        toggle_mod()
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
