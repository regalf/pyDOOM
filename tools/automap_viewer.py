"""Top-down automap viewer for a DOOM map.

Usage: python tools/automap_viewer.py [MAP] [WAD] [--frames N] [--shot FILE]

Keys: arrows pan (leaves follow mode, like vanilla), +/- zoom (hold),
0 zoom-to-fit toggle, f follow, g grid, c IDDT cheat cycle (walls/things),
m add mark, x clear marks, PgUp/PgDn switch map, Esc/q quit.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))  # sibling tool modules (bmp)

import pygame

from bmp import save_surface
from pydoom.automap import Automap, load_playpal_from_wad
from pydoom.mapdata import Map
from pydoom.wad import WadFile

WIDTH, HEIGHT = 960, 600
TICRATE = 35


HELP_LINES = [
    "arrows pan | +/- zoom | 0 fit | f follow | g grid | c cheat | m mark | x clear | PgUp/PgDn map | Esc quit",
]


def build(map_name: str, wad_path: str):
    wad = WadFile(wad_path)
    game_map = Map.from_wad(wad, map_name)
    palette = load_playpal_from_wad(wad)
    am = Automap(game_map, WIDTH, HEIGHT, palette)
    return wad, am


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    map_name = args[0].upper() if len(args) > 0 else "E1M1"
    default_wad = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")
    wad_path = args[1] if len(args) > 1 else default_wad
    frames_opt = None
    shot_opt = None
    for a in sys.argv[1:]:
        if a.startswith("--frames="):
            frames_opt = int(a.split("=", 1)[1])
        elif a.startswith("--shot="):
            shot_opt = a.split("=", 1)[1]

    pygame.init()
    screen = pygame.display.set_mode((WIDTH, HEIGHT))
    pygame.display.set_caption(f"pydoom automap - {map_name}")
    # Render to an offscreen canvas: headless-safe (image.save works on it
    # even with the dummy video driver) and identical to the display path.
    canvas = pygame.Surface((WIDTH, HEIGHT))
    try:
        font = pygame.font.SysFont(None, 18)
    except Exception:
        font = None  # e.g. dummy video driver without font support
    clock = pygame.time.Clock()

    wad, am = build(map_name, wad_path)
    maps = wad.list_maps()
    map_idx = maps.index(am.map.marker) if am.map.marker in maps else 0

    tic_acc = 0.0
    frame = 0
    running = True
    while running:
        dt = clock.tick(60) / 1000.0
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif ev.key == pygame.K_LEFT:
                    if am.followplayer:
                        am.toggle_follow()
                    am.key_down_pan(-1, 0)
                elif ev.key == pygame.K_RIGHT:
                    if am.followplayer:
                        am.toggle_follow()
                    am.key_down_pan(1, 0)
                elif ev.key == pygame.K_UP:
                    if am.followplayer:
                        am.toggle_follow()
                    am.key_down_pan(0, 1)
                elif ev.key == pygame.K_DOWN:
                    if am.followplayer:
                        am.toggle_follow()
                    am.key_down_pan(0, -1)
                elif ev.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    am.zoom_hold(zoom_in=True)
                elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    am.zoom_hold(zoom_in=False)
                elif ev.key == pygame.K_0:
                    am.toggle_big()
                elif ev.key == pygame.K_f:
                    am.toggle_follow()
                elif ev.key == pygame.K_g:
                    am.toggle_grid()
                elif ev.key == pygame.K_c:
                    am.cycle_cheat()
                elif ev.key == pygame.K_m:
                    am.add_mark()
                elif ev.key == pygame.K_x:
                    am.clear_marks()
                elif ev.key == pygame.K_PAGEUP:
                    map_idx = (map_idx - 1) % len(maps)
                    am.load_map(Map.from_wad(wad, maps[map_idx]))
                    pygame.display.set_caption(f"pydoom automap - {maps[map_idx]}")
                elif ev.key == pygame.K_PAGEDOWN:
                    map_idx = (map_idx + 1) % len(maps)
                    am.load_map(Map.from_wad(wad, maps[map_idx]))
                    pygame.display.set_caption(f"pydoom automap - {maps[map_idx]}")
            elif ev.type == pygame.KEYUP:
                if ev.key in (
                    pygame.K_LEFT,
                    pygame.K_RIGHT,
                    pygame.K_UP,
                    pygame.K_DOWN,
                ):
                    am.key_up_pan()
                elif ev.key in (
                    pygame.K_PLUS,
                    pygame.K_EQUALS,
                    pygame.K_KP_PLUS,
                    pygame.K_MINUS,
                    pygame.K_KP_MINUS,
                ):
                    am.zoom_release()

        # Vanilla runs the automap ticker at 35 Hz.
        tic_acc += dt
        while tic_acc >= 1.0 / TICRATE:
            am.ticker()
            tic_acc -= 1.0 / TICRATE

        am.draw(canvas)
        screen.blit(canvas, (0, 0))
        if font is not None:
            status = (
                f"{am.map.marker} scale={am.scale_mtof} "
                f"follow={'on' if am.followplayer else 'off'} "
                f"grid={'on' if am.grid else 'off'} cheat={am.cheating}"
            )
            screen.blit(font.render(status, True, (255, 255, 255)), (8, 8))
            screen.blit(font.render(HELP_LINES[0], True, (180, 180, 180)), (8, HEIGHT - 24))
        pygame.display.flip()

        frame += 1
        if frames_opt is not None and frame >= frames_opt:
            if shot_opt:
                save_surface(shot_opt, canvas)
                print(f"saved {shot_opt}")
            running = False

    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
