"""Interactive noclip walkthrough viewer (pygame-ce).

Usage: python tools/doom_view.py [MAP] [WAD]

Keys: W/A/S/D or arrows move/turn, mouse looks, Shift runs,
E uses doors/switches, 1-7 weapons, TAB automap, M sound, Esc menu.
Cheats (typed, always on like vanilla): iddqd idkfa/idfa idclip
idclev11 idmus11 iddt idbeholdv.
Dev keys (only with --debug): N noclip, F freeze AI, X AI info,
PgUp/PgDn switch map, G grabs/releases the mouse.
Demos (fixed-step, checksum-verified): --record=FILE logs inputs,
--play=FILE replays them (regression: matching checksums agree).
Vanilla demos: --record-demo=FILE writes a version-109 .lmp,
--playdemo=FILE plays one back (level transitions included),
--timedemo=FILE plays it fast with no drawing and reports stats.
Idle title falls into the IWAD demo loop (any key stops it).
Hidden test hook: --frames=N quits after N frames (headless smoke test).

Movement uses the real physics (P_TryMove/P_SlideMove): walls block,
angled walls slide, steps up to 24 units climb. No gravity, AI,
weapons or thing collision yet.
"""

import math
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pygame

from pydoom import combat
from pydoom import cheats
from pydoom import demo
from pydoom import ext
from pydoom import flow
from pydoom import interm
from pydoom import menu
from pydoom import oplmusic
from pydoom import p_user
from pydoom import ticcmd
from pydoom import weapons
from pydoom import audio
from pydoom.automap import Automap
from pydoom.ai import AIContext, check_sight
from pydoom.doors import World
from pydoom.info import MT_INDEX, MT_NAMES, STATE_INDEX
from pydoom.info import MF_FLAGS as _MF_FLAGS
from pydoom.mapdata import Map
from pydoom.mobjs import ThingIndex, refresh_sector, spawn_map
from pydoom.mobjs import level_totals, set_mobj_state, think_mobj
from pydoom.mobjs import sweep_dead, tick_mobj_state
from pydoom.mobjs import xy_movement
from pydoom.palette import NUM_PALETTES, load_playpal, load_playpal_index
from pydoom.physics import MF_NOCLIP, Physics
from pydoom.pickup import collect_touched
from pydoom.player import (
    CF_NOCLIP,
    PW_ALLMAP,
    PW_INFRARED,
    PlayerState,
    WP_CHAINGUN,
    WP_CHAINSAW,
    palette_index,
)
from pydoom.statusbar import FaceState, draw_status_bar, update_face
from pydoom.renderer import SCREENHEIGHT, SCREENWIDTH, Renderer
from pydoom.textures import TextureManager
from pydoom.wad import WadFile
from pydoom.wipe import MeltWipe

WIN_W, WIN_H = 960, 600

# NOTE: E1TEXT (d_englsh.h): the episode-one payoff, shown on victory.
_E1TEXT_LINES = (
    "Once you beat the big badasses and",
    "clean out the moon base you're supposed",
    "to win, aren't you? Aren't you? Where's",
    "your fat reward and ticket home?",
    "",
    "It stinks like rotten meat, but looks",
    "like the lost Deimos base. Looks like",
    "you're stuck on The Shores of Hell.",
    "The only way out is through.",
)
TICRATE = 35
WALK_SPEED = 7.0  # map units per tic
RUN_SPEED = 13.0
# NOTE: key turning now rides ticcmd ANGLETURN units (640/1280/320 with
# the SLOWTURNTICS ramp, like vanilla); radians live in ticcmd.py.
# NOTE: mouse radians/px rides the options slider (0.0004 + idx*6e-4,
# so idx 4 lands on the old 0.0028); the const below is history.
MOUSE_SENS = 0.0028  # radians per pixel (default slider position)
VIEWHEIGHT_ABOVE_FLOOR = 41.0


class Camera:
    """Float-precision noclip camera; the engine still gets fixed-point."""

    def __init__(self, x: float, y: float, angle_deg: float, viewz: float):
        self.x = x
        self.y = y
        self.angle = math.radians(angle_deg)  # 0=east, CCW, y=north
        self.viewz = viewz

    @property
    def bam(self) -> int:
        return int(self.angle / (2 * math.pi) * 0x100000000) & 0xFFFFFFFF

    def turn(self, delta: float) -> None:
        self.angle += delta

    def move(self, forward: float, strafe: float) -> None:
        # Strafe thrusts at angle-90deg, like P_MovePlayer.
        self.x += math.cos(self.angle) * forward + math.sin(self.angle) * strafe
        self.y += math.sin(self.angle) * forward - math.cos(self.angle) * strafe


class _ReplayKeys:
    """Virtual pressed-set for demo playback (get_pressed stand-in)."""

    def __init__(self, pressed) -> None:
        self._set = set(pressed)

    def __getitem__(self, key: int) -> bool:
        return key in self._set


def _move_keys(mv) -> list:
    """Movement-intent flags back to the canonical held keys."""
    fwd, back, left, right, turnl, turnr, run, space = mv
    keys = []
    if fwd:
        keys.append(pygame.K_w)
    if back:
        keys.append(pygame.K_s)
    if left:
        keys.append(pygame.K_a)
    if right:
        keys.append(pygame.K_d)
    if turnl:
        keys.append(pygame.K_LEFT)
    if turnr:
        keys.append(pygame.K_RIGHT)
    if run:
        keys.append(pygame.K_LSHIFT)
    if space:
        keys.append(pygame.K_SPACE)
    return keys


MAP_SONGS = {f"E{ep}M{i}": f"D_E{ep}M{i}" for ep in (1, 2, 3)
             for i in range(1, 10)}
TITLE_SONG, INTER_SONG, FINALE_SONG = "D_INTRO", "D_INTER", "D_VICTOR"


def song_for_map(marker: str) -> str:
    """ExMy music lump (vanilla D_E1M1..D_E3M9; E4 reuses, idmus too)."""
    return MAP_SONGS.get(marker.upper(), "D_E1M1")


def step_ui_clock(acc: float, dt: float) -> tuple:
    """UI-tic accumulator step (pure, unit tested).

    Menu skull and intermission tally are 35Hz tickers (vanilla
    M_Ticker/WI_Ticker rate): feeding them per display frame ran the
    skull/tally ~8x fast at 240fps vs 30fps. Returns (new_acc, n):
    fire n ticks this frame; identical wall-clock spans yield identical
    tick counts whatever the frame subdivision.
    """
    acc += dt
    n = 0
    step = 1.0 / TICRATE
    while acc >= step:
        acc -= step
        n += 1
    return acc, n


def step_wipe_clock(melt, acc: float, dt: float) -> tuple:
    """Advance the melt accumulator one frame (pure part, unit tested).

    Returns (frame_or_None, new_acc, landed): landed True ends the
    wipe (caller switches to wipe_after). A shape-broken melt frame
    also lands immediately instead of presenting tiling/garbage (or
    crashing the blit/upload downstream).
    """
    acc += dt * TICRATE
    steps, acc = int(acc), acc - int(acc)
    frame = melt.tick(steps) if steps else melt.frame
    if frame is None:
        return None, acc, True
    if getattr(frame, "shape", None) != (SCREENHEIGHT, SCREENWIDTH) \
            or getattr(frame, "dtype", None) != np.uint8:
        return None, acc, True
    return frame, acc, False


def _dump_wipe_frame(tag: str, fb) -> None:
    """Save one melt framebuffer as .npy for post-mortem (debug only).

    Exact index arrays (not PNGs) so bar positions/duplication can be
    dissected byte for byte after a bad wipe. Best-effort: never raises.
    """
    try:
        import os
        if fb is None or getattr(fb, "shape", None) != (200, 320):
            return
        path = f"/tmp/opencode/wipe-{os.getpid()}"
        os.makedirs(path, exist_ok=True)
        have = len([f for f in os.listdir(path)
                    if f.endswith(".npy")])
        if have > 40:  # NOTE: cap disk use (unlimited fps spams frames)
            return
        np.save(os.path.join(path, f"{tag}.npy"),
                np.ascontiguousarray(fb, dtype=np.uint8))
    except Exception:  # noqa: BLE001 - diagnostics never break play
        pass


def video_geom(settings, want_gl: bool) -> tuple:
    """(w, h, flags, display) for the current video settings.

    Touches nothing (pure apart from guarded desktop queries). GL
    renders natively at the chosen resolution; software renders
    320x200 and only scales the window. Fullscreen-exclusive exists on
    Windows only (callers on other platforms never offer it, and cfg
    validation already maps strays away); borderless fills the chosen
    screen on every platform. display is the SDL display index: it is
    what keeps fullscreen/borderless off the wrong monitor (plain
    set_mode lets GNOME put the window wherever it likes).
    """
    from pydoom.menu import display_count
    screens = max(1, display_count())
    idx = min(max(int(settings.display_index), 0), screens - 1)
    if want_gl:
        parsed = menu.parse_resolution(settings.gl_resolution)
        w, h = parsed if parsed is not None else (960, 600)
    else:
        w, h = menu.sw_window_size(settings.sw_scale)
    mode = settings.display_mode
    if mode == "fullscreen" and sys.platform == "win32":
        return w, h, pygame.FULLSCREEN, idx
    if mode == "borderless":
        dw, dh = desktop_size_on(idx)
        return dw, dh, pygame.NOFRAME, idx
    return w, h, 0, idx


def desktop_size_on(idx: int) -> tuple:
    """Desktop size of screen idx, guarded (headless-safe fallback)."""
    try:
        sizes = pygame.display.get_desktop_sizes()
        if sizes:
            w, h = sizes[min(max(idx, 0), len(sizes) - 1)]
            return int(w), int(h)
    except Exception:  # noqa: BLE001 - dummy video has no desktop
        pass
    return 960, 600


def set_noclip(on: bool, player_mo, cam, phys) -> None:
    """Shared N-key/idclip toggle: MF_NOCLIP flag plus a floor resync
    when clipping back in (so the body never hovers over the void)."""
    if on:
        player_mo.flags |= MF_NOCLIP
    else:
        player_mo.flags &= ~MF_NOCLIP
        player_mo.x, player_mo.y = (int(cam.x * 65536),
                                    int(cam.y * 65536))
        res = phys.check_position(player_mo, player_mo.x, player_mo.y)
        if res.ok:
            player_mo.floorz, player_mo.ceilingz = (res.floorz,
                                                    res.ceilingz)
            player_mo.z = res.floorz
        refresh_sector(player_mo, phys)


