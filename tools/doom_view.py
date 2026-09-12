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
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pygame

from pydoom import combat
from pydoom import cheats
from pydoom import demo
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
from pydoom.mobjs import ThingIndex, refresh_sector, spawn_map, spawn_mobj
from pydoom.mobjs import level_totals, set_mobj_state, think_mobj
from pydoom.mobjs import xy_movement
from pydoom.palette import NUM_PALETTES, load_playpal, load_playpal_index
from pydoom.physics import MF_NOCLIP, Physics
from pydoom.pickup import collect_touched
from pydoom.player import (
    CF_NOCLIP,
    PW_ALLMAP,
    PW_INFRARED,
    PlayerState,
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
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    frames_opt = None
    skill = "normal"
    fast = False
    debug = False  # dev keys (N/F/X/PgUp/...) stay behind this flag
    rec_path = None  # --record=FILE: log per-frame inputs (fixed dt)
    play_path = None  # --play=FILE: replay them (regression demos)
    rec_demo_path = None  # --record-demo=FILE: vanilla-format .lmp
    play_demo_path = None  # --playdemo=FILE: play a vanilla .lmp
    timedemo = False  # --timedemo=FILE: play fast, no draw, report
    checksum_path = None  # --dump-checksums=FILE: per-tic sim trace
    nomonsters = False  # demo header / vanilla -nomonsters spawn filter
    respawn = False  # --respawn: monsters return (any skill, like vanilla)
    kinematic = False  # --kinematic: legacy camera mover (milestone B)
    for a in sys.argv[1:]:
        if a.startswith("--frames="):
            frames_opt = int(a.split("=", 1)[1])
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
    try:  # NOTE: build tag in the HUD, so screenshots name their code.
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        ver = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True,
            text=True, cwd=root, timeout=5).stdout.strip() or "nogit"
    except Exception:
        ver = "nogit"
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

    def load_map(marker: str, keep_ps=None, keep_hp: int | None = None):
        game_map = Map.from_wad(wad, marker)
        # NOTE: g_game.c picks SKY1/2/3/4 per episode at P_SetupLevel.
        try:
            renderer.skytexture = texman.texture_num_for_name(
                f"SKY{marker[1]}")
        except Exception:
            pass
        texman.resolve_map(game_map)
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
        return game_map, cam, phys, player_mo, world, mobjs, ctx, state

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
    gamestate = "level"  # level|menu|wipe|title|inter|finale
    inter = None  # tally screen between maps (G_WorldDone lite)
    wipe_after = "level"  # melt landing state
    next_map, next_keep, next_hp = None, None, None  # inter exit
    if frames_opt is None and not debug:
        gamestate = "title"  # NOTE: vanilla boots to TITLESCREEN
    has_level = gamestate == "level"  # menu-close target before new game
    melt = MeltWipe()
    last_fb = None
    # NOTE: M_QuitDOOM death jingle (shareware picks the first table).
    QUITSOUNDS = ("pldeth", "dmpain", "popain", "slop", "telept",
                  "posit1", "posit3", "sgtatk")

    def render_scene():
        """One frozen-sim scene frame (psprites + status bar included)."""
        # NOTE: muzzle-flash room light (A_Light1/2 levels, with the
        # shotgun/BFG step-up mid-flash), like the psprite flash.
        ps = state["ps"]
        extra = 0
        left = state.get("flash_until", 0) - state.get("tics", 0)
        if left > 0:
            extra = weapons.FLASH_LIGHT[ps.readyweapon]
            split = weapons.FLASH_LIGHT_STEP.get(ps.readyweapon)
            if split is not None and \
                    weapons.FLASH_TICS[ps.readyweapon] - left >= split[0]:
                extra = split[1]
        fb = renderer.render_view(
            game_map, int(cam.x * 65536), int(cam.y * 65536), cam.bam,
            int(cam.viewz * 65536), mobjs, extra_light=extra,
            fullbright=bool(state["ps"].powers.get(PW_INFRARED)),
        )
        # NOTE: P_DrawPlayerSprites lite: ready gun + muzzle flash, bob,
        # lower/raise travel while switching, kick frame while firing.
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
        if attacking:
            # NOTE: body frames ride the full attack cycle (p_pspr.c),
            # so kicks read instead of blinking past.
            span = max(1, state.get("atk_span", 1))
            elapsed = span - (state["atk_until"] - state.get("tics", 0))
            timeline = weapons.ATTACK_BODY[ps.readyweapon]
            pick = timeline[min(len(timeline) - 1, max(0, elapsed))]
            if not renderer.draw_psprite(fb, body, bobx, boby + yoff,
                                         pick):
                renderer.draw_psprite(fb, body, bobx, boby + yoff, "A")
        else:
            renderer.draw_psprite(fb, body, bobx, boby + yoff, "A")
        if firing and flash is not None:
            renderer.draw_psprite(fb, flash, bobx, boby + yoff)
        # NOTE: classic bottom strip (covers the gun base, like vanilla).
        draw_status_bar(renderer, fb, ps, player_mo.health,
                        state.get("facelump", "STFST00"))
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
            melt.start(old, render_scene())
            wipe_after = "level"
            gamestate = "wipe"

    def apply_menu_event(mev):
        """Menu selections: quit, or a wiped fresh start on E1M1."""
        nonlocal gamestate, game_map, cam, phys, player_mo, world, \
            mobjs, ctx, state, map_idx, amap, message, message_tics, \
            noclip, skill, running, has_level, paused, quickslot, \
            demo_play
        if mev == "close":
            gamestate = "level" if has_level else "title"
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
                melt.start(old, render_scene())  # menu melts away
                gamestate = "wipe"

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
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

    def finish_demo_rec() -> None:
        """Append DEMOMARKER and flush the .lmp (G_CheckDemoStatus tail)."""
        nonlocal demo_rec
        if demo_rec is not None and rec_demo_path is not None:
            with open(rec_demo_path, "wb") as f:
                f.write(demo_rec.finish())
            print(f"demo: recorded {demo_rec.tics} tics"
                  f" -> {rec_demo_path}")
            demo_rec = None

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
            dt = min(clock.tick(60) / 1000.0, 0.25)
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
                    # forward (up), so push-away walks forward.
                    tbuilder.add_mouse(ev.rel[0], -ev.rel[1])
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if gamestate == "level" and ev.button == 1:
                    state["firing"] = True
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
            (0.0004 + msettings.mouse_sens * 0.0006) * 65536.0
            / (2 * math.pi))
        audio.engine.master = msettings.sfx_vol / 15  # options slider
        audio.music_set_volume(msettings.mus_vol)  # change-detected
        audio.music_pump()  # one OPL chunk into the mixer, if ready
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
                melt.start(old, render_scene())
                wipe_after = "level"
                gamestate = "wipe"
        tic_acc += dt
        if gamestate != "level" or paused:
            tic_acc = 0  # NOTE: no catch-up burst when unpausing
            if frames % 2 == 0:
                if gamestate == "menu":
                    game_menu.tick()  # skull animates at ~half rate
                elif gamestate == "inter" and inter is not None:
                    inter.tick()  # tally count-up sweep
        while tic_acc >= 1.0 / TICRATE and not state["won"] \
                and gamestate == "level" and not paused:
            tic_acc -= 1.0 / TICRATE
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
            if want_fire and not state["cooldown"] and player_mo.health > 0:
                if kinematic or noclip:
                    player_mo.angle = cam.bam  # NOTE: legacy: camera leads
                cd, flash_now = weapons.fire(
                    ps, player_mo, phys, index, mobjs,
                    renderer.skyflatnum, accurate=not state["refire"],
                    ctx=ctx, queue=state["pending"])
                if cd >= 0:
                    state["cooldown"] = cd
                    body, flash = weapons.PSPRITES[ps.readyweapon]
                    state["atk_until"] = state.get("tics", 0) + cd
                    state["atk_span"] = max(1, cd)
                    if flash_now and flash is not None:
                        state["flash_until"] = (
                            state.get("tics", 0)
                            + weapons.FLASH_TICS[ps.readyweapon])
                # NOTE: cd < 0 means still switching or just auto-switched
                # off a dry gun (vanilla never clicks empty).
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
                    melt.start(old, render_scene())
                    wipe_after = "level"
                    gamestate = "wipe"
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
            # Think mobjs (P_RunThinkers on a live list: thinkers born
            # this tic think right away, like vanilla's head-to-tail
            # walk; removal holds the index so the next body slides in).
            index = phys.things
            _mi = 0
            while _mi < len(mobjs):
                mo = mobjs[_mi]
                if mo is player_mo and ps.playerstate == p_user.PST_LIVE:
                    # NOTE: the live body moves in the player block
                    # above (camera-driven); it never thinks here. The
                    # corpse does (death states, scream, slide, crush).
                    _mi += 1
                    continue
                crossed_mo = think_mobj(mo, phys, ctx)
                for line, side in crossed_mo:
                    world.cross_special_line(line, mo.is_player, mo,
                                             phys, mobjs, side)
                if mo.dead and not (mo.flags & combat._MF_CORPSE):
                    # NOTE: spent puffs/blood/missiles/fog and picked-up
                    # items leave (vanilla idles their S_NULL thinkers;
                    # dropping them is behavior-neutral and saves the
                    # think loop). Corpses stay for crush/respawn/render.
                    del mobjs[_mi]
                    if index is not None:
                        index.unlink(mo)
                    if mo.sector is not None:
                        try:
                            mo.sector.thinglist.remove(mo)
                        except ValueError:
                            pass
                else:
                    _mi += 1
            if world.exit_kind:
                cur = game_map.marker
                nxt = flow.next_map(cur, world.exit_kind == "secret")
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
                    melt.start(old, np.zeros((200, 320), dtype=np.uint8))
                    audio.music_play(FINALE_SONG, "exit-finale")
                    wipe_after = "finale"
                    gamestate = "wipe"
                else:
                    tk, ti, ts = world.totals
                    inter = interm.Intermission(
                        cur, nxt, ps_exit.killcount, tk,
                        ps_exit.itemcount, ti, ps_exit.secretcount, ts,
                        world.time, par)
                    next_map, next_keep, next_hp = nxt, ps_exit, hp_exit
                    first = np.zeros((200, 320), dtype=np.uint8)
                    inter.draw(first, game_menu)
                    melt.start(old, first)
                    audio.music_play(INTER_SONG, "exit-inter")
                    wipe_after = "inter"
                    gamestate = "wipe"
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
            # NOTE: leveltime closes the tic (vanilla P_Ticker): specials
            # think last, so doors/lights/crush see post-move bodies.
            state["tics"] = state.get("tics", 0) + 1
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
        if amap is not None:
            # NOTE: fullscreen automap (TAB): the game keeps running.
            amap.plr_x, amap.plr_y = player_mo.x, player_mo.y
            amap.plr_angle = cam.bam
            screen.fill((0, 0, 0))
            amap.draw(screen, mobjs
                       if (amap.cheating == 2
                           or state["ps"].powers.get(PW_ALLMAP))
                       else None)
            if font is not None:
                hint = font.render(
                    "AUTOMAP +-zoom F-follow G-grid TAB-close",
                    True, (180, 180, 180))
                screen.blit(hint, (8, WIN_H - 24))
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
            fb = render_scene()
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
            stepped = melt.tick()
            if stepped is None:
                gamestate = wipe_after
            else:
                fb = stepped
        last_fb = fb.copy()
        if recording or replaying:
            demo_sum = (demo_sum + int(fb.sum())) % 1000000007
        frame = pygame.image.frombuffer(
            palette_luts[palette_index(state["ps"])][fb].tobytes(),
            (SCREENWIDTH, SCREENHEIGHT), "RGB"
        )
        screen.blit(pygame.transform.scale(frame, (WIN_W, WIN_H)), (0, 0))
        if font is not None and gamestate in ("level", "menu", "wipe") \
                and has_level:
            hud = (f"{game_map.marker} x={cam.x:.0f} y={cam.y:.0f} "
                   f"a={math.degrees(cam.angle) % 360:.0f} "
                   f"{fps_ema:.0f}fps "
                   f"{'noclip' if noclip else 'clip'} "
                   f"AI:{'FROZEN' if ctx.ai_frozen else 'LIVE'} "
                   f"{skill.upper()}{'+FAST' if fast else ''} "
                   f"v{ver}")
            if debug:
                # NOTE: song-thread health for low-fps music reports.
                ms = audio.music_status()
                hud += (f" MUS:{ms.get('backend', '?')} "
                        f"q{ms.get('queue', '?')} "
                        f"p{ms.get('pumped', '?')}/"
                        f"s{ms.get('starved', '?')}")
                if not ms.get("alive", True) or ms.get("error"):
                    hud += f" DEAD:{ms.get('error')}"
            screen.blit(font.render(hud, True, (255, 255, 255)), (8, 8))
            if message is not None and msettings.messages:
                screen.blit(font.render(message, True, (255, 200, 100)),
                            (8, 28))
            if show_ai:
                # Nearest living monster: live AI state for bug reports.
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
                    screen.blit(font.render(ai_line, True, (100, 255, 100)),
                                (8, 64))
            help_line = (
                "WASD/arrows move+turn, mouse look, Shift run, E use, "
                "1-7 weapons, TAB map, M sound/mark, P pause, F1 help, "
                "F6/F9 quicksave, Esc menu"
            )
            if debug:
                help_line += " [N noclip F freeze X AI PgUp/PgDn G mouse]"
            screen.blit(font.render(help_line, True, (180, 180, 180)),
                        (8, WIN_H - 120))
        if font is not None and gamestate == "finale":
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
        pygame.display.flip()
        frames += 1
        if frames_opt is not None and frames >= frames_opt:
            print(f"smoke: {frames} frames, {fps_ema:.0f}fps ema")
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
