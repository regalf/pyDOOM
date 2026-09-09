"""Interactive noclip walkthrough viewer (pygame-ce).

Usage: python tools/doom_view.py [MAP] [WAD]

Keys: W/A/S/D or arrows move/turn, mouse looks, Shift runs,
E uses doors/switches, N toggles noclip (collision is ON by default),
F freezes/thaws monster AI (frozen by nothing at first: they chase),
PgUp/PgDn switch map, G grabs/releases the mouse, Esc/Q quits.
Hidden test hook: --frames=N quits after N frames (headless smoke test).

Movement uses the real physics (P_TryMove/P_SlideMove): walls block,
angled walls slide, steps up to 24 units climb. No gravity, AI,
weapons or thing collision yet.
"""

import math
import os
import subprocess
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import numpy as np
import pygame

from pydoom import combat
from pydoom import flow
from pydoom import weapons
from pydoom.automap import Automap
from pydoom.ai import AIContext, check_sight
from pydoom.doors import World
from pydoom.info import MT_INDEX, MT_NAMES, STATE_INDEX
from pydoom.mapdata import Map
from pydoom.mobjs import ThingIndex, refresh_sector, spawn_map, spawn_mobj
from pydoom.mobjs import think_mobj
from pydoom.palette import load_playpal
from pydoom.physics import MF_NOCLIP, Mover, Physics
from pydoom.pickup import collect_touched
from pydoom.player import PlayerState
from pydoom.statusbar import draw_status_bar
from pydoom.renderer import SCREENHEIGHT, SCREENWIDTH, Renderer
from pydoom.textures import TextureManager
from pydoom.wad import WadFile

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
TURN_SPEED = 0.075  # radians per tic
MOUSE_SENS = 0.0028  # radians per pixel
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