def main() -> int:
    global WIN_W, WIN_H
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    frames_opt = None
    profile_opt = None  # --profile=N: print the v2 profiler table, quit
    skill = "normal"
    fast = False
    debug = False  # dev keys (N/F/X/PgUp/...) stay behind this flag
    extra_hud = False  # --extra-hud: translucent readout block
    dynlights_cli = False  # --dynlights: v2 muzzle/projectile lights
    brightmaps_cli = False  # --brightmaps: v2 lamp exemption
    texfilter_cli = None  # --texture-filter=: v2 linear walls/flats
    rec_path = None  # --record=FILE: log per-frame inputs (fixed dt)
    play_path = None  # --play=FILE: replay them (regression demos)
    rec_demo_path = None  # --record-demo=FILE: vanilla-format .lmp
    play_demo_path = None  # --playdemo=FILE: play a vanilla .lmp
    timedemo = False  # --timedemo=FILE: play fast, no draw, report
    checksum_path = None  # --dump-checksums=FILE: per-tic sim trace
    nomonsters = False  # demo header / vanilla -nomonsters spawn filter
    respawn = False  # --respawn: monsters return (any skill, like vanilla)
    kinematic = False  # --kinematic: legacy camera mover (milestone B)
    video_cli = None  # --video-api=software|opengl (overrides pydoom.cfg)
    no_mods = False  # --no-mods: master mod-loader switch (launcher)
    mods_on: list = []  # --mod-on=ID repeatable (launcher overrides)
    mods_off: list = []  # --mod-off=ID repeatable (launcher overrides)
    for a in sys.argv[1:]:
        if a.startswith("--frames="):
            frames_opt = int(a.split("=", 1)[1])
        elif a.startswith("--profile="):
            profile_opt = int(a.split("=", 1)[1])
        elif a.startswith("--record="):
            rec_path = a.split("=", 1)[1]
        elif a.startswith("--play="):
            play_path = a.split("=", 1)[1]
        elif a.startswith("--record-demo="):
            rec_demo_path = a.split("=", 1)[1]
        elif a.startswith("--playdemo="):
            play_demo_path = a.split("=", 1)[1]
        elif a.startswith("--timedemo="):
            play_demo_path = a.split("=", 1)[1]
            timedemo = True
        elif a.startswith("--dump-checksums="):
            checksum_path = a.split("=", 1)[1]
        elif a == "--debug":
            debug = True
        elif a == "--extra-hud":
            extra_hud = True
        elif a == "--dynlights":
            dynlights_cli = True
        elif a == "--brightmaps":
            brightmaps_cli = True
        elif a.startswith("--texture-filter="):
            texfilter_cli = a.split("=", 1)[1].lower()
        elif a == "--kinematic":
            kinematic = True  # NOTE: legacy camera mover for A/B compare
        elif a.startswith("--skill="):
            skill = a.split("=", 1)[1].lower()
            from pydoom.mobjs import SKILL_BITS
            if skill not in SKILL_BITS:
                raise SystemExit(f"unknown skill {skill} "
                                 f"(baby/easy/normal/hard/nightmare)")
        elif a == "--fast":
            fast = True
        elif a == "--respawn":
            respawn = True
        elif a == "--nomonsters":
            nomonsters = True  # NOTE: vanilla -nomonsters spawn filter
        elif a.startswith("--video-api="):
            video_cli = a.split("=", 1)[1].lower()
        elif a == "--no-mods":
            no_mods = True  # NOTE: mod loader master off (launcher)
        elif a.startswith("--mod-on="):
            mods_on.append(a.split("=", 1)[1])
        elif a.startswith("--mod-off="):
            mods_off.append(a.split("=", 1)[1])
    audio.verbose = debug  # NOTE: terminal chatter needs --debug
    oplmusic.verbose = debug
    map_name = args[0].upper() if len(args) > 0 else "E1M1"
    basedir = os.path.join(os.path.dirname(__file__), "..")
    default_wad = os.path.join(basedir, "doom.wad")
    if not os.path.exists(default_wad):  # NOTE: shareware fallback
        default_wad = os.path.join(basedir, "DOOM1.WAD")
    wad_path = args[1] if len(args) > 1 else default_wad
    msettings = menu.Settings()
    menu.settings_load(menu.CONFIG_PATH, msettings)
    if dynlights_cli:
        msettings.dynlights = True  # NOTE: CLI forces the cfg option on
    if brightmaps_cli:
        msettings.brightmaps = True  # NOTE: CLI forces the cfg option on
    if texfilter_cli in ("nearest", "linear"):
        msettings.texture_filter = texfilter_cli
    # NOTE: wanted renderer backend (CLI overrides pydoom.cfg); the
    # effective one lands after the window dance (try_init below).
    # Legacy --video-api=opengl means v1 (pre-v2 scripts keep working).
    want_api = video_cli if video_cli is not None else msettings.video_api
    if want_api == "opengl":
        want_api = "openglv1"
    if want_api not in menu.VIDEO_APIS:
        print(f"video: unknown api {want_api!r}, using software")
        want_api = "software"
    # NOTE: vanilla demo compat (.lmp playback/record, attract loop)
    # is experimental and off by default; enable with `demos 1` in
    # pydoom.cfg (PYDOOM_DEMOS=1 covers the automated test harness).
    demos_enabled = msettings.demos or os.getenv("PYDOOM_DEMOS") == "1"
    demo_header = None  # parsed .lmp header driving this run, if any
    if rec_demo_path is not None and play_demo_path is not None:
        raise SystemExit("cannot --record-demo and --playdemo together")
    if (play_demo_path is not None or timedemo
            or rec_demo_path is not None) and not demos_enabled:
        print("demo: disabled (vanilla demo compat is experimental "
              "and off by default; set `demos 1` in pydoom.cfg to "
              "enable), booting normally")
        play_demo_path = None
        timedemo = False
        rec_demo_path = None
    if play_demo_path is not None:
        try:
            with open(play_demo_path, "rb") as f:
                demo_blob = f.read()
            demo_header = demo.DemoHeader.from_bytes(demo_blob)
        except (OSError, ValueError) as exc:
            print(f"demo: refused ({exc}), booting normally")
            demo_header = None
            play_demo_path = None
            timedemo = False
        else:
            if not demo_header.single_player():
                print("demo: refused (multiplayer/deathmatch "
                      "streams need netgame), booting normally")
                demo_header = None
                play_demo_path = None
                timedemo = False
            else:
                skill = demo_header.skill_name()
                fast = bool(demo_header.fast)
                respawn = bool(demo_header.respawn)
                nomonsters = bool(demo_header.nomonsters)
                map_name = demo_header.marker()
                print(f"demo: {play_demo_path} skill={skill} map={map_name}"
                      f"{'+FAST' if fast else ''}"
                      f"{'+RESPAWN' if respawn else ''}"
                      f"{'+NOMONSTERS' if nomonsters else ''}")

    wad = WadFile(wad_path)
    from pydoom import mission as _mission
    from pydoom import player as _player
    game_mission = _mission.detect(wad)
    _player.GAMEMODE = game_mission
    print(f"iwad: {os.path.basename(wad_path)} ({game_mission})")
    if demo_header is not None:  # NOTE: mission clamps the demo map
        map_name = demo_header.marker(game_mission)
    texman = TextureManager(wad)
    # NOTE: build tag in the HUD, so screenshots name their code.
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    from pydoom.version import get_version
    ver = get_version(root)
    palette_lut = np.array(load_playpal(wad.read_lump("PLAYPAL")),
                           dtype=np.uint8)
    # NOTE: damage/bonus/suit flash palettes (ST_doPaletteStuff).
    _playpal_data = wad.read_lump("PLAYPAL")
    palette_luts = {
        i: np.array(load_playpal_index(_playpal_data, i), dtype=np.uint8)
        for i in range(NUM_PALETTES)
    }
    renderer = Renderer(wad, texman)
    maps = wad.list_maps()

    gl_info = None  # GL version string when the opengl path is live
    gl_live = False  # set after the window dance (try_init below)
    gl_res = None  # GlResources for the current map (or None)
    gl_frame = None  # FrameRenderer bound to gl_res (or None)
    gl_feed = None  # SpriteFeed for live-mobj billboards (or None)
    gl_dyn = None  # DynamicState: per-frame sector-move sync (or None)
    gl_geo = None  # incremental GEO cache: walls/planes + pos maps

    def drop_gl_resources() -> None:
        """Free GPU resources, safe on live or dead contexts alike.

        Called BEFORE set_mode kills the context when possible (clean
        GPU free); refresh_gl_resources repeats it after (no-op).
        """
        nonlocal gl_res, gl_frame, gl_feed, gl_dyn, gl_geo
        if gl_frame is not None:
            try:
                gl_frame.close()
            except Exception:  # noqa: BLE001, S110 - teardown never fail
                pass
            gl_frame = None
        gl_text_cache.clear()  # NOTE: ids died with the old frame
        if gl_res is not None:
            try:
                gl_res.delete()
            except Exception:  # noqa: BLE001 - dead context teardown
                pass
            gl_res = None
        gl_feed = None
        gl_dyn = None
        gl_geo = None

    def refresh_gl_resources(game_map) -> None:
        """(Re)build GPU resources for game_map (milestone H).

        Opengl path only: software, headless, timedemo and --frames
        runs never enter (gl_live False). Never raises: any failure
        drops back to software presentation mid-session.
        """
        nonlocal gl_res, gl_frame, gl_feed, gl_dyn, gl_geo
        drop_gl_resources()
        if not gl_live or gl_info is None:
            return
        try:
            import time
            from types import SimpleNamespace

            from pydoom.glrender import draw as gldraw
            from pydoom.glrender import dynamic as gldyn
            from pydoom.glrender import light as gllight
            from pydoom.glrender import preprocess as glpre
            from pydoom.glrender import sprites as glsprites
            from pydoom.glrender import textures as gltex
            from pydoom.glrender import upload as glup
            from pydoom.renderer import init_sprite_defs
            t0 = time.time()
            gl_dyn = gldyn.DynamicState.take(game_map)
            walls = glpre.build_walls(game_map, texman,
                                      renderer.skyflatnum)
            planes = glpre.emit_planes(gl_dyn.fans, game_map,
                                       renderer.skyflatnum)
            wtex = gltex.build_wall_textures(
                texman,
                set(gltex.wall_texnums_used(walls))
                | {renderer.skytexture}
                | gldyn.switch_pair_texnums(game_map, texman))
            ftex = gltex.build_flat_textures(
                texman, gltex.all_flatnums(texman))  # NOTE: ALL
            # decodable flats (donut pic-swaps land on any floorpic:
            # ~100x4KB is trivial, so missing-layer drops can never
            # happen at runtime)
            stex = gltex.build_sprite_textures(
                texman, range(texman.numsprites))
            cmap = gllight.colormap_lut(bytes(
                wad.cache_lump("COLORMAP")))
            pal = bytes(wad.read_lump("PLAYPAL"))
            gl_res = glup.GlResources.create(
                walls, planes, wtex, ftex, cmap, pal,
                sprite_tex=stex,
                sector_lights=gldyn.sector_light_bases(game_map))
            gl_feed = glsprites.SpriteFeed(sprites=init_sprite_defs(
                wad, texman.firstsprite, texman.lastsprite))
            if gl_res is not None:
                # NOTE: Openglv2 (glrenderer2) when effectively live;
                # win_backend (not the wanted setting) drives this so a
                # CLI override and a GL fallback cannot disagree.
                if win_backend == "openglv2":
                    # NOTE: same pixels through the caching facade +
                    # profiler (v1 stays frozen); linear filtering is
                    # a v2-only construction flag (default NEAREST).
                    from pydoom.glrenderer2 import renderer as gldraw2
                    gl_frame = gldraw2.FrameRenderer2(
                        gl_res, WIN_W, WIN_H,
                        linear=(msettings.texture_filter == "linear"))
                else:
                    gl_frame = gldraw.FrameRenderer(gl_res, WIN_W, WIN_H)
            gl_geo = SimpleNamespace(
                walls=walls, planes=planes,
                seg_quadpos=glpre.seg_quad_positions(walls),
                sec_tripos=glpre.sec_tri_positions(planes))
            dt = (time.time() - t0) * 1000
            if gl_res is None or gl_frame is None:
                print("gl resources: upload failed "
                      "(software-presented)")
            else:
                print(f"gl resources: {len(walls.quads)} quads, "
                      f"{len(planes.tris)} tris, "
                      f"{len(wtex.order)} walltex, "
                      f"{len(ftex.order)} flats in {dt:.0f}ms")
        except Exception as exc:  # noqa: BLE001 - GL never breaks play
            print(f"gl resources: {exc} (software-presented)")
            gl_res = None
            gl_frame = None
            gl_feed = None
            gl_dyn = None
            gl_geo = None

    def video_summary() -> str:
        """HUD line for the current video state (all-caps: STCFN-safe)."""
        if msettings.video_api != "software":
            head = f"{msettings.video_api.upper()} {WIN_W}X{WIN_H}"
        else:
            head = f"SOFTWARE {msettings.sw_scale * 100}%"
        return (f"{head} {msettings.display_mode.upper()}"
                f" SCR{min(max(msettings.display_index, 0), 8) + 1}"
                f" {msettings.fps_limit or 'UNLIMITED'}FPS"
                f" VSYNC {'ON' if msettings.vsync else 'OFF'}")

    def apply_video_settings() -> None:
        """Staged APPLY from the video menu (menu ("video_changed",)).

        Same-backend geometry only (fps/vsync/mode/screen/show-fps):
        backend and sizes are boot-time settings (launcher VIDEO tab),
        since live window recreation proved unreliable on some drivers.
        GPU teardown runs BEFORE set_mode kills the context; refresh
        rebuilds after. Never raises: any failure keeps a presenting
        window + HUD message.
        """
        global WIN_W, WIN_H
        nonlocal screen, gl_live, gl_info, amap, message, message_tics
        nonlocal win_backend
        from pydoom.glrender import state as _glstate
        want_gl = msettings.video_api != "software"
        drop_gl_resources()
        w, h, flags, disp = video_geom(msettings, want_gl)
        vsync = int(bool(msettings.vsync))
        new_screen = None
        new_gl_info = None
        if want_gl:
            try:
                new_screen = _glstate.positioned_set_mode(
                    (w, h), flags | pygame.OPENGL | pygame.DOUBLEBUF,
                    disp, vsync)
            except Exception:  # noqa: BLE001 - GL unavailable
                new_screen = None
            if new_screen is not None:
                new_gl_info = _glstate._gl_version()
                if new_gl_info is None:
                    new_screen = None
        else:
            try:
                new_screen = _glstate.positioned_set_mode(
                    (w, h), flags, disp, vsync)
            except Exception:  # noqa: BLE001 - keep old window
                new_screen = None
        if new_screen is None:
            # NOTE: set_mode failed, old window+context survived:
            # rebuild what was dropped and report.
            refresh_gl_resources(game_map)
            message = ("OPENGL UNAVAILABLE" if want_gl
                       else "VIDEO MODE FAILED")
            message_tics = 3 * TICRATE
            return
        screen = new_screen
        WIN_W, WIN_H = screen.get_width(), screen.get_height()
        pygame.display.set_caption(f"pydoom - {game_map.marker}")
        pygame.mouse.set_visible(False)
        try:
            pygame.event.set_grab(True)
        except Exception:  # noqa: BLE001 - some WMs refuse grabs
            pass
        if want_gl and new_gl_info is not None:
            gl_live = True
            gl_info = new_gl_info
            win_backend = msettings.video_api
            refresh_gl_resources(game_map)
        else:
            gl_live = False
            win_backend = "software"
        if want_gl and gl_frame is None:
            _gl_fallback_to_software("OPENGL UNAVAILABLE")
        else:
            message = video_summary()
            message_tics = 3 * TICRATE
        menu.settings_save(menu.CONFIG_PATH, msettings)
        amap = None
        print(f"video: {message} ({WIN_W}x{WIN_H})")
        modmgr.set_backend(win_backend)  # NOTE: re-gate backend mods

    def _gl_fallback_to_software(note: str) -> None:
        """After a dead GL rebuild: software window at SW geometry."""
        global WIN_W, WIN_H
        nonlocal screen, gl_live, win_backend
        nonlocal message, message_tics
        from pydoom.glrender import state as _glstate
        w2, h2, f2, d2 = video_geom(msettings, False)
        try:
            screen = _glstate.positioned_set_mode((w2, h2), f2, d2)
            win_backend = "software"
        except Exception:  # noqa: BLE001 - GL window kept; the
            pass  # per-frame heal below retries the downgrade
        WIN_W, WIN_H = screen.get_width(), screen.get_height()
        gl_live = False
        modmgr.set_backend(win_backend)  # NOTE: re-gate backend mods
        message = note
        message_tics = 3 * TICRATE

    gl_text_cache: dict = {}  # text key -> (tex_id, w, h)

    def gl_text_line(text: str, rgb, alpha: int):
        """Upload a font line once (RGBA, surface alpha baked in)."""
        import numpy as np
        img = font.render(text, True, rgb)
        w, h = img.get_width(), img.get_height()
        arr = np.frombuffer(pygame.image.tobytes(img, "RGBA"),
                            dtype=np.uint8).reshape(h, w, 4).copy()
        arr[:, :, 3] = (arr[:, :, 3].astype(np.uint16)
                        * alpha // 255).astype(np.uint8)
        return gl_frame.upload_text(arr.tobytes(), w, h), w, h

    def gl_draw_version() -> None:
        """Top-right build tag (always on, like software)."""
        if font is None or gl_frame is None:
            return
        key = "version"
        if key not in gl_text_cache:
            tex_id, w, h = gl_text_line(f"v{ver}", (255, 255, 255),
                                        96)
            gl_text_cache[key] = (tex_id, w, h)
        tex_id, w, h = gl_text_cache[key]
        gl_frame.draw_text_quad(tex_id, WIN_W - 8 - w, 8, w, h)

    def gl_finale_text() -> None:
        """Centered finale lines (software positions mirrored)."""
        if font is None or gl_frame is None:
            return
        big = font.render("EPISODE 1 COMPLETE", True, (255, 255, 0))
        lines = [("EPISODE 1 COMPLETE", big, WIN_H // 2 - 130,
                  255)]
        for i, text_line in enumerate(_E1TEXT_LINES):
            small = font.render(text_line, True, (200, 200, 200))
            lines.append((text_line, small, WIN_H // 2 - 90 + i * 20,
                          255))
        sub = font.render("ANY KEY: TITLE", True, (255, 255, 255))
        lines.append(("ANY KEY: TITLE", sub, WIN_H // 2 + 130, 255))
        for key, img, y, alpha in lines:
            ck = ("finale", key)
            if ck not in gl_text_cache:
                w, h = img.get_width(), img.get_height()
                import numpy as np
                arr = np.frombuffer(
                    pygame.image.tobytes(img, "RGBA"),
                    dtype=np.uint8).reshape(h, w, 4).copy()
                arr[:, :, 3] = (arr[:, :, 3].astype(np.uint16)
                                * alpha // 255).astype(np.uint8)
                gl_text_cache[ck] = (gl_frame.upload_text(
                    arr.tobytes(), w, h), w, h)
            tex_id, w, h = gl_text_cache[ck]
            gl_frame.draw_text_quad(tex_id, (WIN_W - w) // 2, y, w, h)

    def hud_lines() -> list:
        """[(text, rgb, alpha, x, y)] for the readout block + help
        line, shared by the software blits and the GL text quads
        (same strings, colors, ghost alpha and positions both ways).

        Read-only w.r.t. the sim (f-strings over live state only).
        """
        lines = []
        if gamestate in ("level", "menu", "wipe") and has_level:
            # NOTE: readout block (coords, AI, fps, version) shows with
            # --extra-hud (or --debug, as before) or the Show FPS video
            # setting, translucent, below the red message line when one
            # is up.
            show_hud = extra_hud or debug or msettings.show_fps
            hy = 56 if (show_hud and message is not None
                        and msettings.messages) else 8
            if show_hud:
                hud = (f"{game_map.marker} x={cam.x:.0f} y={cam.y:.0f} "
                       f"a={math.degrees(cam.angle) % 360:.0f} "
                       f"{fps_ema:.0f}fps "
                       f"{'noclip' if noclip else 'clip'} "
                       f"AI:{'FROZEN' if ctx.ai_frozen else 'LIVE'} "
                       f"{skill.upper()}{'+FAST' if fast else ''}")
                if debug:
                    # NOTE: song-thread health for low-fps music reports.
                    ms = audio.music_status()
                    hud += (f" MUS:{ms.get('backend', '?')} "
                            f"q{ms.get('queue', '?')} "
                            f"p{ms.get('pumped', '?')}/"
                            f"s{ms.get('starved', '?')}")
                    if not ms.get("alive", True) or ms.get("error"):
                        hud += f" DEAD:{ms.get('error')}"
                lines.append((hud, (255, 255, 255), 96, 8, hy))
            if show_hud and show_ai:
                # NOTE: nearest-monster AI readout, X only (--debug):
                # --extra-hud keeps just the white coords line above.
                best, bestd = None, None
                for mo in mobjs:
                    if (mo is player_mo or mo.dead or mo.health <= 0
                            or mo.type == MT_INDEX["PLAYER"]):
                        continue
                    d = abs(mo.x - player_mo.x) + abs(mo.y - player_mo.y)
                    if bestd is None or d < bestd:
                        best, bestd = mo, d
                if best is not None:
                    sight = check_sight(best, player_mo, ctx)
                    ai_line = (
                        f"AI {MT_NAMES[best.type]} "
                        f"st={state_names.get(best.state, best.state)} "
                        f"tgt={'Y' if best.target is player_mo else 'N'} "
                        f"sight={'Y' if sight else 'N'} "
                        f"d={bestd // 65536} "
                        f"r={best.reactiontime} m={best.movecount}")
                    lines.append((ai_line, (100, 255, 100), 96,
                                  8, hy + 20))
            help_line = (
                "WASD/arrows move+turn, mouse look, Shift run, E use, "
                "1-7 weapons, TAB map, M sound/mark, P pause, F1 help, "
                "F6/F9 quicksave, Esc menu"
            )
            if debug:
                help_line += " [N noclip F freeze X AI PgUp/PgDn G mouse]"
            lines.append((help_line, (180, 180, 180), 255,
                          8, WIN_H - 120))
        return lines

    def gl_draw_hud() -> None:
        """Readout block + help line as GL text quads (same layout as
        the software blits). Per-frame upload + delete: values change
        every frame (coords, fps), so nothing is cached (raises on GL
        error: the present fallback covers the frame)."""
        if font is None or gl_frame is None:
            return
        import numpy as np
        for text, rgb, alpha, x, y in hud_lines():
            img = font.render(text, True, rgb)
            w, h = img.get_width(), img.get_height()
            arr = np.frombuffer(pygame.image.tobytes(img, "RGBA"),
                                dtype=np.uint8).reshape(h, w, 4).copy()
            arr[:, :, 3] = (arr[:, :, 3].astype(np.uint16)
                            * alpha // 255).astype(np.uint8)
            tex_id = gl_frame.upload_text(arr.tobytes(), w, h)
            try:
                gl_frame.draw_text_quad(tex_id, x, y, w, h)
            finally:
                gl_frame.delete_text(tex_id)

    def sync_gl_dynamic() -> None:
        """Per-frame dynamic-sector sync (doors/plats/lights/switches).

        Diffs the live map against the load-time snapshot: CLEAN does
        zero GL work, LIGHT re-uploads only the tiny sector-light
        texture (flicker/strobe), GEO refreshes only affected walls +
        planes in place (fast path: same tiers, VBO rows patched, VAOs
        stay valid; tier-set changes like fully closed/opened doors
        take the slow full rebuild for that frame). Read-only w.r.t.
        the sim (demo checksums untouched). Raises on GL error:
        gl_present_all catches it and falls back to software for the
        frame.
        """
        if gl_dyn is None or gl_res is None or gl_frame is None:
            return
        from pydoom.glrender import dynamic as gldyn
        from pydoom.glrender import preprocess as glpre
        from pydoom.glrender import textures as gltex
        kind = gl_dyn.diff(game_map)
        if kind == gldyn.CLEAN:
            return
        if kind == gldyn.GEO:
            if gl_geo is not None:
                stable, texmoved, touched = glpre.refresh_walls(
                    gl_geo.walls, gl_geo.seg_quadpos, game_map,
                    texman, renderer.skyflatnum,
                    gl_dyn.changed_sectors, gl_dyn.changed_sides)
                touched_tris = glpre.refresh_planes(
                    gl_geo.planes, gl_dyn.fans, game_map,
                    renderer.skyflatnum, gl_dyn.changed_sectors,
                    gl_geo.sec_tripos)
                if stable:
                    missing = (
                        set(gltex.wall_texnums_used(gl_geo.walls))
                        - set(gl_res.wall_textures))
                    if missing:
                        gl_res.upload_wall_textures(
                            gltex.build_wall_textures(texman,
                                                      missing))
                    if texmoved:
                        gl_res.replan_wall_index(gl_geo.walls.quads)
                    if touched:
                        gl_res.reupload_walls_fast(gl_geo.walls.quads,
                                                   touched)
                    if touched_tris and not gl_res.reupload_planes_fast(
                            gl_geo.planes.tris, gl_res.flat_layers,
                            touched_tris):
                        gl_res.reupload_planes(gl_geo.planes,
                                               gl_res.flat_layers)
                else:
                    slow_geo_sync()
            else:
                slow_geo_sync()
        gl_res.upload_sector_lights(
            gldyn.sector_light_bases(game_map))

    def slow_geo_sync() -> None:
        """Full GEO rebuild (cold path: tier sets changed, e.g. a door
        fully closed/opened, or no incremental cache). Re-plans the
        seg->quad map after (quad order/count moved)."""
        from pydoom.glrender import preprocess as glpre
        from pydoom.glrender import textures as gltex
        walls = glpre.build_walls(game_map, texman,
                                  renderer.skyflatnum)
        planes = glpre.emit_planes(gl_dyn.fans, game_map,
                                   renderer.skyflatnum)
        missing = (set(gltex.wall_texnums_used(walls))
                   - set(gl_res.wall_textures))
        if missing:
            gl_res.upload_wall_textures(
                gltex.build_wall_textures(texman, missing))
        gl_res.reupload_walls(walls)
        gl_res.reupload_planes(planes, gl_res.flat_layers)
        if gl_geo is not None:
            gl_geo.walls = walls
            gl_geo.planes = planes
            gl_geo.seg_quadpos = glpre.seg_quad_positions(walls)
            # NOTE: sec_tripos never changes (fan topology is fixed).

    def gl_present_all(fb, pal_idx) -> None:
        """GL present: live world (level/menu) + overlay + text."""
        if has_level and gamestate in ("level", "menu"):
            sync_gl_dynamic()
            bbs = gl_feed.project(
                mobjs, int(cam.x * 65536), int(cam.y * 65536),
                cam.bam, texman) if gl_feed is not None else []
            if renderer.skytexture in gl_res.wall_textures:
                sky = (gl_res.wall_textures[renderer.skytexture],
                       gl_res.wall_info[renderer.skytexture][1])
            else:
                sky = None
            guns = []
            for base, frame, bobx, boby in gun_draws():
                spr = renderer.sprite_num_for_base(base, frame)
                if spr is None:
                    continue
                patch = texman.get_sprite_patch(spr)
                tex_id = gl_res.sprite_textures.get(spr)
                if tex_id is None:
                    continue
                guns.append((tex_id, patch.width, patch.height,
                             patch.leftoffset, patch.topoffset,
                             bobx, boby))
            if win_backend == "openglv2" and hasattr(gl_frame, "set_lights"):
                # NOTE: step 6 dynlights (opt-in, empty = vanilla) and
                # step 8 brightmaps (opt-in flag): set every frame so
                # toggling never leaves stale uniforms behind.
                from pydoom.glrenderer2 import lights as _dynl
                entries = []
                if msettings.dynlights and flash_light() > 0:
                    entries.append(_dynl.muzzle_light(
                        player_mo.x / 65536.0, player_mo.y / 65536.0,
                        player_mo.z / 65536.0 + 41.0))
                if msettings.dynlights:
                    _styles = {MT_INDEX["ROCKET"]: "rocket",
                               MT_INDEX["PLASMA"]: "plasma",
                               MT_INDEX["BFG"]: "bfg"}
                    for mo in mobjs:
                        if mo.dead or not (mo.flags & _MF_FLAGS["MF_MISSILE"]):
                            continue
                        entries.append(_dynl.missile_light(
                            mo.x / 65536.0, mo.y / 65536.0,
                            (mo.z + mo.height // 2) / 65536.0,
                            _styles.get(mo.type, "rocket")))
                gl_frame.set_lights(_dynl.pack_lights(entries))
                gl_frame.set_brightmaps(msettings.brightmaps)
            gl_frame.render(
                int(cam.x * 65536), int(cam.y * 65536),
                int(cam.viewz * 65536), cam.bam,
                extra_light=flash_light(),
                fullbright=bool(state["ps"].powers.get(PW_INFRARED)),
                sprites=bbs, sky=sky, psprites=guns,
                pal_index=pal_idx, pitch=cam_pitch)
            gl_frame.blit_world()
        gl_frame.present_overlay(fb, pal_idx)
        if gamestate == "finale":
            gl_finale_text()
        gl_draw_version()

    def load_map(marker: str, keep_ps=None, keep_hp: int | None = None):
        if loaded_marker[0] is not None and loaded_marker[0] != marker:
            # NOTE: ext map_unload (per-map mod state cleanup): every
            # transition funnels through load_map, before the new setup.
            modmgr.emit("map_unload", map=loaded_marker[0])
        game_map = Map.from_wad(wad, marker)
        # NOTE: g_game.c picks SKY1/2/3/4 per episode at P_SetupLevel.
        try:
            renderer.skytexture = texman.texture_num_for_name(
                f"SKY{marker[1]}")
        except Exception:
            pass
        texman.resolve_map(game_map)
        refresh_gl_resources(game_map)
        phys = Physics(game_map)
        # Live mobjs (statues until AI lands); physics sees them.
        index = ThingIndex(game_map)
        phys.things = index
        phys.damage_hook = lambda tm, th: combat.things_hit(tm, th, ctx)
        # NOTE: vanilla P_SetupLevel order: the THINGS loop (console
        # player spawned inline at its loop position, with its lastlook
        # draw) runs BEFORE World/P_SpawnSpecials (light thinkers draw
        # flicker/strobe seeds), so the RNG stream lines up from tic 0.
        player_box: dict = {}
        mobjs = spawn_map(game_map, phys, index, skill, nomonsters,
                          player_hook=lambda mo, th: player_box.update(
                              mo=mo, thing=th))
        world = World(game_map, texman)
        world.totals = level_totals(mobjs, game_map.sectors)
        # Player body for monster AI and walls alike: the camera drives
        # this mobj directly (no separate physics body, so there is no
        # self-collision). Culled from its own view like vanilla.
        player_mo = player_box["mo"]
        player_mo.is_player = True
        start = player_box["thing"]
        if keep_hp is not None:
            player_mo.health = keep_hp
        sub = renderer.sector_at(
            game_map, start.x << 16, start.y << 16
        )
        assert sub.sector is not None
        floor = sub.sector.floorheight / 65536.0
        cam = Camera(float(start.x), float(start.y), float(start.angle),
                     floor + VIEWHEIGHT_ABOVE_FLOOR)
        ctx = AIContext(
            physics=phys, world=world, players=[player_mo],
            sector_index={id(s): i for i, s in enumerate(game_map.sectors)},
            skill=skill, fast=fast, respawn=respawn,
        )
        ctx.mobjs = mobjs
        ctx.skyflatnum = renderer.skyflatnum

        from pydoom.doors import grind_sector

        def _grind_sector(sec, crush) -> bool:
            """PIT_ChangeSector over live mobjs (gibs, drops, damage)."""
            return grind_sector(world, sec, crush, mobjs, phys, ctx)
        world.grind = _grind_sector
        # NOTE: level transitions carry guns/ammo/armor (keys/powers
        # stripped); player health rides on the fresh body below.
        ps = keep_ps if keep_ps is not None else PlayerState()
        if keep_ps is not None:
            flow.strip_for_next_level(ps)
        ctx.player_state = ps
        state = {"cooldown": 0, "refire": False, "firing": False,
                 "start": start, "ps": ps, "won": False,
                 "flash_until": 0, "atk_until": 0, "atk_span": 1,
                 "pending": [],  # NOTE: scheduled shots (weapon windup)
                 "bob": 0, "face": FaceState()}
        # NOTE: ext map_load (all transitions funnel through load_map).
        modmgr.emit("map_load", map=marker, skill=skill)
        loaded_marker[0] = marker
        return game_map, cam, phys, player_mo, world, mobjs, ctx, state

    # NOTE: ext manager before the first load_map (map_load hook fires
    # inside it); menu attaches later for ExtApi.text, backend + refresh
    # once win_backend exists below. No mods/ dir = empty manager, the
    # game runs exactly as before.
    modmgr = ext.ModManager(ext.default_mods_dir())
    ext.set_current(modmgr)
    if not no_mods:
        modmgr.discover()
        # NOTE: launcher/CLI per-mod picks (off wins on conflict); the
        # backend re-gate below refreshes onto the final states.
        modmgr.apply_overrides(set(mods_on), set(mods_off))
    modmgr.palette = palette_lut  # NOTE: ext api.image converts UI art here
    loaded_marker: list = [None]  # NOTE: ext map_unload tracks this
    # NOTE: G_InitNew sim part (M_ClearRandom + fast tables) runs on
    # fresh runs only: boot, menu new game, demo start. Transitions,
    # warps and snapshots keep the stream going, like vanilla.
    flow.init_new(skill, fast)
    game_map, cam, phys, player_mo, world, mobjs, ctx, state = load_map(
        map_name)
    combat.register_combat_actions()
    cheat = cheats.CheatEngine()  # iddqd/idkfa/idclip/... on typed chars
    noclip = False
    paused = False  # P freezes the sim (vanilla pause, music plays on)
    quickslot = None  # F6/F9 slot (vanilla quickSaveSlot, menu sets it)
    show_ai = False  # X toggles a nearest-monster AI readout
    amap = None  # TAB automap overlay (vanilla keeps the game running)
    am_zoom_in = am_zoom_out = False
    state_names = {v: k for k, v in STATE_INDEX.items()}
    message: str | None = None
    message_tics = 0
    map_idx = maps.index(game_map.marker) if game_map.marker in maps else 0
    game_menu = menu.Menu(
        wad, msettings,
        menu.SKILLS.index(skill) if skill in menu.SKILLS else 2,
        _mission.episode_count(game_mission) - 1)
    modmgr.menu = game_menu  # NOTE: ExtApi.text draws via the menu font
    if no_mods:
        menu.remove_extensions_entry(game_menu.menus)
    cam_pitch = 0.0  # NOTE: mouselook-only GL pitch (radians, +up)
    ext_settings_last: dict | None = None  # NOTE: ext settings cache
    ext_gamestate_last: str | None = None  # NOTE: ext gamestate cache
    gamestate = "level"  # level|menu|wipe|title|inter|finale
    inter = None  # tally screen between maps (G_WorldDone lite)
    wipe_after = "level"  # melt landing state
    next_map, next_keep, next_hp = None, None, None  # inter exit
    if frames_opt is None and not debug:
        gamestate = "title"  # NOTE: vanilla boots to TITLESCREEN
    has_level = gamestate == "level"  # menu-close target before new game
    melt = MeltWipe()
    melt_acc = 0.0  # NOTE: wipe_ScreenWipe melts in real-time tics
    wipe_dbg_mark = -1  # NOTE: last dumped melt.total milestone
    last_fb = None
    # NOTE: M_QuitDOOM death jingle (shareware picks the first table).
    QUITSOUNDS = ("pldeth", "dmpain", "popain", "slop", "telept",
                  "posit1", "posit3", "sgtatk")

    def flash_light() -> int:
        """Muzzle-flash room light (A_Light1/2 levels, with the
        shotgun/BFG step-up mid-flash), like the psprite flash."""
        ps = state["ps"]
        extra = 0
        left = state.get("flash_until", 0) - state.get("tics", 0)
        if left > 0:
            extra = weapons.FLASH_LIGHT[ps.readyweapon]
            split = weapons.FLASH_LIGHT_STEP.get(ps.readyweapon)
            if split is not None and \
                    weapons.FLASH_TICS[ps.readyweapon] - left >= split[0]:
                extra = split[1]
        return extra

    def gun_draws() -> list:
        """(base, frame, bobx, boby) in draw order (P_DrawPlayerSprites
        lite: ready gun + muzzle flash, bob, lower/raise travel while
        switching, kick frame while firing). The software path draws
        these into the fb; the GL path resolves them to psprite quads
        (same selection incl. the pick-or-A fallback)."""
        ps = state["ps"]
        body, flash = weapons.PSPRITES[ps.readyweapon]
        bob, amp = state.get("tics", 0), state.get("bobamp", 0)
        # NOTE: vanilla sway cycle is 64 tics (angle = 128*leveltime);
        # height bounces on the positive lobe only (angle & 4095).
        phase = bob * math.pi / 32
        bobx = int(amp * math.cos(phase))
        boby = int(amp * abs(math.sin(phase)))
        firing = state.get("tics", 0) < state["flash_until"]
        attacking = state.get("tics", 0) < state.get("atk_until", 0)
        if ps.pendingweapon != ps.readyweapon:
            # NOTE: A_Lower/A_Raise dip: old gun sinks, new gun rises.
            travel = 1 - ps.switchtics / weapons.SWITCH_TICS
            if travel < 0.5:
                yoff = int(96 * travel * 2)
            else:
                body, flash = weapons.PSPRITES[ps.pendingweapon]
                yoff = int(96 * (1 - (travel - 0.5) * 2))
            firing = attacking = False
        else:
            yoff = 0
        draws = []
        if attacking:
            # NOTE: body frames ride the full attack cycle (p_pspr.c),
            # so kicks read instead of blinking past.
            span = max(1, state.get("atk_span", 1))
            elapsed = span - (state["atk_until"] - state.get("tics", 0))
            timeline = weapons.attack_timeline(ps.readyweapon,
                                               state.get("atkflip", 0),
                                               state.get("atkheld", False))
            pick = timeline[min(len(timeline) - 1, max(0, elapsed))]
            if renderer.sprite_num_for_base(body, pick) is not None:
                draws.append((body, pick, bobx, boby + yoff))
            else:
                draws.append((body, "A", bobx, boby + yoff))
        elif ps.pendingweapon != ps.readyweapon:
            # NOTE: lower/raise states show each gun's own up/down
            # frame (all A, saw C: S_SAWUP/S_SAWDOWN run on frame 2).
            shown = (ps.pendingweapon if travel >= 0.5
                     else ps.readyweapon)
            rest = "C" if shown == WP_CHAINSAW else "A"
            draws.append((body, rest, bobx, boby + yoff))
        else:
            # NOTE: ready guns hold frame A, except the idling saw
            # (S_SAW/S_SAWB alternate C/D every 4 tics, blade up).
            draws.append((body, weapons.idle_frame(
                ps.readyweapon, state.get("tics", 0)),
                bobx, boby + yoff))
        if firing and flash is not None:
            draws.append((flash, "A", bobx, boby + yoff))
        return draws

    def begin_wipe(old, new, after=None) -> None:
        """Start a melt (every transition funnels here).

        after lands the wipe (None keeps the current wipe_after, like
        the menu path always did). A malformed pair lands immediately
        instead of raising mid-transition. With --debug, every start
        (and landing, in the wipe branch) logs frame + shapes; a start
        while the previous wipe never landed prints RESTARTED, which is
        the tripwire for duplicated-bar artifacts (two live melt
        generations blending would stack status bars).
        """
        nonlocal gamestate, wipe_after
        # NOTE: melt.total milestones dumped per wipe (post-mortem).
        nonlocal wipe_dbg_mark
        wipe_dbg_mark = -1
        if debug:
            print(f"melt: start after={after} "
                  f"old={None if old is None else getattr(old, 'shape', '?')} "
                  f"new={None if new is None else getattr(new, 'shape', '?')} "
                  f"frame={frames} "
                  f"{'RESTARTED-MID-WIPE' if not melt.done else 'clean'}")
            _dump_wipe_frame(f"w{frames:06d}-start-old", old)
            _dump_wipe_frame(f"w{frames:06d}-start-new", new)
        if old is None or new is None:
            gamestate = after if after is not None else "level"
            return
        try:
            melt.start(old, new)
        except Exception as exc:  # noqa: BLE001 - land, never crash/artifacts
            print(f"melt: refused ({exc}), landing")
            gamestate = after if after is not None else "level"
            return
        if after is not None:
            wipe_after = after
        gamestate = "wipe"

    def render_scene(with_view: bool = True):
        """One frozen-sim scene frame (psprites + status bar included).

        with_view False skips the 3D raycast and the gun blits (GL
        path: the world and the gun draw natively, the fb carries
        only overlay art over a transparent-255 background)."""
        ps = state["ps"]
        extra = flash_light()
        if with_view:
            fb = renderer.render_view(
                game_map, int(cam.x * 65536), int(cam.y * 65536),
                cam.bam, int(cam.viewz * 65536), mobjs,
                extra_light=extra,
                fullbright=bool(state["ps"].powers.get(PW_INFRARED)),
            )
            for base, frame, bobx, boby in gun_draws():
                renderer.draw_psprite(fb, base, bobx, boby, frame)
        else:
            fb = np.full((200, 320), 255, dtype=np.uint8)
        # NOTE: classic bottom strip (covers the gun base, like vanilla).
        draw_status_bar(renderer, fb, ps, player_mo.health,
                        state.get("facelump", "STFST00"))
        # NOTE: ext statusbar (custom rows/overdraw): cosmetic, after the
        # vanilla strip so mods draw on top of it, under HUD messages.
        modmgr.emit("statusbar", fb=fb)
        # NOTE: HUD messages ride the red STCFN font flush top-left
        # (hu_stuff), above the readout block, like the original.
        if message is not None and msettings.messages:
            game_menu.draw_text(fb, message, 0, 0)
        return fb

    def build_snapshot(name: str) -> dict:
        """Mid-level bundle: scalars plus one pickle of the live graph."""
        from pydoom import saveg
        from pydoom.m_random import get_state
        return {
            "version": saveg.SAVE_VERSION, "name": name,
            "marker": game_map.marker, "skill": skill,
            "fast": fast, "respawn": respawn, "nomonsters": nomonsters,
            "time": world.time, "rng": get_state(),
            "cam": (cam.x, cam.y, cam.angle, cam.viewz),
            "player": mobjs.index(player_mo),
            "blob": saveg.build_blob(game_map.sectors, world.thinkers,
                                      mobjs, state["ps"]),
        }

    def apply_snapshot(bundle) -> None:
        """Load: fresh map, then graft the saved graph onto it."""
        from pydoom import saveg
        from pydoom.doors import Ceiling, FloorMover, Plat, VerticalDoor
        from pydoom.m_random import set_state
        nonlocal gamestate, game_map, cam, phys, player_mo, world, \
            mobjs, ctx, state, map_idx, amap, message, message_tics, \
            noclip, skill, fast, respawn, nomonsters, running, has_level, \
            demo_play, wipe_after
        err = saveg.validate(bundle, maps)
        if err is not None:
            audio.play("oof")
            return
        old = last_fb.copy() if last_fb is not None else None
        skill = bundle["skill"]
        fast = bundle.get("fast", False)
        respawn = bundle.get("respawn", False)
        nomonsters = bundle.get("nomonsters", False)
        demo_play = None  # NOTE: saves carry no stream position
        (game_map, cam, phys, player_mo, world, mobjs, ctx,
         state) = load_map(bundle["marker"])
        sectors_old, thinkers, mobjs_new, ps_new = saveg.unpack_blob(
            bundle["blob"], game_map.sectors)
        saveg.sector_state(sectors_old, game_map.sectors)
        index = phys.things
        for mo in list(mobjs):  # fresh spawn out (sectors die with it)
            try:
                index.unlink(mo)
            except Exception:
                pass
        for sec in game_map.sectors:
            sec.thinglist.clear()
            sec.specialdata = None
        for mo in mobjs_new:  # saved mobjs in, relinked to the index
            index.link(mo)
            mo.sector.thinglist.append(mo)
        for th in thinkers:  # movers re-claim their sectors
            if isinstance(th, (VerticalDoor, FloorMover, Ceiling, Plat)):
                th.sector.specialdata = th
        world.thinkers = thinkers
        world.time = bundle["time"]
        mobjs = mobjs_new
        ctx.mobjs = mobjs
        player_mo = mobjs[bundle["player"]]
        ctx.players = [player_mo]
        state["ps"] = ps_new
        ctx.player_state = ps_new
        cam.x, cam.y, cam.angle, cam.viewz = bundle["cam"]
        set_state(bundle["rng"])
        amap = None
        message, message_tics = None, 0
        noclip = False
        has_level = True
        cheat.reset()
        map_idx = maps.index(game_map.marker)
        pygame.display.set_caption(f"pydoom - {game_map.marker}")
        audio.music_play(song_for_map(game_map.marker), "load-game")
        if old is None:
            gamestate = "level"
        else:  # NOTE: vanilla melts into the loaded game
            begin_wipe(old, render_scene(), "level")

    def refresh_ext_menu() -> None:
        """Rebuild Options -> Extension rows from ModManager.status()."""
        items = []
        for mid, ver, state, reason in modmgr.status():
            label = ext.row_label(mid, ver, state, reason)
            items.append(menu.MenuItem(f"ext:{mid}", None, "action",
                                       "", label=label))
        if not items:
            items.append(menu.MenuItem("ext:", None, "action",
                                       "", label="(no mods found)"))
        extdef = game_menu.menus["extensions"]
        extdef.items = items
        extdef.last_on = 0

    def apply_menu_event(mev):
        """Menu selections: quit, or a wiped fresh start on E1M1."""
        nonlocal gamestate, game_map, cam, phys, player_mo, world, \
            mobjs, ctx, state, map_idx, amap, message, message_tics, \
            noclip, skill, running, has_level, paused, quickslot, \
            demo_play
        if mev == "close":
            gamestate = "level" if has_level else "title"
        elif isinstance(mev, tuple) and mev[0] == "ext_open":
            refresh_ext_menu()
            game_menu.current = "extensions"
        elif isinstance(mev, tuple) and mev[0] == "ext_toggle":
            modmgr.toggle(mev[1])
            refresh_ext_menu()
        elif isinstance(mev, tuple) and mev[0] == "video_changed":
            apply_video_settings()
        elif mev == "quit":
            audio.play(random.choice(QUITSOUNDS))
            menu.settings_save(menu.CONFIG_PATH, msettings)
            running = False
        elif isinstance(mev, tuple) and mev[0] == "load_game":
            from pydoom import saveg
            apply_snapshot(saveg.read_slot(mev[1]))
        elif mev == "endgame":
            # NOTE: M_EndGameResponse: back to the title (netgame N/A).
            has_level = False
            paused = False
            amap = None
            demo_play = None
            finish_demo_rec()
            audio.music_play(TITLE_SONG, "end-game")
            gamestate = "title"
        elif isinstance(mev, tuple) and mev[0] == "save_game":
            if not has_level:
                audio.play("oof")
                return
            from pydoom import saveg
            saveg.write_slot(mev[1], build_snapshot(mev[2]))
            quickslot = mev[1]  # NOTE: manual saves arm quicksave
            gamestate = "level"  # NOTE: vanilla closes after saving
        elif isinstance(mev, tuple) and mev[0] == "new_game":
            old = last_fb.copy() if last_fb is not None else None
            skill = mev[2]
            demo_play = None  # NOTE: new game stops playback (G_DoNewGame)
            flow.init_new(skill, fast)
            if rec_demo_path is not None:
                finish_demo_rec()
            has_level = True
            # NOTE: G_DeferedInitNew starts at the chosen episode's
            # first map (E2M1/E3M1); unknown lumps fall back to E1M1.
            dest = _mission.episode_start(mev[1], maps)
            map_idx = maps.index(dest)
            (game_map, cam, phys, player_mo, world, mobjs, ctx,
             state) = load_map(dest)
            if rec_demo_path is not None:
                arm_demo_rec()  # NOTE: header names the fresh start map
            amap = None
            message, message_tics = None, 0
            noclip = False
            paused = False
            cheat.reset()
            pygame.display.set_caption(f"pydoom - {dest}")
            audio.music_play(song_for_map(dest), "new-game")
            if old is None:
                gamestate = "level"
            else:
                # NOTE: menu melts away (wipe_after untouched, as before).
                begin_wipe(old, render_scene(), None)

    pygame.init()
    # NOTE: milestone H phase 0: backend selection lives in
    # glrender.state; any failure falls back to software, never raises.
    # gl_info is the GL version string on the live opengl path. Window
    # geometry comes from the video settings (resolution for GL, scale
    # for software, display mode for both).
    from pydoom.glrender import state as glstate
    WIN_W, WIN_H, _win_flags, _win_disp = video_geom(
        msettings, want_api != "software")
    screen, gl_info, video_api, video_why = glstate.try_init(
        WIN_W, WIN_H, want_api, frames_opt, timedemo,
        flags=_win_flags, vsync=int(bool(msettings.vsync)),
        display=_win_disp)
    WIN_W, WIN_H = screen.get_width(), screen.get_height()
    print(f"video: {video_api} {WIN_W}x{WIN_H} ({video_why})"
          f" fps={msettings.fps_limit or 'unlimited'}"
          f" vsync={int(bool(msettings.vsync))}")
    gl_live = video_api in ("openglv1", "openglv2") \
        and gl_info is not None
    # NOTE: which window type backs `screen` (software 2D blits onto a
    # GL window present black, so the loop self-heals that mismatch).
    win_backend = video_api if gl_live else "software"
    # NOTE: ext backend gate lives here: mods requiring opengl/software
    # resolve now (and again on every live backend switch below).
    modmgr.set_backend(win_backend)
    win_heal_failed = False  # NOTE: stop retrying a dead downgrade
    if gl_live:
        refresh_gl_resources(game_map)  # NOTE: boot map loaded above
    pygame.display.set_caption(f"pydoom - {game_map.marker}")
    try:
        audio.init(wad)  # silent no-op when the mixer is missing
    except Exception:
        pass
    try:
        audio.music_init(wad)  # song thread (silent without PyOPL)
        audio.music_play(TITLE_SONG if gamestate == "title"
                         else song_for_map(game_map.marker),
                         "boot-title" if gamestate == "title" else "boot-level")
    except Exception:
        pass
    try:
        font = pygame.font.SysFont(None, 18)
    except Exception:
        font = None
    clock = pygame.time.Clock()
    pygame.mouse.set_visible(False)
    pygame.event.set_grab(True)

    tic_acc = 0.0
    ui_acc = 0.0  # NOTE: 35Hz menu/inter ticks (fps-independent, below)
    fps_ema = 60.0
    frames = 0
    running = True
    recording = rec_path is not None
    replaying = play_path is not None
    demo_log: list = []  # per-frame inputs while recording
    demo_in: list = []  # replay script
    if replaying:
        import pickle
        with open(play_path, "rb") as f:
            demo_in = pickle.load(f)
    demo_idx = 0
    demo_frame = False  # a replayed frame drives input this tick
    demo_keys = None  # virtual pressed-set while replaying
    demo_sum = 0  # framebuffer checksum (record and replay agree)
    rec_events: list = []
    tbuilder = ticcmd.TiccmdBuilder()  # 35 Hz input packets (milestone A)
    demo_play = None  # DemoReader driving ticcmds (G_DoPlayDemo)
    if play_demo_path is not None and demo_header is not None:
        demo_play = demo.DemoReader(demo_blob)
        # NOTE: ext demo_start (timedemo shares the playdemo arm).
        modmgr.emit("demo_start",
                    mode="timedemo" if timedemo else "playback")
    attract_idx = 0  # next IWAD demo (D_AdvanceDemo rotation DEMO1-3)
    attract_idle = 0.0  # title seconds before the demo loop kicks in
    attract_active = False  # a live demo started by the title loop
    # NOTE: vanilla pagetime approx (sync-exempt); env override is for
    # headless smoke tests only.
    ATTRACT_DELAY = float(os.environ.get("PYDOOM_ATTRACT_DELAY", 10.0))
    demo_rec = None  # DemoWriter for --record-demo (vanilla .lmp)

    def arm_demo_rec() -> None:
        """(Re)start the .lmp recorder with a fresh header (run start)."""
        nonlocal demo_rec
        demo_rec = demo.DemoWriter(demo.DemoHeader(
            skill=demo.SKILL_NAMES.index(skill)
            if skill in demo.SKILL_NAMES else 2,
            episode=int(game_map.marker[1]),
            map=int(game_map.marker[3:]), deathmatch=0,
            respawn=int(respawn), fast=int(fast),
            nomonsters=int(nomonsters), consoleplayer=0,
            players=(1, 0, 0, 0)))
        # NOTE: ext demo_start (stream guards/overlays): record branch.
        modmgr.emit("demo_start", mode="record")

    def finish_demo_rec() -> None:
        """Append DEMOMARKER and flush the .lmp (G_CheckDemoStatus tail)."""
        nonlocal demo_rec
        if demo_rec is not None and rec_demo_path is not None:
            with open(rec_demo_path, "wb") as f:
                f.write(demo_rec.finish())
            print(f"demo: recorded {demo_rec.tics} tics"
                  f" -> {rec_demo_path}")
            demo_rec = None
        # NOTE: ext demo_stop (record branch; None-safe when idle).
        modmgr.emit("demo_stop", mode="record")

    def end_demo_playback(note: str) -> None:
        """Stream over (DEMOMARKER, finale): back to title like vanilla
        G_CheckDemoStatus; -timedemo prints stats and quits instead."""
        nonlocal demo_play, running, gamestate, has_level
        nonlocal attract_active, attract_idle
        tics = demo_play.tics if demo_play is not None else 0
        demo_play = None
        attract_active = False
        attract_idle = 0.0  # NOTE: title pause between loop demos
        if timedemo:
            print(f"timedemo: {tics} tics {note} {game_map.marker} "
                  f"t={world.time} "
                  f"({player_mo.x >> 16},{player_mo.y >> 16}) "
                  f"hp={player_mo.health} k={state['ps'].killcount}")
            running = False
        gamestate = "title"
        has_level = False
        audio.music_play(TITLE_SONG, "demo-title")
        # NOTE: ext demo_stop (stream over, timedemo quits instead).
        modmgr.emit("demo_stop",
                    mode="timedemo" if timedemo else "playback")

    def stop_demo_playback() -> None:
        """Any key stops a running demo (vanilla demo loop exit)."""
        nonlocal demo_play, attract_active, attract_idle
        nonlocal gamestate, has_level
        demo_play = None
        attract_active = False
        attract_idle = 0.0
        gamestate = "title"
        has_level = False
        audio.music_play(TITLE_SONG, "demo-stop")
        # NOTE: ext demo_stop (any-key exit, attract included).
        modmgr.emit("demo_stop", mode="playback")

    def start_attract_demo() -> bool:
        """D_AdvanceDemo: title timeout plays DEMO1/2/3 in rotation."""
        nonlocal demo_play, attract_active, attract_idx
        nonlocal gamestate, has_level, game_map, cam, phys, player_mo
        nonlocal world, mobjs, ctx, state, map_idx, amap
        nonlocal skill, fast, respawn, nomonsters, message, message_tics
        for _ in range(3):
            name = ("DEMO1", "DEMO2", "DEMO3")[attract_idx]
            attract_idx = (attract_idx + 1) % 3
            try:
                blob = wad.read_lump(name)
                header = demo.DemoHeader.from_bytes(blob)
            except Exception:
                continue  # NOTE: missing/bad lump: try the next demo
            if not header.single_player() \
                    or header.marker(game_mission) not in maps:
                continue
            skill = header.skill_name()
            fast = bool(header.fast)
            respawn = bool(header.respawn)
            nomonsters = bool(header.nomonsters)
            flow.init_new(skill, fast)
            map_idx = maps.index(header.marker(game_mission))
            (game_map, cam, phys, player_mo, world, mobjs, ctx,
             state) = load_map(header.marker(game_mission))
            amap = None
            message, message_tics = None, 0
            pygame.display.set_caption(f"pydoom - {game_map.marker}")
            audio.music_play(song_for_map(game_map.marker),
                             "attract-demo")
            demo_play = demo.DemoReader(blob)
            attract_active = True
            # NOTE: ext demo_start (title-loop attract branch).
            modmgr.emit("demo_start", mode="attract")
            gamestate = "level"
            has_level = True
            print(f"demo: attract {name} ({header.marker(game_mission)})")
            return True
        return False

    checksum_lines: list = []  # per-tic sim trace (desync detector)

    def trace_sim_tic() -> None:
        """Append one desync-detector line (stream pos + sim state)."""
        from pydoom.m_random import get_state
        from pydoom.player import AM_CLIP, AM_SHELL
        rng0, rng1 = get_state()
        ps = state["ps"]
        checksum_lines.append(
            f"{demo_play.tics if demo_play is not None else -1} "
            f"{world.time} {game_map.marker} "
            f"{player_mo.x} {player_mo.y} {player_mo.z} "
            f"{player_mo.angle} {player_mo.health} "
            f"{player_mo.momx} {player_mo.momy} "
            f"{rng0} {rng1} "
            f"{sum(1 for mo in mobjs if not mo.dead)} "
            f"{ps.killcount} {ps.ammo[AM_CLIP]} {ps.ammo[AM_SHELL]} "
             f"{ps.readyweapon} {ps.keys}")

    if rec_demo_path is not None:
        arm_demo_rec()
    if demo_play is not None:
        # NOTE: vanilla boots straight into the demo (no title wait);
        # menu new games still cancel playback (G_DoNewGame).
        gamestate = "level"
        has_level = True
    while running:
        if timedemo:
            clock.tick(0)  # NOTE: uncapped, like -timedemo -nodraw
            dt = 1.0 / 60  # NOTE: fixed steps keep stream alignment
        elif recording or replaying or demo_play is not None \
                or demo_rec is not None:
            clock.tick(60)
            dt = 1.0 / 60  # NOTE: demos run on fixed steps, tic-exact
        else:
            fps_cap = msettings.fps_limit
            if fps_cap not in menu.FPS_LIMITS:
                fps_cap = 60
            dt = min(clock.tick(fps_cap) / 1000.0, 0.25)
        fps_ema += (1.0 / max(dt, 1e-6) - fps_ema) * 0.05
        if gamestate == "title" and demo_play is None \
                and play_demo_path is None and not timedemo \
                and rec_demo_path is None and rec_path is None \
                and play_path is None and frames_opt is None \
                and demos_enabled:
            # NOTE: D_AdvanceDemo lite: an idle title falls into the
            # IWAD demo loop (any key wakes it instead, below).
            # Gated: demo compat is experimental (see `demos` in
            # pydoom.cfg); otherwise the title simply waits.
            attract_idle += dt
            if attract_idle >= ATTRACT_DELAY:
                attract_idle = 0.0
                start_attract_demo()
        demo_frame = False
        demo_keys = None
        if replaying:
            if demo_idx < len(demo_in):
                # NOTE: same-frame injection: posted events land in the
                # get() below, polled keys come from the virtual set.
                for entry in demo_in[demo_idx]["ev"]:
                    kind = entry[0]
                    if kind == "down":
                        pygame.event.post(pygame.event.Event(
                            pygame.KEYDOWN, key=entry[1], unicode=entry[2]))
                    elif kind == "up":
                        pygame.event.post(pygame.event.Event(
                            pygame.KEYUP, key=entry[1]))
                    elif kind == "motion":
                        pygame.event.post(pygame.event.Event(
                            pygame.MOUSEMOTION, rel=entry[1],
                            buttons=(0, 0, 0)))
                    elif kind == "btn":
                        pygame.event.post(pygame.event.Event(
                            pygame.MOUSEBUTTONDOWN if entry[2]
                            else pygame.MOUSEBUTTONUP, button=entry[1]))
                demo_keys = _ReplayKeys(_move_keys(
                    demo_in[demo_idx].get("mv", [False] * 8)))
                demo_idx += 1
                demo_frame = True
            else:
                replaying = False
        # NOTE: slider mapping shared by ticcmd and ext look mods (one
        # formula, so the options slider drives both identically).
        sens_rad_per_px = 0.0004 + msettings.mouse_sens * 0.0006
        for ev in pygame.event.get():
            if ev.type in (pygame.KEYDOWN, pygame.MOUSEBUTTONDOWN):
                attract_idle = 0.0  # NOTE: activity resets the demo loop
            if ev.type == pygame.KEYUP and pygame.K_1 <= ev.key <= pygame.K_7:
                # NOTE: ungated release: a digit let go in the menu must
                # not stick as a held weapon (vanilla re-sends BT_CHANGE
                # every tic while the key is down).
                tbuilder.note_weapon_up(ev.key - pygame.K_1)
            if recording:
                if ev.type == pygame.KEYDOWN:
                    rec_events.append(
                        ("down", ev.key, getattr(ev, "unicode", "") or ""))
                elif ev.type == pygame.KEYUP:
                    rec_events.append(("up", ev.key))
                elif ev.type == pygame.MOUSEMOTION:
                    rec_events.append(("motion", tuple(ev.rel)))
                elif ev.type == pygame.MOUSEBUTTONDOWN:
                    rec_events.append(("btn", ev.button, True))
                elif ev.type == pygame.MOUSEBUTTONUP:
                    rec_events.append(("btn", ev.button, False))
            if ev.type == pygame.QUIT:
                menu.settings_save(menu.CONFIG_PATH, msettings)
                running = False
            elif ev.type == pygame.KEYDOWN:
                if demo_play is not None:
                    # NOTE: any key stops demo playback (attract or
                    # -playdemo); the key itself is consumed.
                    stop_demo_playback()
                    continue
                if gamestate == "title":
                    # NOTE: any key wakes the title into the menu.
                    gamestate = "menu"
                    game_menu.open()
                    audio.play("swtchn")
                    continue
                if gamestate == "menu":
                    # NOTE: vanilla menus eat every key (no cheats here).
                    k = None
                    if ev.key == pygame.K_UP:
                        k = "up"
                    elif ev.key == pygame.K_DOWN:
                        k = "down"
                    elif ev.key == pygame.K_LEFT:
                        k = "left"
                    elif ev.key == pygame.K_RIGHT:
                        k = "right"
                    elif ev.key in (pygame.K_RETURN, pygame.K_KP_ENTER):
                        k = "enter"
                    elif ev.key == pygame.K_ESCAPE:
                        k = "esc"
                    elif ev.key == pygame.K_BACKSPACE:
                        k = "backspace"  # NOTE: savegame name entry
                    else:
                        ch = getattr(ev, "unicode", "") or ""
                        if len(ch) == 1:
                            if ch.isalpha():
                                k = ch.lower()
                            elif 33 <= ord(ch) <= 126:
                                k = ch  # NOTE: savegame name entry
                    if k is not None:
                        for mev in game_menu.key(k):
                            apply_menu_event(mev)
                    continue
                if gamestate == "inter":
                    # NOTE: observers don't hurry the tally (vanilla reads
                    # stream buttons here; our stream skips non-level tics,
                    # so any hurry stays self-consistent either way).
                    if demo_play is None and inter is not None:
                        inter.keypress()  # NOTE: hurry the tally
                    continue
                if gamestate == "finale":
                    # NOTE: E1TEXT read: any key returns to the title
                    # (playback already ended at the exit above).
                    if demo_play is not None:
                        continue
                    has_level = False
                    audio.music_play(TITLE_SONG, "finale-title")
                    gamestate = "title"
                    continue
                if gamestate != "level":
                    continue  # NOTE: wipe melts undisturbed
                if ev.key == pygame.K_SPACE:
                    # NOTE: edge latch: sub-frame taps still fire one
                    # tic (vanilla polls every 28 ms; level polls at
                    # low fps miss taps between frames entirely).
                    state["atk_latch"] = True
                # NOTE: every typed char feeds the cheat matcher first
                # (m_cheat, always on like vanilla); dev keys below
                # need --debug, and quit moved into the menu.
                for cname, carg in cheat.feed(
                        getattr(ev, "unicode", "") or ""):
                    _ps = state["ps"]
                    if cname == "iddqd":
                        message = cheats.apply_god(_ps, player_mo)
                        message_tics = 3 * TICRATE
                    elif cname == "idkfa":
                        message = cheats.apply_kfa(_ps)
                        message_tics = 3 * TICRATE
                    elif cname == "idfa":
                        message = cheats.apply_fa(_ps)
                        message_tics = 3 * TICRATE
                    elif cname in ("idclip", "idspispopd"):
                        noclip = not noclip
                        _ps.cheats ^= CF_NOCLIP
                        set_noclip(noclip, player_mo, cam, phys)
                        message = (cheats.NOCLIP_ON if noclip
                                   else cheats.NOCLIP_OFF)
                        message_tics = 3 * TICRATE
                    elif cname == "idchoppers":
                        message = cheats.apply_choppers(_ps)
                        message_tics = 3 * TICRATE
                    elif cname == "iddt":
                        if amap is None:
                            amap = Automap(game_map, WIN_W, WIN_H,
                                           palette_lut.tolist())
                            am_zoom_in = am_zoom_out = False
                        amap.cycle_cheat()
                    elif cname == "idmypos":
                        message = cheats.MYPOS_FMT.format(
                            a=int(math.degrees(cam.angle) % 360),
                            x=player_mo.x >> 16, y=player_mo.y >> 16)
                        message_tics = 3 * TICRATE
                    elif cname == "idclev":
                        # NOTE: registered warp is E1M1-E3M9, fresh start
                        # (PST_REBORN); bad digits fail silently. Like
                        # G_DoNewGame this stops playback and reseeds.
                        if (len(carg) == 2 and carg[0] in "123"
                                and carg[1] in "123456789"):
                            dest = f"E{carg[0]}M{carg[1]}"
                            if (int(carg[0]) <= _mission.episode_count(
                                    game_mission) and dest in maps):
                                demo_play = None
                                flow.init_new(skill, fast)
                                map_idx = maps.index(dest)
                                (game_map, cam, phys, player_mo, world,
                                 mobjs, ctx, state) = load_map(
                                    maps[map_idx])
                                amap = None
                                message, message_tics = (cheats.CLEV,
                                                         3 * TICRATE)
                                pygame.display.set_caption(
                                    f"pydoom - {game_map.marker}")
                    elif cname == "idmus":
                        # NOTE: registered jukebox is E1M1-E3M9.
                        if (len(carg) == 2 and carg[0] in "123"
                                and carg[1] in "123456789"
                                and int(carg[0]) <= _mission.episode_count(
                                    game_mission)):
                            audio.music_play(
                                f"D_E{carg[0]}M{carg[1]}", "idmus")
                        message = cheats.MUS
                        message_tics = 3 * TICRATE
                    elif cname == "idbehold":
                        message = cheats.apply_behold(_ps, player_mo,
                                                      carg)
                        message_tics = 3 * TICRATE
                if ev.key == pygame.K_ESCAPE:
                    gamestate = "menu"  # NOTE: sim freezes underneath
                    game_menu.open()  # NOTE: vanilla lands on Main
                    amap = None  # menu takes over the screen
                    am_zoom_in = am_zoom_out = False
                    audio.play("swtchn")
                elif ev.key == pygame.K_g:
                    if not debug:
                        continue  # NOTE: vanilla G does nothing in game
                    if amap is not None:
                        amap.toggle_grid()  # vanilla TAB-mode G
                    else:
                        pygame.event.set_grab(not pygame.event.get_grab())
                        pygame.mouse.set_visible(
                            not pygame.mouse.get_visible())
                elif ev.key == pygame.K_n:
                    if debug:
                        noclip = not noclip
                        set_noclip(noclip, player_mo, cam, phys)
                elif ev.key == pygame.K_e:
                    # NOTE: use rides the next ticcmd as BT_USE (the tic
                    # loop edges it with usedown, like vanilla); latching
                    # keeps sub-frame taps from getting lost.
                    tbuilder.note_use()
                elif pygame.K_1 <= ev.key <= pygame.K_7:
                    # NOTE: digits latch into the next ticcmd (BT_CHANGE).
                    tbuilder.note_weapon_down(ev.key - pygame.K_1)
                elif ev.key == pygame.K_PAGEUP:
                    if not debug:
                        continue
                    demo_play = None  # NOTE: debug warp: fresh stream state
                    flow.init_new(skill, fast)
                    map_idx = (map_idx - 1) % len(maps)
                    (game_map, cam, phys, player_mo, world, mobjs, ctx,
                     state) = load_map(maps[map_idx])
                    amap = None  # new map, new automap
                    message, message_tics = None, 0
                    pygame.display.set_caption(
                        f"pydoom - {game_map.marker}")
                    audio.music_play(song_for_map(game_map.marker),
                                             "debug-warp")
                elif ev.key == pygame.K_PAGEDOWN:
                    if not debug:
                        continue
                    demo_play = None  # NOTE: debug warp: fresh stream state
                    flow.init_new(skill, fast)
                    map_idx = (map_idx + 1) % len(maps)
                    (game_map, cam, phys, player_mo, world, mobjs, ctx,
                     state) = load_map(maps[map_idx])
                    amap = None  # new map, new automap
                    message, message_tics = None, 0
                    pygame.display.set_caption(
                        f"pydoom - {game_map.marker}")
                    audio.music_play(song_for_map(game_map.marker),
                                             "debug-warp")
                elif ev.key == pygame.K_f:
                    if amap is not None:
                        amap.toggle_follow()  # vanilla TAB-mode F
                    elif debug:
                        ctx.ai_frozen = not ctx.ai_frozen
                elif ev.key == pygame.K_TAB:
                    if amap is None:
                        amap = Automap(game_map, WIN_W, WIN_H,
                                       palette_lut.tolist())
                        am_zoom_in = am_zoom_out = False
                    else:
                        amap = None
                elif ev.key == pygame.K_F1:
                    # NOTE: vanilla help key: straight to Read This!.
                    if has_level and gamestate == "level":
                        gamestate = "menu"
                        game_menu.open_readthis()
                        audio.play("swtchn")
                elif ev.key == pygame.K_F6:
                    # NOTE: vanilla quicksave (pick a slot first time).
                    if has_level and gamestate == "level":
                        from pydoom import saveg
                        if quickslot is None:
                            gamestate = "menu"
                            game_menu.open()
                            game_menu.enter_slots("save")
                            audio.play("swtchn")
                        else:
                            name = saveg.slot_name(quickslot)
                            if name == saveg.EMPTY:
                                name = "QUICKSAVE"
                            saveg.write_slot(quickslot,
                                             build_snapshot(name))
                            message = "QUICKSAVED"
                            message_tics = 2 * TICRATE
                elif ev.key == pygame.K_F9:
                    if has_level and gamestate == "level":
                        from pydoom import saveg
                        if quickslot is None:
                            audio.play("oof")
                            message = ("you haven't picked a quicksave "
                                       "slot yet!")
                            message_tics = 3 * TICRATE
                        else:
                            apply_snapshot(saveg.read_slot(quickslot))
                elif ev.key in (pygame.K_p, pygame.K_PAUSE):
                    # NOTE: P doubles as a cheat letter (idclip ends on
                    # it): the cheat fires first, pause toggles after.
                    # Vanilla pauses on Pause/Break, bound here too.
                    if has_level:
                        paused = not paused
                elif ev.key == pygame.K_m:
                    # NOTE: vanilla TAB-mode M drops a mark; outside the
                    # automap M is mute (keyup below skips open maps).
                    if amap is not None and gamestate == "level":
                        amap.add_mark()
                elif ev.key in (pygame.K_EQUALS, pygame.K_PLUS,
                                pygame.K_KP_PLUS):
                    am_zoom_in = True
                elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    am_zoom_out = True
            elif ev.type == pygame.KEYUP and gamestate == "level":
                if ev.key in (pygame.K_EQUALS, pygame.K_PLUS,
                              pygame.K_KP_PLUS):
                    am_zoom_in = False
                elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    am_zoom_out = False
                elif ev.key == pygame.K_x:
                    if debug:
                        show_ai = not show_ai
                elif ev.key == pygame.K_m:
                    if amap is None:
                        if audio.toggle_mute():
                            message, message_tics = "SOUND OFF", TICRATE
                        else:
                            message, message_tics = "SOUND ON", TICRATE
            elif ev.type == pygame.MOUSEMOTION:
                if gamestate == "level" and (pygame.event.get_grab()
                                             or demo_frame):
                    # NOTE: motion accumulates; each tic quantizes its
                    # share to int16 angleturn (milestone A). Y is negated:
                    # pygame rel-y grows downward, vanilla mousey grows
                    # forward (up), so push-away walks forward. Ext mods
                    # may consume/scale axes first (no_mouse_forward eats
                    # Y, mouselook reads it for vertical look).
                    mev = modmgr.emit("mouse_motion", dx=ev.rel[0],
                                      dy=-ev.rel[1], consume_x=False,
                                      consume_y=False,
                                      sens_rad_per_px=sens_rad_per_px)
                    mx = 0 if mev.consume_x else mev.dx
                    my = 0 if mev.consume_y else mev.dy
                    tbuilder.add_mouse(mx, my)
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if gamestate == "level" and ev.button == 1:
                    state["firing"] = True
                    state["atk_latch"] = True  # NOTE: same tap guarantee
            elif ev.type == pygame.MOUSEBUTTONUP:
                if ev.button == 1:
                    state["firing"] = False

        keys = pygame.key.get_pressed()
        if demo_keys is not None:
            keys = demo_keys  # NOTE: replayed held-keys, not hardware
        if recording:
            # NOTE: movement intent (ticcmd spirit), not raw keys: a
            # typed cheat 'd' must never strafe the replay like K_d.
            demo_log.append({
                "ev": rec_events,
                "mv": [bool(keys[pygame.K_w] or keys[pygame.K_UP]),
                       bool(keys[pygame.K_s] or keys[pygame.K_DOWN]),
                       bool(keys[pygame.K_a]), bool(keys[pygame.K_d]),
                       bool(keys[pygame.K_LEFT]),
                       bool(keys[pygame.K_RIGHT]),
                       bool(keys[pygame.K_LSHIFT]
                            or keys[pygame.K_RSHIFT]),
                       bool(keys[pygame.K_SPACE])],
            })
            rec_events = []
        # NOTE: slider rad/px into angleturn units/px (vanilla parity is
        # 8.0); the builder quantizes per tic, so demos store int16.
        tbuilder.mouse_units_per_px = (
            sens_rad_per_px * 65536.0 / (2 * math.pi))
        audio.engine.master = msettings.sfx_vol / 15  # options slider
        audio.music_set_volume(msettings.mus_vol)  # change-detected
        audio.music_pump()  # one OPL chunk into the mixer, if ready
        # NOTE: ext settings (volumes, sens): change-detected, so mods
        # hear slider/menu/launcher edits without per-frame spam.
        _snap = ext.settings_snapshot(msettings)
        if _snap != ext_settings_last:
            ext_settings_last = _snap
            modmgr.emit("settings", **_snap)
        # NOTE: ext gamestate (level/menu/wipe/title/inter/finale):
        # change-detected like settings, so one site covers every
        # transition (mods gate HUD art to "level" with this).
        if gamestate != ext_gamestate_last:
            _old_gs = ext_gamestate_last
            ext_gamestate_last = gamestate
            modmgr.emit("gamestate", old=_old_gs, new=gamestate)
        if gamestate == "inter" and inter is not None \
                and inter.finished_tally():
            # NOTE: tally over: wipe into the carried next level.
            old = last_fb.copy() if last_fb is not None else None
            (game_map, cam, phys, player_mo, world, mobjs, ctx,
             state) = load_map(next_map, next_keep, next_hp)
            amap = None  # new map, new automap
            map_idx = maps.index(game_map.marker)
            message, message_tics = None, 0
            pygame.display.set_caption(f"pydoom - {next_map}")
            audio.music_play(song_for_map(next_map), "inter-next")
            inter = None
            if old is None:
                gamestate = "level"
            else:
                begin_wipe(old, render_scene(), "level")
        tic_acc += dt
        modmgr.demo_guard = demo_play is not None or bool(timedemo)
        if gamestate != "level" or paused:
            tic_acc = 0  # NOTE: no catch-up burst when unpausing
        if gamestate == "menu" or \
                (gamestate == "inter" and inter is not None):
            # NOTE: menu skull + inter tally tick on 35Hz UI tics, not
            # display frames (frame-fed ticks ran them ~8x fast at
            # 240fps vs 30fps).
            ui_acc, n_ui = step_ui_clock(ui_acc, dt)
            for _ in range(n_ui):
                if gamestate == "menu":
                    game_menu.tick()
                elif gamestate == "inter" and inter is not None:
                    inter.tick()
        else:
            ui_acc = 0.0  # NOTE: no stale burst on menu/inter entry
        while tic_acc >= 1.0 / TICRATE and not state["won"] \
                and gamestate == "level" and not paused:
            tic_acc -= 1.0 / TICRATE
            modmgr.emit("pre_tic", tic=state.get("tics", 0))
            index = phys.things  # NOTE: bound up front; the thinkers
            # loop below runs after the player block (vanilla order).
            if message_tics:
                message_tics -= 1
                if not message_tics:
                    message = None
            # NOTE: vanilla P_Ticker order (players, then thinkers, then
            # specials): the player block runs first below, mobjs after
            # it, and world.tick()/leveltime close the tic at the end.
            if amap is not None:
                # NOTE: vanilla automap ticks with the gamesim, not the
                # display: zoom/pan speed stays constant at any fps.
                if am_zoom_in:
                    amap.zoom_hold(True)
                elif am_zoom_out:
                    amap.zoom_hold(False)
                else:
                    amap.zoom_release()
                amap.ticker()
            # NOTE: mobjs think after the player block below (P_RunThinkers).
            # Weapon raise ticks, then one input packet per tic: held
            # keys are sampled here (35 Hz), not per display frame.
            ps = state["ps"]
            weapons.tick_weapon(ps)
            if state["cooldown"]:
                state["cooldown"] -= 1
            tkeys = pygame.key.get_pressed()
            if demo_keys is not None:
                tkeys = demo_keys  # NOTE: replayed held-keys, not hardware
            cmd = None
            if demo_play is not None:
                # NOTE: G_Ticker demo branch: the stream drives the sim.
                cmd = demo_play.read_cmd()
                if cmd is None:
                    end_demo_playback("end")
                    continue
            if cmd is None:
                # NOTE: low-res turning only while recording (vanilla
                # G_BuildTiccmd); plain play stays full-res.
                tbuilder.lowres_turn = demo_rec is not None
                cmd = tbuilder.build(ticcmd.RawInput(
                    up=bool(tkeys[pygame.K_w] or tkeys[pygame.K_UP]),
                    down=bool(tkeys[pygame.K_s] or tkeys[pygame.K_DOWN]),
                    strafeleft=bool(tkeys[pygame.K_a]),
                    straferight=bool(tkeys[pygame.K_d]),
                    turnleft=bool(tkeys[pygame.K_LEFT]),
                    turnright=bool(tkeys[pygame.K_RIGHT]),
                    speed=bool(tkeys[pygame.K_LSHIFT]
                               or tkeys[pygame.K_RSHIFT]),
                    attack=bool(state["firing"]
                                or tkeys[pygame.K_SPACE])))
                if state.pop("atk_latch", False):
                    # NOTE: latched edge lands on this live packet
                    # (demo streams bypass the builder, untouched).
                    cmd.buttons |= ticcmd.BT_ATTACK
                # NOTE: ext build_ticcmd (movement mods): live packets
                # only (playback stays bit-exact); the filtered packet
                # is what gets recorded, so demos/checksums cover it.
                # Broken mod values never corrupt the packet.
                tev = modmgr.emit("build_ticcmd",
                                  forwardmove=cmd.forwardmove,
                                  sidemove=cmd.sidemove,
                                  angleturn=cmd.angleturn,
                                  buttons=cmd.buttons)
                try:
                    cmd.forwardmove = int(tev.forwardmove)
                    cmd.sidemove = int(tev.sidemove)
                    cmd.angleturn = int(tev.angleturn)
                    cmd.buttons = int(tev.buttons)
                except (TypeError, ValueError):  # noqa: BLE001 - keep packet
                    pass
                if demo_rec is not None:
                    demo_rec.append(cmd)
            ps.cmd = cmd  # NOTE: friction reads the move axes (P_XYMovement)
            if player_mo.flags & _MF_FLAGS["MF_JUSTATTACKED"]:
                # NOTE: chainsaw lunge (vanilla P_PlayerThink): the next
                # packet drives straight forward, ignoring the turn keys.
                cmd.angleturn = 0
                cmd.forwardmove = 0xC800 // 512
                cmd.sidemove = 0
                player_mo.flags &= ~_MF_FLAGS["MF_JUSTATTACKED"]
            if not (kinematic or noclip) \
                    and ps.playerstate == p_user.PST_LIVE \
                    and player_mo.health > 0:
                # NOTE: angle+thrust run before firing (vanilla P_MovePlayer
                # precedes P_MovePsprites), so shots use this tic's exact
                # integer angle; XY/friction stay at the move site below.
                if player_mo.reactiontime:
                    player_mo.reactiontime -= 1
                else:
                    p_user.move_player(player_mo, cmd, set_mobj_state)
                cam.angle = (player_mo.angle * 2 * math.pi / 0x100000000)
            # NOTE: BT_USE edges through usedown (vanilla P_MovePlayer
            # runs use before psprites/fire): holding E must not
            # re-trigger doors every tic. Dead bodies wait for USE to
            # reborn (death_think below), never use lines.
            if (cmd.buttons & ticcmd.BT_USE and not ps.usedown
                    and ps.playerstate == p_user.PST_LIVE):
                # NOTE: use-aim is the exact integer angle on the vanilla
                # path (cam.bam rounds through float); legacy uses cam.
                aim = (player_mo.angle if not (kinematic or noclip)
                       else cam.bam)
                message = world.use_lines(
                    player_mo.x, player_mo.y, aim, phys,
                    state["ps"].keys, player_mo, mobjs)
                message_tics = 3 * TICRATE if message else 0
                if world.teleport_angle is not None:
                    cam.angle = (world.teleport_angle
                                 * 2 * math.pi / 0x100000000)
                    cam.x = player_mo.x / 65536.0
                    cam.y = player_mo.y / 65536.0
                    world.teleport_angle = None
                ps.usedown = True
            elif not cmd.buttons & ticcmd.BT_USE:
                ps.usedown = False
            # NOTE: damaging floors/secrets (vanilla P_PlayerThink runs
            # these pre-move, while still airborne from last tic: the
            # z-gate skips falling bodies, so stairs don't zap mid-drop).
            if ps.playerstate == p_user.PST_LIVE:
                sec_msg = world.player_in_special_sector(player_mo, ps, ctx)
                if sec_msg is not None:
                    message, message_tics = sec_msg, 3 * TICRATE
            want_fire = bool(cmd.buttons & ticcmd.BT_ATTACK)
            if weapons.tick_pending(ps, player_mo, phys, index, mobjs,
                                    renderer.skyflatnum, ctx,
                                    state["pending"]) \
                    and player_mo.health > 0:
                # NOTE: a scheduled muzzle flash shows (its shot may
                # follow later, or never, if the gun lowered).
                _body, _flash = weapons.PSPRITES[ps.readyweapon]
                if _flash is not None:
                    state["flash_until"] = (
                        state.get("tics", 0)
                        + weapons.FLASH_TICS[ps.readyweapon])
            cd_now = state["cooldown"]
            edge_release = (not want_fire) and bool(state.get("refire"))
            chained_cycle = bool(state.get("atkheld"))
            if not want_fire:
                state["atkheld"] = False  # NOTE: released: chain over
            elif ps.pendingweapon != ps.readyweapon:
                state["atkheld"] = False  # NOTE: switch aborts the chain
            atkheld = bool(state.get("atkheld"))
            chained = weapons.chained_pull(cd_now, atkheld,
                                           ps.readyweapon)
            if want_fire and player_mo.health > 0 \
                    and (cd_now == 0 or chained):
                if kinematic or noclip:
                    player_mo.angle = cam.bam  # NOTE: legacy: camera leads
                held_now = chained  # NOTE: short entry cycle (A_ReFire)
                cd, flash_now = weapons.fire(
                    ps, player_mo, phys, index, mobjs,
                    renderer.skyflatnum, accurate=not state["refire"],
                    ctx=ctx, queue=state["pending"], held=held_now)
                if cd >= 0:
                    state["cooldown"] = cd
                    body, flash = weapons.PSPRITES[ps.readyweapon]
                    # NOTE: +1 covers the refire tic itself: the chained
                    # pull lands at entry T+cd (decrement runs first),
                    # while renders already read tics==T+cd, so plain
                    # T+cd leaves one idle tic per cycle (vanilla chains
                    # inside the 0-tic refire state, no gap). Rendered
                    # attack tics then equal cd, and the timeline runs
                    # from index 0 instead of skipping its first frame
                    # (span stays cd: elapsed = tics-T-1 runs 0..cd-1).
                    state["atk_until"] = state.get("tics", 0) + cd + 1
                    state["atk_span"] = max(1, cd)
                    state["atkheld"] = held_now
                    if ps.readyweapon == WP_CHAINGUN:
                        # NOTE: vanilla shows one body frame per 4-tic
                        # pull, alternating A/B across pulls.
                        state["atkflip"] = 1 - state.get("atkflip", 0)
                    if flash_now and flash is not None:
                        state["flash_until"] = (
                            state.get("tics", 0)
                            + weapons.FLASH_TICS[ps.readyweapon])
                # NOTE: cd < 0 means still switching or just auto-switched
                # off a dry gun (vanilla never clicks empty).
            if edge_release and chained_cycle and player_mo.health > 0 \
                    and ps.pendingweapon == ps.readyweapon:
                # NOTE: vanilla plays out the refire-state tail on
                # release (S_PISTOL4/S_PLASMA2/..., the B frames a tap
                # always shows) instead of snapping to idle: extend the
                # cycle by REFIRE_AT tics, phase-preserved (span grows
                # too, so the FULL tail reads to its last frame). Taps
                # and 0-tail guns (chaingun/missile/saw) no-op here.
                tail = weapons.REFIRE_AT.get(ps.readyweapon, 0)
                if tail:
                    state["atk_until"] = state.get("atk_until", 0) + tail
                    state["atk_span"] = state.get("atk_span", 1) + tail
            state["refire"] = want_fire
            if (cmd.buttons & ticcmd.BT_CHANGE
                    and ps.playerstate == p_user.PST_LIVE):
                # NOTE: bit i is digit i+1 through the KEYMAP toggle
                # (request_weapon is idempotent, holds don't stall it).
                weapons.request_weapon(
                    ps, str(((cmd.buttons & ticcmd.BT_WEAPONMASK)
                             >> ticcmd.BT_WEAPONSHIFT) + 1))
            # NOTE: Doomguy face ticks with the gamesim (ST_updateFaceWidget).
            state["facelump"] = update_face(state["face"], ps, player_mo,
                                            bool(want_fire))
            if (ps.readyweapon == WP_CHAINSAW
                    and ps.pendingweapon == WP_CHAINSAW
                    and not state["cooldown"]
                    and state.get("tics", 0) % 8 == 0
                    and not audio.engine.playing(("sawhit", "sawful"),
                                                 player_mo)):
                # NOTE: A_WeaponReady revs only on S_SAW entries (the
                # S_SAW/S_SAWB pair ticks 4+4), so every 8 tics, not 4.
                # Tails ring out first: the rev waits for a free moment.
                audio.play("sawidl", player_mo.x, player_mo.y, player_mo)
            if player_mo.health <= 0 \
                    and ps.playerstate == p_user.PST_LIVE:
                # NOTE: PST_DEAD: the view falls and waits for USE
                # (P_DeathThink); the corpse keeps no inventory yet.
                # The key hint is display-only (rebirth itself stays
                # vanilla: USE edge, like the original).
                ps.playerstate = p_user.PST_DEAD
                message, message_tics = "YOU DIED - press USE (E)", \
                    3 * TICRATE
            dead_this_tic = False
            if ps.playerstate == p_user.PST_DEAD:
                dead_viewz = p_user.death_think(
                    ps, player_mo, state.get("tics", 0),
                    bool(cmd.buttons & ticcmd.BT_USE))
                cam.viewz = dead_viewz / 65536.0
                cam.angle = (player_mo.angle * 2 * math.pi / 0x100000000)
                # NOTE: weapon sway chases the (decaying) corpse momentum
                # like vanilla: no frozen bob over the death fall.
                state["bobamp"] = min(16, ps.bob >> 16)
                if ps.playerstate == p_user.PST_REBORN:
                    # NOTE: vanilla SP rebirth reloads the level from
                    # scratch (G_DoReborn -> ga_loadlevel, no tally):
                    # fresh map and pistol+50, tallies kept, RNG stream
                    # untouched (multiplayer respawns in place instead).
                    keep_kills = ps.killcount
                    keep_items = ps.itemcount
                    keep_secrets = ps.secretcount
                    (game_map, cam, phys, player_mo, world, mobjs, ctx,
                     state) = load_map(game_map.marker)
                    index = phys.things
                    state["ps"].killcount = keep_kills
                    state["ps"].itemcount = keep_items
                    state["ps"].secretcount = keep_secrets
                    state["ps"].usedown = True
                    message, message_tics = None, 0
                    ps = state["ps"]
                    dead_this_tic = False
                    # NOTE: melt into the fresh level (presentation only;
                    # vanilla cuts instantly): softens the reload snap.
                    # The reloaded world starts thinking next tic, like
                    # vanilla's tick-boundary ga_loadlevel.
                    old = last_fb.copy() if last_fb is not None else None
                    begin_wipe(old, render_scene(), "level")
                    continue
                else:
                    # NOTE: corpse waits for USE (vanilla P_DeathThink):
                    # no fire/move/pickup, but mobjs, specials and the
                    # clock keep running below (never freeze the world).
                    dead_this_tic = True
            if (kinematic or noclip) and not dead_this_tic:
                # NOTE: legacy camera mover (--kinematic, debug noclip):
                # the packet drives position directly, no momentum.
                if tkeys[pygame.K_LSHIFT] or tkeys[pygame.K_RSHIFT]:
                    tic_scale = RUN_SPEED / 50.0
                else:
                    tic_scale = WALK_SPEED / 25.0
                cam.turn(ticcmd.angleturn_to_rad(cmd.angleturn))
                fwd = cmd.forwardmove * tic_scale
                strafe = cmd.sidemove * tic_scale
            else:
                # NOTE: vanilla momentum path (milestone B): angle+thrust
                # ran right after the build (P_MovePlayer before firing);
                # here come CalcHeight, then friction/slide in xy_movement.
                # The camera only follows the integer body.
                if not dead_this_tic:
                    onground = player_mo.z <= player_mo.floorz
                    cam.angle = (player_mo.angle * 2 * math.pi / 0x100000000)
                    cam.viewz = p_user.calc_height(
                        ps, player_mo, state.get("tics", 0),
                        onground) / 65536.0
                    state["bobamp"] = min(16, ps.bob >> 16)
                fwd = strafe = 0.0
            if fwd or strafe:
                # NOTE: weapon bob amplitude chases speed (P_CalcHeight);
                # the sway phase rides the tic clock, like vanilla.
                state["bobamp"] = min(state.get("bobamp", 0) + 2, 16)
                dx = (math.cos(cam.angle) * fwd
                      + math.sin(cam.angle) * strafe)
                dy = (math.sin(cam.angle) * fwd
                      - math.cos(cam.angle) * strafe)
                if noclip:
                    cam.x += dx
                    cam.y += dy
                    if index is not None:
                        index.move(player_mo,
                                   int(cam.x * 65536), int(cam.y * 65536))
                    refresh_sector(player_mo, phys)
                else:
                    crossed: list = []
                    nx = player_mo.x + int(dx * 65536)
                    ny = player_mo.y + int(dy * 65536)
                    ok, got = phys.try_move(player_mo, nx, ny)
                    if ok:
                        crossed.extend(got)
                        cam.x = player_mo.x / 65536.0
                        cam.y = player_mo.y / 65536.0
                    else:
                        # NOTE: blocked touches fire too (vanilla
                        # PIT_CheckLine): pushing a pillar face or
                        # sliding along a walk trigger counts.
                        crossed.extend(got)
                        player_mo.momx, player_mo.momy = (
                            int(dx * 65536), int(dy * 65536))
                        phys.slide_move(player_mo, crossed)
                        cam.x = player_mo.x / 65536.0
                        cam.y = player_mo.y / 65536.0
                    for line, side in crossed:
                        msg = world.cross_special_line(line, True, player_mo,
                                                       phys, mobjs, side)
                        if msg is not None:
                            message, message_tics = msg, 3 * TICRATE
            else:
                # NOTE: standing still settles the weapon (P_CalcHeight);
                # the vanilla path already set bobamp from player->bob.
                if kinematic or noclip:
                    state["bobamp"] = max(state.get("bobamp", 0) - 4, 0)
            if not (kinematic or noclip) and not dead_this_tic:
                # NOTE: friction/slide/crossing (P_XYMovement); the camera
                # follows the integer body, like the renderer follows mo.
                # Corpses hold still (their view falls via death_think).
                crossed_v: list = []
                if player_mo.momx or player_mo.momy:
                    ox, oy = player_mo.x, player_mo.y
                    crossed_v = xy_movement(player_mo, phys, ctx)
                    if (player_mo.x, player_mo.y) != (ox, oy):
                        refresh_sector(player_mo, phys)
                    cam.x = player_mo.x / 65536.0
                    cam.y = player_mo.y / 65536.0
                for line, side in crossed_v:
                    msg = world.cross_special_line(line, True, player_mo,
                                                   phys, mobjs, side)
                    if msg is not None:
                        message, message_tics = msg, 3 * TICRATE
            if not noclip and not dead_this_tic:
                # Floor/ceiling refresh (lifts carry standing bodies).
                res = phys.check_position(
                    player_mo, player_mo.x, player_mo.y)
                if res.ok:
                    player_mo.floorz, player_mo.ceilingz = (
                        res.floorz, res.ceilingz)
                    if not kinematic:
                        # NOTE: P_ZMovement smooth step-up: stairs dip
                        # the view, calc_height walks it back.
                        p_user.z_step_adjust(ps, player_mo)
                if kinematic:
                    # NOTE: legacy gravity lite + floor glue (comparison
                    # path only): ride lifts up, fall fast, snap close.
                    if res.ok:
                        if player_mo.z > player_mo.floorz:
                            player_mo.z = max(player_mo.floorz,
                                              player_mo.z - 8 * 65536)
                        else:
                            player_mo.z = player_mo.floorz
                elif p_user.z_movement(player_mo, ps):
                    audio.play("oof", player_mo.x, player_mo.y, player_mo)
            # Walk-over pickups (P_TouchSpecialThing sweep) + power ticks.
            ps = state["ps"]
            ps.tick(player_mo)
            if ps.playerstate == p_user.PST_LIVE:
                got = collect_touched(phys, player_mo, ps, ctx)
                if got is not None:
                    message, message_tics = got, 3 * TICRATE
            if world.teleport_angle is not None:
                # NOTE: W1 teleports land mid-stride (E1M8 exit chain).
                cam.angle = (world.teleport_angle
                             * 2 * math.pi / 0x100000000)
                cam.x = player_mo.x / 65536.0
                cam.y = player_mo.y / 65536.0
                world.teleport_angle = None
            # NOTE: ext player_think (player timers/states): after the
            # whole vanilla player block (move, use, fire, pickups),
            # before mobjs think (P_RunThinkers order).
            modmgr.emit("player_think", tic=state.get("tics", 0))
            if modmgr.subscribed("aim"):
                # NOTE: ext aim (crosshair mods): exact center-line hitscan
                # (no auto-aim spread: the dot shows what you point at).
                # One ray per tic, skipped entirely with no listeners.
                _aim_hit = False
                _pst = state["ps"]
                if _pst.playerstate == p_user.PST_LIVE \
                        and player_mo.health > 0:
                    _slope, _tgt = combat.aim_line_attack(
                        player_mo, player_mo.angle, combat.MISSILERANGE,
                        phys, index, mobjs, renderer.skyflatnum)
                    _aim_hit = _tgt is not None
                modmgr.emit("aim", target=_aim_hit)
            # Think mobjs (P_RunThinkers on a live list: thinkers born
            # this tic think right away, like vanilla's head-to-tail
            # walk; removal holds the index so the next body slides in).
            index = phys.things
            _mi = 0
            while _mi < len(mobjs):
                mo = mobjs[_mi]
                if mo is player_mo and ps.playerstate == p_user.PST_LIVE:
                    # NOTE: the live body moves in the player block
                    # above (camera-driven); it never thinks here. Only
                    # its state clock runs (pain frames still reach
                    # S_PLAY_PAIN2's A_Pain, like vanilla). The corpse
                    # does (death states, scream, slide, crush).
                    tick_mobj_state(mo, ctx)
                    _mi += 1
                    continue
                crossed_mo = think_mobj(mo, phys, ctx)
                for line, side in crossed_mo:
                    world.cross_special_line(line, mo.is_player, mo,
                                             phys, mobjs, side)
                if not sweep_dead(mobjs, index, _mi):
                    # NOTE: live -1-tic corpses stay for crush/respawn/
                    # render; everything swept (puffs, missiles, fog,
                    # S_NULL barrels) is behavior-neutral to drop.
                    _mi += 1
            if world.exit_kind:
                cur = game_map.marker
                nxt = flow.next_map(cur, world.exit_kind == "secret")
                # NOTE: ext level_exit (progression mods, stats): notify
                # only, the transition below already decided.
                modmgr.emit("level_exit", exited=cur, entering=nxt,
                            secret=world.exit_kind == "secret")
                ps_exit, hp_exit = state["ps"], player_mo.health
                if demo_play is not None:
                    # NOTE: desync-detector checkpoint (statdump-style).
                    tk, ti, ts = world.totals
                    print(f"demo: exit {cur} -> {nxt} t={world.time} "
                          f"k={ps_exit.killcount}/{tk} "
                          f"i={ps_exit.itemcount}/{ti} "
                          f"s={ps_exit.secretcount}/{ts}")
                try:
                    par = interm.PARS[int(cur[1]) - 1][int(cur[3:])] * 35
                except (ValueError, IndexError):
                    par = 0
                old = (last_fb.copy() if last_fb is not None
                       else render_scene())
                if nxt is None:
                    # NOTE: E1M8 exit melts to the black finale screen.
                    # During playback the run ends here (vanilla would
                    # keep consuming stream tics behind the text).
                    if demo_play is not None:
                        end_demo_playback("victory")
                        continue
                    begin_wipe(old, np.zeros((200, 320), dtype=np.uint8),
                               "finale")
                    audio.music_play(FINALE_SONG, "exit-finale")
                else:
                    tk, ti, ts = world.totals
                    inter = interm.Intermission(
                        cur, nxt, ps_exit.killcount, tk,
                        ps_exit.itemcount, ti, ps_exit.secretcount, ts,
                        world.time, par)
                    next_map, next_keep, next_hp = nxt, ps_exit, hp_exit
                    first = np.zeros((200, 320), dtype=np.uint8)
                    inter.draw(first, game_menu)
                    begin_wipe(old, first, "inter")
                    audio.music_play(INTER_SONG, "exit-inter")
            # Ease viewz toward standing height on the current floor
            # (legacy/ noclip only: the vanilla path sets cam.viewz from
            # P_CalcHeight every tic, no smoothing like vanilla).
            if kinematic or noclip:
                if noclip:
                    sub = renderer.sector_at(
                        game_map, int(cam.x * 65536), int(cam.y * 65536)
                    )
                    floor = (sub.sector.floorheight / 65536.0
                             if sub.sector is not None else cam.viewz)
                else:
                    floor = player_mo.z / 65536.0
                target = floor + VIEWHEIGHT_ABOVE_FLOOR
                cam.viewz += (target - cam.viewz) * 0.3
            # NOTE: ext camera (mouselook) runs after every default height
            # path, so handled=True wins over calc_height and the spring.
            # R/C mirror here to avoid indexing replayed key states.
            try:
                _kup = bool(tkeys[pygame.K_r])
                _kdn = bool(tkeys[pygame.K_c])
            except Exception:  # noqa: BLE001 - demo key states vary
                _kup, _kdn = False, False
            cev = modmgr.emit("camera", viewz=cam.viewz, pitch=cam_pitch,
                              keys_up=_kup, keys_down=_kdn, handled=False)
            # NOTE: the death cam owns viewz while dead (PST_DEAD flow).
            if cev.handled and \
                    state["ps"].playerstate == p_user.PST_LIVE:
                cam.viewz = float(cev.viewz)
                cam_pitch = float(cev.pitch)
            elif cam_pitch:
                cam_pitch *= 0.8  # NOTE: ease back to 0 without mouselook
            # NOTE: leveltime closes the tic (vanilla P_Ticker): specials
            # think last, so doors/lights/crush see post-move bodies.
            state["tics"] = state.get("tics", 0) + 1
            modmgr.emit("post_tic", tic=state["tics"])
            # Door thinkers, buttons, crush checks (blocker = player).
            # NOTE: vanilla P_ChangeSector sees every body overlapping
            # the moving sector, not just the center point: sample the
            # bbox corners too, so a closing door catches you standing
            # on its threshold instead of sealing you inside solid rock
            # (no fit -> stuck under the map).
            radius = player_mo.radius
            touched: list = []  # NOTE: order-stable dedup (center first):
            for px, py in ((player_mo.x, player_mo.y),  # no hash-order here
                           (player_mo.x - radius, player_mo.y - radius),
                           (player_mo.x + radius, player_mo.y - radius),
                           (player_mo.x - radius, player_mo.y + radius),
                           (player_mo.x + radius, player_mo.y + radius)):
                sub = phys.subsector_at(px, py)
                if sub.sector is not None and sub.sector not in touched:
                    touched.append(sub.sector)
            world.blocker = (
                (tuple(touched), player_mo.z, player_mo.height)
                if touched else None
            )
            world.tick()
            if checksum_path is not None and demo_play is not None:
                trace_sim_tic()

        audio.set_listener(player_mo.x, player_mo.y, cam.bam)
        use_gl = (gl_live and gl_frame is not None
                  and not (recording or replaying))
        if amap is not None:
            # NOTE: ext automap_draw (custom markers): mods append (x, y)
            # fixed world coords, drawn on both paths via _draw_marks.
            amap.mod_marks = []
            modmgr.emit("automap_draw", marks=amap.mod_marks, amap=amap)
            # NOTE: fullscreen automap (TAB): the game keeps running.
            amap.plr_x, amap.plr_y = player_mo.x, player_mo.y
            amap.plr_angle = cam.bam
            if use_gl:
                try:
                    segs = amap.collect_segments(mobjs
                             if (amap.cheating == 2
                                 or state["ps"].powers.get(PW_ALLMAP))
                             else None)
                    gl_frame.clear_window()
                    gl_frame.draw_automap(segs)
                    gl_draw_version()
                except Exception as exc:  # noqa: BLE001 - frame fallback
                    print(f"gl automap: {exc} (software fallback)")
                    use_gl = False
            if not use_gl:
                screen.fill((0, 0, 0))
                amap.draw(screen, mobjs
                           if (amap.cheating == 2
                               or state["ps"].powers.get(PW_ALLMAP))
                           else None)
            if not use_gl and font is not None:
                hint = font.render(
                    "AUTOMAP +-zoom F-follow G-grid TAB-close",
                    True, (180, 180, 180))
                screen.blit(hint, (8, WIN_H - 24))
            # NOTE: automap hint/help text stays software-only.
            pygame.display.flip()
            frames += 1
            if frames_opt is not None and frames >= frames_opt:
                print(f"smoke: {frames} frames, {fps_ema:.0f}fps ema")
                running = False
            continue
        if timedemo:
            # NOTE: no draw/blit at all (vanilla -nodraw/-noblit); the
            # sim still ticks on fixed steps above.
            frames += 1
            if frames_opt is not None and frames >= frames_opt:
                running = False
            continue
        if has_level:
            fb = render_scene(with_view=not use_gl)
        else:
            fb = np.zeros((200, 320), dtype=np.uint8)
            game_menu.draw_title(fb)  # NOTE: TITLESCREEN backdrop
        if gamestate == "menu":
            game_menu.draw(fb)  # NOTE: menu floats over the frozen sim
        elif paused and gamestate == "level":
            pw = game_menu._patch_w("M_PAUSE")
            game_menu._blit("M_PAUSE", fb, (320 - pw) // 2, 4)
        elif gamestate == "inter" and inter is not None:
            fb = np.zeros((200, 320), dtype=np.uint8)
            inter.draw(fb, game_menu)
        elif gamestate == "wipe":
            # NOTE: idle frames re-show the last melt step (vanilla
            # keeps wipe_scr); re-rendering would flash the end scene.
            # A broken melt frame lands instantly (never tiling/black).
            stepped, melt_acc, landed = step_wipe_clock(
                melt, melt_acc, dt)
            if landed:
                if debug:
                    print(f"melt: landed after={wipe_after} "
                          f"frame={frames}")
                gamestate = wipe_after
                # NOTE: the landing frame must show the destination
                # screen, not the stale render_scene fb (in GL mode that
                # fb carries the status bar, which flashed one frame
                # over intermission/finale screens).
                if wipe_after == "inter" and inter is not None:
                    fb = np.zeros((200, 320), dtype=np.uint8)
                    inter.draw(fb, game_menu)
                elif wipe_after == "finale":
                    fb = np.zeros((200, 320), dtype=np.uint8)
            else:
                if debug and melt.total // 10 != wipe_dbg_mark:
                    wipe_dbg_mark = melt.total // 10
                    _dump_wipe_frame(
                        f"w{frames:06d}-t{melt.total:03d}", stepped)
                fb = stepped
        last_fb = fb.copy()
        # NOTE: ext post_overlay (cosmetic HUD): after the last_fb copy so
        # mod drawings never leak into wipes or snapshots.
        modmgr.emit("post_overlay", fb=fb)
        if recording or replaying:
            demo_sum = (demo_sum + int(fb.sum())) % 1000000007
        if use_gl:
            # NOTE: GL present (world + gun + overlay + text); any
            # failure falls back to software for this frame (the
            # fullscreen blit below overwrites the half-drawn back
            # buffer, so recovery is clean).
            try:
                gl_present_all(fb, palette_index(state["ps"]))
                gl_draw_hud()
            except Exception as exc:  # noqa: BLE001 - frame fallback
                print(f"gl present: {exc} (software fallback)")
                use_gl = False
        if not use_gl:
            # NOTE: a GL-backed window can never show software blits
            # (black forever): if the GL renderer is structurally gone
            # (not a one-frame present failure), downgrade the window
            # once instead of presenting black.
            if gl_frame is None and win_backend != "software" \
                    and not win_heal_failed:
                try:
                    from pydoom.glrender import state as _heal_state
                    hw, hh, hf, hd = video_geom(msettings, False)
                    screen = _heal_state.positioned_set_mode(
                        (hw, hh), hf, hd)
                    WIN_W, WIN_H = screen.get_width(), screen.get_height()
                    win_backend = "software"
                    modmgr.set_backend(win_backend)  # NOTE: re-gate mods
                    amap = None
                    print(f"video: healed to software {WIN_W}x{WIN_H}")
                except Exception as exc:  # noqa: BLE001 - stay black?
                    print(f"video: heal failed ({exc})")
                    win_heal_failed = True
            frame = pygame.image.frombuffer(
                palette_luts[palette_index(state["ps"])][fb].tobytes(),
                (SCREENWIDTH, SCREENHEIGHT), "RGB"
            )
            # NOTE: integer-scale letterbox (windowed sizes match exactly,
            # fullscreen/borderless centers with black bars, never stretch).
            bw, bh, bx, by = menu.letterbox(WIN_W, WIN_H)
            if (bw, bh) != (SCREENWIDTH, SCREENHEIGHT):
                frame = pygame.transform.scale(frame, (bw, bh))
            screen.fill((0, 0, 0))
            screen.blit(frame, (bx, by))
        if not use_gl and font is not None:
            # NOTE: readout block + help line (same strings/positions
            # as the GL text quads above, via hud_lines()).
            for text, rgb, alpha, x, y in hud_lines():
                img = font.render(text, True, rgb)
                img.set_alpha(alpha)
                screen.blit(img, (x, y))
        if not use_gl and font is not None and gamestate == "finale":
            big = font.render("EPISODE 1 COMPLETE", True, (255, 255, 0))
            screen.blit(big, (WIN_W // 2 - big.get_width() // 2,
                               WIN_H // 2 - 130))
            # NOTE: E1TEXT (d_englsh.h), the episode payoff.
            for i, text_line in enumerate(_E1TEXT_LINES):
                small = font.render(text_line, True, (200, 200, 200))
                screen.blit(small, (WIN_W // 2 - small.get_width() // 2,
                                    WIN_H // 2 - 90 + i * 20))
            sub = font.render("ANY KEY: TITLE",
                              True, (255, 255, 255))
            screen.blit(sub, (WIN_W // 2 - sub.get_width() // 2,
                              WIN_H // 2 + 130))
        if not use_gl and font is not None:
            # NOTE: build tag, always on top-right (~38% ghost): every
            # screenshot names its code, no flags needed for bug reports.
            ver_img = font.render(f"v{ver}", True, (255, 255, 255))
            ver_img.set_alpha(96)
            screen.blit(ver_img, (WIN_W - 8 - ver_img.get_width(), 8))
        pygame.display.flip()
        frames += 1
        if frames_opt is not None and frames >= frames_opt:
            print(f"smoke: {frames} frames, {fps_ema:.0f}fps ema")
            running = False
        if profile_opt is not None and frames >= profile_opt:
            # NOTE: v2 profiler report (Openglv2 only; other backends
            # have no profiler yet).
            prof = getattr(gl_frame, "prof", None)
            if prof is not None:
                print(prof.report())
                from pydoom.glrenderer2 import gl as _profgl
                print(f"profile: GL calls={_profgl.stats()}")
            else:
                print("profile: no profiler on this backend")
            running = False

    if recording:
        import pickle
        with open(rec_path, "wb") as f:
            pickle.dump(demo_log, f)
        print(f"demo: recorded {len(demo_log)} frames, checksum {demo_sum}")
    if play_path is not None:
        print(f"demo: replayed {demo_idx} frames, checksum {demo_sum}")
    finish_demo_rec()
    if checksum_path is not None:
        with open(checksum_path, "w") as f:
            f.write("# pydoom sim trace v1: stream-tic leveltime marker "
                    "x y z angle hp momx momy rng0 rng1 alive kills\n")
            f.write("\n".join(checksum_lines) + "\n")
        print(f"demo: wrote {len(checksum_lines)} trace lines"
              f" -> {checksum_path}")
    audio.music_shutdown()  # song thread out before the mixer dies
    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
