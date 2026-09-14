"""GL render benchmark (milestone H dev tool, not a test).

Usage: .venv/bin/python tools/gl_bench.py [MAP] [WAD] [--frames=N]

Opens a 960x600 GL window (viewer size), builds the Phase 1/2
resources once, then renders N frames cycling spread viewpoints with
glFinish per frame (honest serialized GPU timing, no readback stall
in the loop) and prints FPS plus a software-raster baseline on the
same views for comparison. Needs a display; headless exits 2 with a
note (CI never runs this: FPS asserts would be machine-dependent).
"""

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

WIN_W, WIN_H = 960, 600
WARMUP = 10


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    frames = 240
    for a in sys.argv[1:]:
        if a.startswith("--frames="):
            frames = max(1, int(a.split("=", 1)[1]))
    marker = args[0].upper() if len(args) > 0 else "E1M1"
    basedir = os.path.join(os.path.dirname(__file__), "..")
    default_wad = os.path.join(basedir, "doom.wad")
    if not os.path.exists(default_wad):
        default_wad = os.path.join(basedir, "DOOM1.WAD")
    wad_path = args[1] if len(args) > 1 else default_wad

    from pydoom.mapdata import Map
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(wad_path)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, marker)
    texman.resolve_map(game_map)
    skyflat = texman.flat_num_for_name("F_SKY1")

    import pygame
    pygame.init()
    try:
        pygame.display.set_mode((WIN_W, WIN_H),
                                pygame.OPENGL | pygame.DOUBLEBUF)
        from OpenGL import GL
        ver = GL.glGetString(GL.GL_VERSION)
        if not ver:
            raise ValueError("no GL version")
    except Exception as exc:
        print(f"gl bench: no GL context ({exc}), needs a display")
        pygame.quit()
        return 2
    print(f"gl bench: {marker} ({os.path.basename(wad_path)}) "
          f"GL {ver.decode('ascii', 'replace').split(' (')[0]}")

    from pydoom.glrender import draw as gldraw
    from pydoom.glrender import light as gllight
    from pydoom.glrender import preprocess as glpre
    from pydoom.glrender import textures as gltex
    from pydoom.glrender import upload as glup
    t0 = time.perf_counter()
    walls = glpre.build_walls(game_map, texman, skyflat)
    t_walls = time.perf_counter()
    planes = glpre.build_planes(game_map, skyflat)
    t_planes = time.perf_counter()
    wtex = gltex.build_wall_textures(
        texman, gltex.wall_texnums_used(walls))
    ftex = gltex.build_flat_textures(texman, gltex.flatnums_used(
        planes))
    t_tex = time.perf_counter()
    cmap = gllight.colormap_lut(bytes(wad.cache_lump("COLORMAP")))
    pal = gllight.palette_lut(bytes(wad.read_lump("PLAYPAL")))
    res = glup.GlResources.create(walls, planes, wtex, ftex, cmap,
                                  pal)
    t_up = time.perf_counter()
    fr = gldraw.FrameRenderer(res, WIN_W, WIN_H)
    t_prog = time.perf_counter()
    if res is None:
        print("gl bench: upload failed")
        pygame.quit()
        return 2
    draws = len(res.wall_batches) + 1  # NOTE: batches + planes
    print(f"gl bench: {len(walls.quads)} quads, "
          f"{len(planes.tris)} tris, {len(wtex.order)} walltex, "
          f"{len(ftex.order)} flats, {draws} draws/frame")
    print(f"gl bench: build walls {(t_walls - t0) * 1000:.0f}ms + "
          f"planes {(t_planes - t_walls) * 1000:.0f}ms + "
          f"textures {(t_tex - t_planes) * 1000:.0f}ms + "
          f"upload {(t_up - t_tex) * 1000:.0f}ms + "
          f"programs {(t_prog - t_up) * 1000:.0f}ms")

    from pydoom.renderer import Renderer
    renderer = Renderer(wad, texman)
    things = game_map.things
    step = max(1, len(things) // 12)
    views = []
    for t in things[::step][:12]:
        sub = renderer.sector_at(game_map, t.x << 16, t.y << 16)
        if sub.sector is None:
            continue
        vz = sub.sector.floorheight + 41 * 65536
        for i in range(2):
            views.append((t.x << 16, t.y << 16, vz,
                          (i * 0x80000000) & 0xFFFFFFFF))
    assert views, "no viewpoints"
    for v in views[:WARMUP]:
        fr.render(*v)
    GL.glFinish()
    t0 = time.perf_counter()
    for i in range(frames):
        fr.render(*views[i % len(views)])
    GL.glFinish()
    dt = time.perf_counter() - t0
    print(f"gl bench: {frames} frames in {dt:.2f}s = "
          f"{frames / dt:.1f} fps ({dt / frames * 1000:.2f} "
          f"ms/frame, {WIN_W}x{WIN_H}, glFinish-serialized)")

    t0 = time.perf_counter()
    for i in range(frames):
        x, y, vz, angle = views[i % len(views)]
        renderer.render_view(game_map, x, y, angle, viewz=vz,
                             mobjs=[])
    dt = time.perf_counter() - t0
    print(f"gl bench: software {frames} frames in {dt:.2f}s = "
          f"{frames / dt:.1f} fps ({dt / frames * 1000:.2f} "
          f"ms/frame, 320x200 raster incl. masked/sky)")
    fr.close()
    res.delete()
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
