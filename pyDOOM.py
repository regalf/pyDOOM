"""pyDOOM launcher: WAD/skill/map picker plus inline flags.

Lists the IWADs next to this file (fallback DOOM1.WAD), the maps in
the picked WAD, and the viewer flags worth a toggle (debug, fast,
respawn, nomonsters, kinematic, demos); LAUNCH saves the picks to
pydoom.cfg and runs tools/doom_view.py with the matching argv.
File-path commands (--playdemo/--record-demo/...) stay CLI-only.
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

SKILLS = ("baby", "easy", "normal", "hard", "nightmare")
FALLBACK_WAD = "DOOM1.WAD"


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
                      kinematic: bool = False) -> list:
    """Viewer argv for a launcher selection (unit tested, no GUI)."""
    args = [sys.executable, os.path.join(ROOT, "tools", "doom_view.py"),
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
    return args


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
    sel_map = cfg.last_map if cfg.last_map in maps else maps[0]
    skill = cfg.last_skill if cfg.last_skill in SKILLS else "normal"
    debug, fast, respawn = False, False, False
    nomonsters, kinematic, demos = False, False, cfg.demos

    pygame.init()
    screen = pygame.display.set_mode((480, 360))
    pygame.display.set_caption("pyDOOM launcher")
    font = pygame.font.SysFont("monospace", 20)
    big = pygame.font.SysFont("monospace", 28, bold=True)

    rows = ["wad", "map", "skill", "debug", "fast", "respawn",
            "nomonsters", "kinematic", "demos", "launch", "quit"]
    labels = {"wad": "IWAD", "map": "MAP", "skill": "SKILL",
              "debug": "DEBUG MODE", "fast": "FAST",
              "respawn": "RESPAWN", "nomonsters": "NO MONSTERS",
              "kinematic": "KINEMATIC", "demos": "DEMO COMPAT",
              "launch": "LAUNCH", "quit": "QUIT"}

    def value(row: str) -> str:
        if row == "wad":
            return wad
        if row == "map":
            return sel_map
        if row == "skill":
            return skill.upper()
        if row == "launch":
            return ">"
        if row == "quit":
            return "x"
        flag = {"debug": debug, "fast": fast, "respawn": respawn,
                "nomonsters": nomonsters, "kinematic": kinematic,
                "demos": demos}[row]
        return "ON" if flag else "OFF"

    def cycle(row: str, direction: int) -> None:
        nonlocal wad, maps, sel_map, skill
        nonlocal debug, fast, respawn, nomonsters, kinematic, demos
        if row == "wad":
            wad = wads[(wads.index(wad) + direction) % len(wads)]
            maps = maps_in(wad)
            sel_map = maps[0] if sel_map not in maps else sel_map
        elif row == "map":
            sel_map = maps[(maps.index(sel_map) + direction) % len(maps)]
        elif row == "skill":
            skill = SKILLS[(SKILLS.index(skill) + direction) % len(SKILLS)]
        elif row in ("debug", "fast", "respawn", "nomonsters",
                     "kinematic", "demos"):
            toggled = not {"debug": debug, "fast": fast,
                           "respawn": respawn, "nomonsters": nomonsters,
                           "kinematic": kinematic,
                           "demos": demos}[row]
            if row == "debug":
                debug = toggled
            elif row == "fast":
                fast = toggled
            elif row == "respawn":
                respawn = toggled
            elif row == "nomonsters":
                nomonsters = toggled
            elif row == "kinematic":
                kinematic = toggled
            else:
                demos = toggled

    def launch() -> None:
        cfg.last_wad, cfg.last_skill, cfg.last_map = wad, skill, sel_map
        cfg.demos = demos
        settings_save(os.path.join(ROOT, CONFIG_PATH), cfg)
        args = build_viewer_args(wad, sel_map, skill, debug, fast,
                                 respawn, nomonsters, kinematic)
        print("pyDOOM:", " ".join(args[1:]))
        subprocess.run(args, cwd=ROOT)

    at = 0
    rects: list = []
    running = True
    while running:
        screen.fill((10, 10, 18))
        screen.blit(big.render("pyDOOM", True, (220, 30, 30)), (24, 12))
        rects = []
        for i, row in enumerate(rows):
            y = 56 + i * 26
            color = (255, 220, 120) if i == at else (160, 160, 160)
            screen.blit(font.render(labels[row], True, color), (24, y))
            val = value(row)
            img = font.render(val, True, color)
            screen.blit(img, (456 - img.get_width(), y))
            rects.append(pygame.Rect(16, y - 2, 448, 24))
        screen.blit(font.render("up/down move - left/right toggle - "
                                "enter launch/quit - esc quit",
                                True, (90, 90, 90)), (24, 340 - 14))
        pygame.display.flip()
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_UP:
                    at = (at - 1) % len(rows)
                elif ev.key == pygame.K_DOWN:
                    at = (at + 1) % len(rows)
                elif ev.key in (pygame.K_LEFT, pygame.K_RIGHT):
                    cycle(rows[at], 1 if ev.key == pygame.K_RIGHT
                          else -1)
                elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                    if rows[at] == "quit":
                        running = False
                    elif rows[at] == "launch":
                        launch()
                    else:
                        cycle(rows[at], 1)
            elif ev.type == pygame.MOUSEBUTTONDOWN and ev.button == 1:
                for i, rect in enumerate(rects):
                    if rect.collidepoint(ev.pos):
                        at = i
                        if rows[i] == "quit":
                            running = False
                        elif rows[i] == "launch":
                            launch()
                        else:
                            cycle(rows[i], 1)
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
