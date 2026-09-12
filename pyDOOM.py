"""pyDOOM launcher: picker plus settings, on separate tabs.

PLAY tab holds real scrollable lists (IWADs always; maps only when
DEBUG MODE is on, otherwise the game boots its first map). The
SETTINGS tab holds the skill list and the inline viewer flags.
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
         "extra_hud")
FLAG_LABELS = {"debug": "DEBUG MODE", "fast": "FAST",
               "respawn": "RESPAWN", "nomonsters": "NO MONSTERS",
               "kinematic": "KINEMATIC", "demos": "DEMO COMPAT",
               "extra_hud": "EXTRA HUD"}


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


def build_viewer_args(wad: str, map_name: str, skill: str,
                      debug: bool = False, fast: bool = False,
                      respawn: bool = False, nomonsters: bool = False,
                      kinematic: bool = False,
                      extra_hud: bool = False) -> list:
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
    return args


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
    from pydoom.menu import (CONFIG_PATH, Settings, settings_load,
                             settings_save)
    cfg = Settings()
    settings_load(os.path.join(ROOT, CONFIG_PATH), cfg)
    wads = find_wads()
    if not wads:
        print("pyDOOM: no .WAD next to pyDOOM.py (want DOOM1.WAD)")
        return 1
    wad = cfg.last_wad if cfg.last_wad in wads else wads[0]
    maps = maps_in(wad)
    skill = cfg.last_skill if cfg.last_skill in SKILLS else "normal"
    flags = {name: (name == "demos" and cfg.demos) for name in FLAGS}

    pygame.init()
    screen = pygame.display.set_mode((560, 420))
    pygame.display.set_caption("pyDOOM launcher")
    font = pygame.font.SysFont("monospace", 18)
    big = pygame.font.SysFont("monospace", 26, bold=True)
    small = pygame.font.SysFont("monospace", 14)

    tabs = ("PLAY", "SETTINGS")
    tab = 0
    wad_list = PickList(wads, visible=6)
    if wad in wads:
        wad_list.index = wads.index(wad)
    map_list = PickList(maps, visible=9)
    if cfg.last_map in maps:
        map_list.index = maps.index(cfg.last_map)
    skill_list = PickList([s.upper() for s in SKILLS], visible=5)
    skill_list.index = SKILLS.index(skill)
    flag_list = PickList([FLAG_LABELS[n] for n in FLAGS], visible=7)
    focus = 0  # NOTE: which list owns up/down on the PLAY tab
    sfocus = 0  # NOTE: 0 skill, 1 flags on the SETTINGS tab
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
        settings_save(os.path.join(ROOT, CONFIG_PATH), cfg)
        args = build_viewer_args(
            wad, start_map, picked_skill,
            flags["debug"], flags["fast"], flags["respawn"],
            flags["nomonsters"], flags["kinematic"],
            flags["extra_hud"])
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
        else:
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
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_TAB or (
                        ev.key == pygame.K_1 or ev.key == pygame.K_2):
                    tab = 1 - tab if ev.key == pygame.K_TAB else \
                        (ev.key - pygame.K_1)
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
                else:
                    if not flags["debug"]:
                        sfocus = 1  # NOTE: skill hides without debug
                    if ev.key == pygame.K_UP:
                        (flag_list if sfocus else skill_list).move(-1)
                    elif ev.key == pygame.K_DOWN:
                        (flag_list if sfocus else skill_list).move(1)
                    elif ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                        if flags["debug"]:
                            sfocus = 1 - sfocus
                    elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        if sfocus:
                            toggle_flag()
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
                else:
                    if flags["debug"] and skill_list.click(ev.pos):
                        sfocus = 0
                    elif flag_list.click(ev.pos):
                        sfocus = 1
                        toggle_flag()
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