def main() -> int:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    frames_opt = None
    for a in sys.argv[1:]:
        if a.startswith("--frames="):
            frames_opt = int(a.split("=", 1)[1])
    map_name = args[0].upper() if len(args) > 0 else "E1M1"
    default_wad = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")
    wad_path = args[1] if len(args) > 1 else default_wad

    wad = WadFile(wad_path)
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
    renderer = Renderer(wad, texman)
    maps = wad.list_maps()

    def load_map(marker: str, keep_ps=None, keep_hp: int | None = None):
        game_map = Map.from_wad(wad, marker)
        texman.resolve_map(game_map)
        start = next(t for t in game_map.things if t.type == 1)
        sub = renderer.sector_at(
            game_map, start.x << 16, start.y << 16
        )
        assert sub.sector is not None
        floor = sub.sector.floorheight / 65536.0
        cam = Camera(float(start.x), float(start.y), float(start.angle),
                     floor + VIEWHEIGHT_ABOVE_FLOOR)
        phys = Physics(game_map)
        mover = Mover(x=start.x << 16, y=start.y << 16,
                      z=sub.sector.floorheight)
        mover.floorz = sub.sector.floorheight
        mover.ceilingz = sub.sector.ceilingheight
        world = World(game_map, texman)
        # Live mobjs (statues until AI lands); physics sees them.
        index = ThingIndex(game_map)
        phys.things = index
        phys.damage_hook = lambda tm, th: combat.things_hit(tm, th, ctx)
        mobjs = spawn_map(game_map, phys, index)
        # Player body for monster AI and walls alike: the camera drives
        # this mobj directly (no separate physics body, so there is no
        # self-collision). Culled from its own view like vanilla.
        player_mo = spawn_mobj(game_map, phys, index,
                               mover.x, mover.y, mover.z,
                               MT_INDEX["PLAYER"])
        player_mo.is_player = True
        if keep_hp is not None:
            player_mo.health = keep_hp
        mobjs.append(player_mo)
        ctx = AIContext(
            physics=phys, world=world, players=[player_mo],
            sector_index={id(s): i for i, s in enumerate(game_map.sectors)},
        )
        ctx.mobjs = mobjs
        ctx.skyflatnum = renderer.skyflatnum
        # NOTE: level transitions carry guns/ammo/armor (keys/powers
        # stripped); player health rides on the fresh body below.
        ps = keep_ps if keep_ps is not None else PlayerState()
        if keep_ps is not None:
            flow.strip_for_next_level(ps)
        ctx.player_state = ps
        state = {"cooldown": 0, "refire": False, "firing": False,
                 "start": start, "ps": ps, "won": False,
                 "flash_until": 0, "bob": 0}
        return game_map, cam, phys, player_mo, world, mobjs, ctx, state

    game_map, cam, phys, player_mo, world, mobjs, ctx, state = load_map(
        map_name)
    combat.register_combat_actions()
    noclip = False
    show_ai = False  # X toggles a nearest-monster AI readout
    amap = None  # TAB automap overlay (vanilla keeps the game running)
    am_zoom_in = am_zoom_out = False
    state_names = {v: k for k, v in STATE_INDEX.items()}
    message: str | None = None
    message_tics = 0
    map_idx = maps.index(game_map.marker) if game_map.marker in maps else 0

    pygame.init()
    screen = pygame.display.set_mode((WIN_W, WIN_H))
    pygame.display.set_caption(f"pydoom - {game_map.marker}")
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
    while running:
        dt = min(clock.tick(60) / 1000.0, 0.25)
        fps_ema += (1.0 / max(dt, 1e-6) - fps_ema) * 0.05
        for ev in pygame.event.get():
            if ev.type == pygame.QUIT:
                running = False
            elif ev.type == pygame.KEYDOWN:
                if ev.key in (pygame.K_ESCAPE, pygame.K_q):
                    running = False
                elif ev.key == pygame.K_g:
                    if amap is not None:
                        amap.toggle_grid()  # vanilla TAB-mode G
                    else:
                        pygame.event.set_grab(not pygame.event.get_grab())
                        pygame.mouse.set_visible(
                            not pygame.mouse.get_visible())
                elif ev.key == pygame.K_n:
                    noclip = not noclip
                    if noclip:
                        player_mo.flags |= MF_NOCLIP
                    else:
                        player_mo.flags &= ~MF_NOCLIP
                        player_mo.x, player_mo.y = (
                            int(cam.x * 65536), int(cam.y * 65536))
                        res = phys.check_position(
                            player_mo, player_mo.x, player_mo.y)
                        if res.ok:
                            player_mo.floorz, player_mo.ceilingz = (
                                res.floorz, res.ceilingz)
                            player_mo.z = res.floorz
                        refresh_sector(player_mo, phys)
                elif ev.key == pygame.K_e:
                    message = world.use_lines(
                        player_mo.x, player_mo.y, cam.bam, phys,
                        state["ps"].keys, player_mo, mobjs)
                    message_tics = 3 * TICRATE if message else 0
                    if world.teleport_angle is not None:
                        cam.angle = (world.teleport_angle
                                     * 2 * math.pi / 0x100000000)
                        cam.x = player_mo.x / 65536.0
                        cam.y = player_mo.y / 65536.0
                        world.teleport_angle = None
                elif ev.key == pygame.K_PAGEUP:
                    map_idx = (map_idx - 1) % len(maps)
                    (game_map, cam, phys, player_mo, world, mobjs, ctx,
                     state) = load_map(maps[map_idx])
                    amap = None  # new map, new automap
                    message, message_tics = None, 0
                    pygame.display.set_caption(
                        f"pydoom - {game_map.marker}")
                elif ev.key == pygame.K_PAGEDOWN:
                    map_idx = (map_idx + 1) % len(maps)
                    (game_map, cam, phys, player_mo, world, mobjs, ctx,
                     state) = load_map(maps[map_idx])
                    amap = None  # new map, new automap
                    message, message_tics = None, 0
                    pygame.display.set_caption(
                        f"pydoom - {game_map.marker}")
                elif ev.key == pygame.K_f:
                    if amap is not None:
                        amap.toggle_follow()  # vanilla TAB-mode F
                    else:
                        ctx.ai_frozen = not ctx.ai_frozen
                elif ev.key == pygame.K_TAB:
                    if amap is None:
                        amap = Automap(game_map, WIN_W, WIN_H,
                                       palette_lut.tolist())
                        am_zoom_in = am_zoom_out = False
                    else:
                        amap = None
                elif ev.key in (pygame.K_EQUALS, pygame.K_PLUS,
                                pygame.K_KP_PLUS):
                    am_zoom_in = True
                elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    am_zoom_out = True
            elif ev.type == pygame.KEYUP:
                if ev.key in (pygame.K_EQUALS, pygame.K_PLUS,
                              pygame.K_KP_PLUS):
                    am_zoom_in = False
                elif ev.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    am_zoom_out = False
                elif ev.key == pygame.K_x:
                    show_ai = not show_ai
                elif pygame.K_1 <= ev.key <= pygame.K_7:
                    weapons.request_weapon(state["ps"], chr(ev.key))
            elif ev.type == pygame.MOUSEMOTION:
                if pygame.event.get_grab():
                    cam.turn(-ev.rel[0] * MOUSE_SENS)
            elif ev.type == pygame.MOUSEBUTTONDOWN:
                if ev.button == 1:
                    state["firing"] = True
            elif ev.type == pygame.MOUSEBUTTONUP:
                if ev.button == 1:
                    state["firing"] = False

        keys = pygame.key.get_pressed()
        run = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
        speed = RUN_SPEED if run else WALK_SPEED
        tic_acc += dt
        while tic_acc >= 1.0 / TICRATE and not state["won"]:
            tic_acc -= 1.0 / TICRATE
            state["tics"] = state.get("tics", 0) + 1
            if message_tics:
                message_tics -= 1
                if not message_tics:
                    message = None
            # Door thinkers, buttons, crush checks (blocker = player).
            sub_now = phys.subsector_at(player_mo.x, player_mo.y)
            world.blocker = (
                (sub_now.sector, player_mo.z, player_mo.height)
                if sub_now.sector is not None else None
            )
            world.tick()
            # Think mobjs (the player body is driven by the camera).
            index = phys.things
            for mo in list(mobjs):
                if mo is player_mo:
                    continue
                crossed_mo = think_mobj(mo, phys, ctx)
                for line in crossed_mo:
                    world.cross_special_line(line, False, mo, phys,
                                             mobjs)  # silent
                if mo.dead:
                    mobjs.remove(mo)
                    if index is not None:
                        index.unlink(mo)
                    if mo.sector is not None:
                        try:
                            mo.sector.thinglist.remove(mo)
                        except ValueError:
                            pass
            # Weapon raise ticks, then fire/switch (mouse-left or Space).
            ps = state["ps"]
            weapons.tick_weapon(ps)
            if state["cooldown"]:
                state["cooldown"] -= 1
            want_fire = state["firing"] or keys[pygame.K_SPACE]
            if want_fire and not state["cooldown"]:
                player_mo.angle = cam.bam
                cd = weapons.fire(ps, player_mo, phys, index, mobjs,
                                  renderer.skyflatnum,
                                  accurate=not state["refire"], ctx=ctx)
                if cd >= 0:
                    state["cooldown"] = cd
                    body, flash = weapons.PSPRITES[ps.readyweapon]
                    if flash is not None:
                        state["flash_until"] = (
                            state.get("tics", 0) + weapons.FLASH_TICS)
                # NOTE: cd < 0 means still switching or just auto-switched
                # off a dry gun (vanilla never clicks empty).
            state["refire"] = want_fire
            if player_mo.health <= 0:
                # Respawn at the map start, monsters lose interest.
                message, message_tics = "YOU DIED", 3 * TICRATE
                start = state["start"]
                if index is not None:
                    index.unlink(player_mo)
                if player_mo.sector is not None:
                    try:
                        player_mo.sector.thinglist.remove(player_mo)
                    except ValueError:
                        pass
                if player_mo in mobjs:
                    mobjs.remove(player_mo)
                player_mo = spawn_mobj(
                    game_map, phys, index, start.x << 16, start.y << 16,
                    -1, MT_INDEX["PLAYER"])
                player_mo.is_player = True
                mobjs.append(player_mo)
                ctx.players = [player_mo]
                for mo in mobjs:
                    mo.target = None
                    mo.threshold = 0
                cam.x, cam.y = float(start.x), float(start.y)
                cam.viewz = player_mo.z / 65536.0 + VIEWHEIGHT_ABOVE_FLOOR
                # NOTE: vanilla rebirth resets the inventory to pistol+50.
                state["ps"] = PlayerState()
                ctx.player_state = state["ps"]
            fwd = strafe = 0.0
            if keys[pygame.K_w] or keys[pygame.K_UP]:
                fwd += speed
            if keys[pygame.K_s] or keys[pygame.K_DOWN]:
                fwd -= speed
            if keys[pygame.K_a]:
                strafe -= speed
            if keys[pygame.K_d]:
                strafe += speed
            if keys[pygame.K_LEFT]:
                cam.turn(TURN_SPEED)
            if keys[pygame.K_RIGHT]:
                cam.turn(-TURN_SPEED)
            if fwd or strafe:
                # NOTE: weapon bob follows footsteps (P_MovePsprites):
                # amplitude chases speed, phase always advances.
                state["bob"] += 2 if run else 1
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
                    for line in crossed:
                        msg = world.cross_special_line(line, True, player_mo,
                                                       phys, mobjs)
                        if msg is not None:
                            message, message_tics = msg, 3 * TICRATE
            else:
                # NOTE: standing still settles the weapon (P_CalcHeight).
                state["bobamp"] = max(state.get("bobamp", 0) - 4, 0)
            if not noclip:
                # Gravity lite + floor glue (no full gamesim falling):
                # ride lifts up, fall fast, snap when close.
                res = phys.check_position(
                    player_mo, player_mo.x, player_mo.y)
                if res.ok:
                    player_mo.floorz, player_mo.ceilingz = (
                        res.floorz, res.ceilingz)
                    if player_mo.z > player_mo.floorz:
                        player_mo.z = max(player_mo.floorz,
                                          player_mo.z - 8 * 65536)
                    else:
                        player_mo.z = player_mo.floorz
            # Walk-over pickups (P_TouchSpecialThing sweep) + power ticks.
            ps = state["ps"]
            ps.tick(player_mo)
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
            # Damaging floors, secrets and the E1M8 burn-out exit.
            sec_msg = world.player_in_special_sector(player_mo, ps, ctx)
            if sec_msg is not None:
                message, message_tics = sec_msg, 3 * TICRATE
            if world.exit_kind:
                cur = game_map.marker
                nxt = flow.next_map(cur, world.exit_kind == "secret")
                if nxt is None:
                    state["won"] = True  # E1M8 exit: episode complete
                else:
                    keep, hp = state["ps"], player_mo.health
                    (game_map, cam, phys, player_mo, world, mobjs,
                     ctx, state) = load_map(nxt, keep, hp)
                    amap = None  # new map, new automap
                    map_idx = maps.index(game_map.marker)
                    message, message_tics = None, 0
                    pygame.display.set_caption(f"pydoom - {nxt}")
            # Ease viewz toward standing height on the current floor.
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

        if amap is not None:
            # NOTE: fullscreen automap (TAB): the game keeps running.
            amap.plr_x, amap.plr_y = player_mo.x, player_mo.y
            amap.plr_angle = cam.bam
            if am_zoom_in:
                amap.zoom_hold(True)
            elif am_zoom_out:
                amap.zoom_hold(False)
            else:
                amap.zoom_release()
            amap.ticker()
            screen.fill((0, 0, 0))
            amap.draw(screen)
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
        fb = renderer.render_view(
            game_map, int(cam.x * 65536), int(cam.y * 65536), cam.bam,
            int(cam.viewz * 65536), mobjs,
        )
        # NOTE: P_DrawPlayerSprites lite: ready gun + muzzle flash, bob,
        # lower/raise travel while switching, kick frame while firing.
        ps = state["ps"]
        body, flash = weapons.PSPRITES[ps.readyweapon]
        bob, amp = state["bob"], state.get("bobamp", 0)
        bobx = int(amp * math.cos(bob * 0.35))
        boby = int(amp * math.sin(bob * 0.35))
        firing = state.get("tics", 0) < state["flash_until"]
        if ps.pendingweapon != ps.readyweapon:
            # NOTE: A_Lower/A_Raise dip: old gun sinks, new gun rises.
            travel = 1 - ps.switchtics / weapons.SWITCH_TICS
            if travel < 0.5:
                yoff = int(96 * travel * 2)
            else:
                body, flash = weapons.PSPRITES[ps.pendingweapon]
                yoff = int(96 * (1 - (travel - 0.5) * 2))
            firing = False
        else:
            yoff = 0
        if firing:
            # NOTE: attack frames kick the body while the flash shows.
            if not renderer.draw_psprite(fb, body, bobx, boby + yoff, "B"):
                renderer.draw_psprite(fb, body, bobx, boby + yoff, "A")
        else:
            renderer.draw_psprite(fb, body, bobx, boby + yoff, "A")
        if firing and flash is not None:
            renderer.draw_psprite(fb, flash, bobx, boby + yoff)
        # NOTE: classic bottom strip (covers the gun base, like vanilla).
        draw_status_bar(renderer, fb, ps, player_mo.health)
        frame = pygame.image.frombuffer(
            palette_lut[fb].tobytes(), (SCREENWIDTH, SCREENHEIGHT), "RGB"
        )
        screen.blit(pygame.transform.scale(frame, (WIN_W, WIN_H)), (0, 0))
        if font is not None:
            hud = (f"{game_map.marker} x={cam.x:.0f} y={cam.y:.0f} "
                   f"a={math.degrees(cam.angle) % 360:.0f} "
                   f"{fps_ema:.0f}fps "
                   f"{'noclip' if noclip else 'clip'} "
                   f"AI:{'FROZEN' if ctx.ai_frozen else 'LIVE'} "
                   f"v{ver}")
            screen.blit(font.render(hud, True, (255, 255, 255)), (8, 8))
            if message is not None:
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
            screen.blit(font.render(
                "WASD/arrows move+turn, mouse look, Shift run, E use, "
                "1-7 weapons, TAB map, N noclip, F freeze AI, X AI info, "
                "PgUp/PgDn map, G mouse, Esc quit",
                True, (180, 180, 180)), (8, WIN_H - 24))
            if state["won"]:
                big = font.render("EPISODE 1 COMPLETE", True, (255, 255, 0))
                screen.blit(big, (WIN_W // 2 - big.get_width() // 2,
                                   WIN_H // 2 - 130))
                # NOTE: E1TEXT (d_englsh.h), the episode payoff.
                for i, text_line in enumerate(_E1TEXT_LINES):
                    small = font.render(text_line, True, (200, 200, 200))
                    screen.blit(small, (WIN_W // 2 - small.get_width() // 2,
                                        WIN_H // 2 - 90 + i * 20))
                sub = font.render("PgUp/PgDn: replay maps   Esc: quit",
                                  True, (255, 255, 255))
                screen.blit(sub, (WIN_W // 2 - sub.get_width() // 2,
                                   WIN_H // 2 + 130))
        pygame.display.flip()
        frames += 1
        if frames_opt is not None and frames >= frames_opt:
            print(f"smoke: {frames} frames, {fps_ema:.0f}fps ema")
            running = False

    pygame.quit()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
