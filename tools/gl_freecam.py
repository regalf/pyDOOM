"""OpenGL freecam for visual bug reports (noclip fly camera).

Same GL world as the game (walls/planes/sprites/sky + portal PVS),
no sim: WASD/arrows to move, SPACE up, SHIFT down, mouse-drag to
look. P saves a screenshot (with the coords HUD baked in) to
./freecam_shots/ so reports carry exact repro coordinates.

Usage: tools/gl_freecam.py [MAP] [WAD] [--pos=x,y,z,ang] [--frames=N]
"""

import math
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

WIN_W, WIN_H = 960, 600
BASE_SPEED = 450.0  # map units/sec
FAST_MULT = 4.0
TURN_SPEED = math.radians(140.0)


class Camera:
    """Float noclip camera (doom_view convention: 0=east, CCW)."""

    def __init__(self, x: float, y: float, angle_deg: float,
                 viewz: float):
        self.x = x
        self.y = y
        self.angle = math.radians(angle_deg)
        self.viewz = viewz

    @property
    def bam(self) -> int:
        return int(self.angle / (2 * math.pi)
                   * 0x100000000) & 0xFFFFFFFF

    def move(self, forward: float, strafe: float) -> None:
        self.x += (math.cos(self.angle) * forward
                   + math.sin(self.angle) * strafe)
        self.y += (math.sin(self.angle) * forward
                   - math.cos(self.angle) * strafe)


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    opts = [a for a in sys.argv[1:] if a.startswith("--")]
    marker = args[0].upper() if len(args) > 0 else "E1M1"
    basedir = os.path.join(os.path.dirname(__file__), "..")
    default_wad = os.path.join(basedir, "doom.wad")
    if not os.path.exists(default_wad):
        default_wad = os.path.join(basedir, "DOOM1.WAD")
    wad_path = args[1] if len(args) > 1 else default_wad
    frames_opt = [o for o in opts if o.startswith("--frames=")]
    max_frames = int(frames_opt[0].split("=", 1)[1]) if frames_opt else 0
    pos_opt = [o for o in opts if o.startswith("--pos=")]
    pos = (pos_opt[0].split("=", 1)[1].split(",")
           if pos_opt else None)

    from pydoom.mapdata import Map
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(wad_path)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, marker)
    texman.resolve_map(game_map)

    import pygame
    pygame.init()
    try:
        pygame.display.set_mode((WIN_W, WIN_H),
                                pygame.OPENGL | pygame.DOUBLEBUF)
        from OpenGL import GL  # noqa: F401 - context check
    except Exception as exc:  # noqa: BLE001 - needs a display
        print(f"gl freecam: no GL context ({exc}), needs a display")
        pygame.quit()
        return 2

    from types import SimpleNamespace

    from pydoom.glrender import draw as gldraw
    from pydoom.glrender import dynamic as gldyn
    from pydoom.glrender import light as gllight
    from pydoom.glrender import preprocess as glpre
    from pydoom.glrender import sprites as glsprites
    from pydoom.glrender import textures as gltex
    from pydoom.glrender import upload as glup
    from pydoom.glrender import visibility as glvis
    from pydoom.info import spawn_visual
    from pydoom.renderer import (
        SKIP_THING_TYPES,
        Renderer,
        init_sprite_defs,
    )
    t0 = time.time()
    renderer = Renderer(wad, texman)
    dyn = gldyn.DynamicState.take(game_map)
    walls = glpre.build_walls(game_map, texman, renderer.skyflatnum)
    planes = glpre.emit_planes(dyn.fans, game_map, renderer.skyflatnum)
    wtex = gltex.build_wall_textures(
        texman,
        set(gltex.wall_texnums_used(walls))
        | {renderer.skytexture}
        | gldyn.switch_pair_texnums(game_map, texman))
    ftex = gltex.build_flat_textures(texman, gltex.all_flatnums(texman))
    stex = gltex.build_sprite_textures(texman, range(texman.numsprites))
    res = glup.GlResources.create(
        walls, planes, wtex, ftex,
        gllight.colormap_lut(bytes(wad.cache_lump("COLORMAP"))),
        bytes(wad.read_lump("PLAYPAL")), sprite_tex=stex,
        sector_lights=gldyn.sector_light_bases(game_map))
    if res is None:
        print("gl freecam: upload failed")
        pygame.quit()
        return 2
    fr = gldraw.FrameRenderer(res, WIN_W, WIN_H)
    print(f"gl freecam: {marker} {len(walls.quads)} quads, "
          f"{len(planes.tris)} tris in "
          f"{(time.time() - t0) * 1000:.0f}ms")
    if renderer.skytexture in res.wall_textures:
        sky = (res.wall_textures[renderer.skytexture],
               res.wall_info[renderer.skytexture][1])
    else:
        sky = None
    feed = glsprites.SpriteFeed(sprites=init_sprite_defs(
        wad, texman.firstsprite, texman.lastsprite))
    mobjs = []
    for thing in game_map.things:
        if thing.type in SKIP_THING_TYPES:
            continue
        visual = spawn_visual(thing.type)
        if visual is None:
            continue
        sprite, frame, flags = visual
        sub = renderer.sector_at(game_map, thing.x << 16,
                                 thing.y << 16)
        if sub.sector is None:
            continue
        mobjs.append(SimpleNamespace(
            dead=False, state=1, flags=flags, sprite=sprite,
            frame=frame, x=thing.x << 16, y=thing.y << 16,
            z=sub.sector.floorheight,
            angle=((thing.angle % 360) * 0x100000000) // 360,
            sector=sub.sector))
    sec_index = {id(s): i for i, s in enumerate(game_map.sectors)}

    try:
        font = pygame.font.SysFont(None, 20)
    except Exception:  # noqa: BLE001 - HUD stays off without fonts
        font = None
    start_thing = next((t for t in game_map.things if t.type == 1),
                       None)
    if start_thing is not None:
        sub = renderer.sector_at(game_map, start_thing.x << 16,
                                 start_thing.y << 16)
        floor = sub.sector.floorheight / 65536.0
        home = (float(start_thing.x), float(start_thing.y),
                float(start_thing.angle), floor + 41.0)
    else:
        home = (0.0, 0.0, 90.0, 41.0)
    if pos is not None and len(pos) == 4:
        # NOTE: --pos=x,y,z,ang (Camera takes angle before viewz).
        home = (float(pos[0]), float(pos[1]),
                float(pos[3]), float(pos[2]))
    cam = Camera(*home)
    show_things = True
    shots = os.path.join(os.getcwd(), "freecam_shots")
    os.makedirs(shots, exist_ok=True)
    print("gl freecam controls: WASD/arrows move, mouse-drag looks, "
          "SPACE up, SHIFT down, CTRL fast, R reset, T things, "
          "P screenshot, ESC quits")
    print(f"gl freecam: screenshots -> {shots}/")

    clock = pygame.time.Clock()
    fps_ema = 60.0
    running, frame = True, 0
    want_shot = False
    while running:
        dt = min(clock.tick(60) / 1000.0, 0.1)
        fps_ema += (1.0 / max(dt, 1e-6) - fps_ema) * 0.05
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key == pygame.K_ESCAPE:
                    running = False
                elif ev.key == pygame.K_r:
                    cam = Camera(*home)
                elif ev.key == pygame.K_t:
                    show_things = not show_things
                elif ev.key in (pygame.K_p, pygame.K_F12):
                    want_shot = True
            elif ev.type == pygame.MOUSEMOTION and ev.buttons[0]:
                cam.angle += -ev.rel[0] * 0.004
        pressed = pygame.key.get_pressed()
        cam.angle %= 2 * math.pi
        if pressed[pygame.K_LEFT]:
            cam.angle += TURN_SPEED * dt
        if pressed[pygame.K_RIGHT]:
            cam.angle -= TURN_SPEED * dt
        speed = BASE_SPEED * (FAST_MULT if pressed[pygame.K_LCTRL]
                              or pressed[pygame.K_RCTRL] else 1.0)
        fwd = ((pressed[pygame.K_w] or pressed[pygame.K_UP])
               - (pressed[pygame.K_s] or pressed[pygame.K_DOWN]))
        strafe = pressed[pygame.K_d] - pressed[pygame.K_a]
        cam.move(fwd * speed * dt, strafe * speed * dt)
        if pressed[pygame.K_SPACE] or pressed[pygame.K_PAGEUP]:
            cam.viewz += speed * dt
        if (pressed[pygame.K_LSHIFT] or pressed[pygame.K_RSHIFT]
                or pressed[pygame.K_PAGEDOWN]):
            cam.viewz -= speed * dt
        cam.viewz = max(-1024.0, min(2048.0, cam.viewz))

        try:
            sub = renderer.sector_at(game_map, int(cam.x * 65536),
                                     int(cam.y * 65536))
            start = (sec_index.get(id(sub.sector))
                     if sub.sector is not None else None)
        except Exception:  # noqa: BLE001 - void edge shows all
            start = None
        if start is None:
            nvis = len(game_map.sectors)
            res.upload_visible([1] * nvis)
            live = mobjs
        else:
            mask = glvis.visible_mask(game_map, start)
            res.upload_visible(mask)
            vis = {i for i, v in enumerate(mask) if v}
            nvis = len(vis)
            live = [mo for mo in mobjs
                    if sec_index.get(id(mo.sector)) in vis]
        bbs = (feed.project(live, int(cam.x * 65536),
                            int(cam.y * 65536), cam.bam, texman)
               if show_things else [])
        try:
            fr.render(int(cam.x * 65536), int(cam.y * 65536),
                      int(cam.viewz * 65536), cam.bam,
                      sprites=bbs, sky=sky)
            fr.blit_world()
        except Exception as exc:  # noqa: BLE001 - never die on GL
            print(f"gl freecam: render failed: {exc}")
            continue
        if font is not None:
            import numpy as np
            hud = (f"{marker} x={cam.x:.0f} y={cam.y:.0f} "
                   f"z={cam.viewz:.0f} "
                   f"a={math.degrees(cam.angle) % 360:.0f} "
                   f"vis={nvis}/{len(game_map.sectors)} "
                   f"{fps_ema:.0f}fps")
            help_line = ("WASD/arrows move, drag look, SPACE up, "
                         "SHIFT down, CTRL fast, R reset, T things, "
                         "P shot, ESC quit")
            lines = [(hud, (255, 255, 255), 255, 8, 8),
                     (help_line, (180, 180, 180), 255, 8, WIN_H - 28)]
            for text, rgb, alpha, x, y in lines:
                img = font.render(text, True, rgb)
                w, h = img.get_width(), img.get_height()
                arr = np.frombuffer(
                    pygame.image.tobytes(img, "RGBA"),
                    dtype=np.uint8).reshape(h, w, 4).copy()
                arr[:, :, 3] = (arr[:, :, 3].astype(np.uint16)
                                * alpha // 255).astype(np.uint8)
                tex_id = fr.upload_text(arr.tobytes(), w, h)
                try:
                    fr.draw_text_quad(tex_id, x, y, w, h)
                finally:
                    fr.delete_text(tex_id)
        cx, cy = WIN_W // 2, WIN_H // 2
        fr.draw_automap([(cx - 9, cy, cx - 3, cy, 140, 140, 140),
                         (cx + 3, cy, cx + 9, cy, 140, 140, 140),
                         (cx, cy - 9, cx, cy - 3, 140, 140, 140),
                         (cx, cy + 3, cx, cy + 9, 140, 140, 140)])
        if want_shot:
            # NOTE: capture pre-flip (the back buffer holds this exact
            # frame; post-flip it would be a stale buffer).
            want_shot = False
            tag = (f"{marker}_x{cam.x:.0f}_y{cam.y:.0f}_"
                   f"z{cam.viewz:.0f}_"
                   f"a{math.degrees(cam.angle) % 360:.0f}")
            try:
                shot = fr.readback_window()
                surf = pygame.surfarray.make_surface(
                    shot.swapaxes(0, 1))
                path = os.path.join(shots, tag + ".png")
                pygame.image.save(surf, path)
                print(f"gl freecam: saved {path}")
            except Exception as exc:  # noqa: BLE001 - report only
                print(f"gl freecam: screenshot failed: {exc}")
        pygame.display.flip()
        frame += 1
        if max_frames and frame >= max_frames:
            running = False
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
