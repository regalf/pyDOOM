# Simplifications & divergences

pyDOOM aims at a faithful recreation of linuxdoom-1.10, but some
corners are deliberately simplified where the original costs more
than it gives. This file lists every known one, by area. Rules:

- Everything **reachable in E1M1–E3M9 is implemented** — machine
  checked on E1: every linedef special (1,2,5,7,8,9,11,16,18,20,22,
  23,26–28,31–34,35,36,46,48,51,62,63,70,76,82,86,88,90,91,97,98,
  103) and every sector special (0,1,2,3,7,8,9,11,12,13,16)
  occurring in the episode has a handler (E2/E3 ride the same
  families; their boss exits are tested on the real maps).
  Walk/switch roles follow `p_spec.c`/`p_switch.c` exactly: 22 is
  W1-only (E1M5/E1M7 bridge, USE does nothing), plus W1
  30/37/56/59/104, WR 89/95 and SR-42 close out of the E2/E3 set.
- Each item below is also marked with a `NOTE:` comment at the exact
  code location.

## Scope: registered Doom 1 (E1–E3)

- `doom.wad` (registered) boots by default with `DOOM1.WAD` shareware
  fallback; mission detect (`pydoom/mission.py`, dsda `CheckIWAD`
  lite) drives `GAMEMODE`, episode gating, warps and demo clamps.
  `doom.wad` itself is gitignored (commercial IWAD, copyright):
  registered-only tests skip without it.
- E1–E3 load and play end to end: per-episode skies, music, pars,
  flow (secret M9s, M9 returns, M8 victories), boss exits (E2M8
  cyber / E3M8 spider), `A_KeenDie` (E4M2-ready), per-episode
  intermission maps. Ultimate (E4) lands with its IWAD.
- No multiplayer: `MF_NOTDMATCH` things always spawn, no frags,
  no deathmatch exits, no `-respawn` outside nightmare.

## Player body and movement

- The camera drives a player mobj directly (no separate physics
  body, hence no self-collision); `MF_NOCLIP` re-syncs floor/sector
  when clipping back in.
- No key rebinding, no mouse-button or joystick setup.
- Input rides vanilla `ticcmd_t` packets at 35 Hz (`pydoom/ticcmd.py`:
  forward/side units, int16 angleturn with the key-turn ramp,
  `BT_ATTACK`/`BT_USE`/`BT_CHANGE` bits), applied by the vanilla
  momentum mover (`pydoom/p_user.py`: thrust, `FRICTION`/`STOPSPEED`,
  `P_CalcHeight` bob/viewheight). `--kinematic` keeps the old
  camera-direct mover for comparison; key/mouse bindings stay fixed.
- Death reloads the level from scratch in single player
  (`G_DoReborn` → `ga_loadlevel`, no tally): fresh map and pistol+50
  with tallies kept and the RNG stream untouched, exactly like
  vanilla (multiplayer respawns in place instead, out of scope).
- Gravity lite plus floor glue instead of full `P_ZMovement`: lifts
  carry the body via re-glue, falls snap when close. No falling
  damage (vanilla has none either), no jumping/crouching (same).

## Renderer (320×200 software, numba)

- Faithful core: BSP walk, visplanes with spans, masked mid-textures,
  sprites with fuzz (spectres), SKY1, muzzle-flash `extra_light`,
  psprites anchored like `R_DrawPSprite` (unmirrored, so kick frames
  align), PLAYPAL damage/bonus/suit flashes.
- Fixed 320×200 (upscaled ×3): no low-detail mode, no gamma (F11),
  no screensize (+/−). The status bar is baked into the framebuffer.
- The light-amplification visor renders everything at maximum light
  (`fixedcolormap`); the computer map shows live monster dots.

## Monsters and AI

- Full Doom 1 roster with sight (`REJECT` + validator), sound
  propagation, chase/attack/pain/death, infighting, per-episode
  boss deaths (E1M8 floors, E2M8/E3M8 exits), nightmare respawn
  (~12 s, teleport fog, `reactiontime 18`). Slammed skullfly
  charges stop, drop to spawn and chase again, like vanilla.
- Floaters (cacolings) keep their spawn height: `MF_FLOAT` height
  adjustment toward the target (vanilla `P_Move`) is skipped, and
  there is no gravity for `MF_NOGRAVITY` bodies.
- Monster-only `A_*` actions that are sound/visual-only in vanilla
  are registered no-ops; unknown actions can never crash the thinker.
- Corpses ride lifts via explicit sector re-glue (stationary bodies
  never move, so vanilla's per-move refresh can't reach them).

## Combat and weapons

- Real state durations, cooldowns, flash/light levels, pellets,
  auto-aim slope, BFG spray, berserk ×10, gibs via `XDEATHSTATE`,
  clip/shotgun drops at half ammo, armor clearing even on fumes,
  telefrag (≥1000) going through godmode and invulnerability.
- Chaingun barks through the pistol lump — that is vanilla (there is
  no separate chaingun sound). Plasma/BFG fire fine; under the
  shareware IWAD they stay silent and sprite-less (no such lumps;
  the registered WAD has them and they play). Weapon pickups chime
  `wpnup`, and the Doom-2-only megasphere is refused on pickup.

## Doors, platforms, ceilings, crushers

- Full families (manual, blazing, lifts, stairs 8/16, donuts, plats
  incl. perpetual, fast/silent crush-and-raise, lowerers, raisers).
- Vanilla quirks preserved: `lowerAndCrush` (type 44) grinds without
  hurting (`crush` stays false in the original), grinding crushers
  slow to `CEILSPEED/8` (fast ones never do).
- Not wired: in-stasis reactivation, gibbing corpses and removing
  dropped items under a crusher (damage only), switches 41/43/49
  (stub message). Monster line triggers are the vanilla subset
  (39, 97, 125, 126, 4, 10, 88).

## Pickups and player

- Caps, backpack doubling (once), armor classes, key bitmask with
  skulls, power durations, medikit wording by need, dry-pickup
  auto-arm — all vanilla. Kill/item/secret tallies feed the
  intermission and reset per level.

## Status bar, face, automap

- Vanilla widgets, arms 2–7 lamps, ammo readout (fists count
  bullets), animated face cascade with rampage glare and death.
- Automap is a TAB overlay while the sim runs (follow/grid/zoom,
  full iddt cheat cycle, `M` drops marks in map mode). The
  `pw_allmap` branch is skipped (reveal defaults to full anyway).

## Flow, exits, teleports

- E1–E3 routing with secret exits, `PST_REBORN` fresh-start warps,
  `strip_for_next_level` carry, E1M8 burn-out exit sector, direct
  E2M8/E3M8 boss exits.
- Walk-over and USE teleports (39/97/125/126) hop with the crossing
  side (back side shut, W1 clears, monsters ride, 125/126 skip
  players). Blocked landings refuse with no fog at all
  (`P_TeleportMove`), telefrag first, fog and facing from the pad.

## Intermission and finale

- Two vanilla screens over the per-episode world map (`WIMAP{epsd}`,
  per-episode spots and flickers): staged tally (kills/items/secret
  climb +2/tic with pistol ticks and explosion thumps, 1 s pauses,
  time/par +3, any key hurries) then the entering map (taken-map
  splats, blinking YOU ARE HERE arrow, 4 s hold). Kill-less maps
  read 100% instead of faulting like vanilla would, and any key
  (not just attack/use) hurries. E2 level flickers run always
  instead of gating on progress (cosmetic).
- M8 exits melt to black with the episode payoff as an overlay
  (`E1TEXT` today; E2/E3 texts, the E3 bunny scroll and the cast
  call land with the finale phase); any key returns to the title
  (vanilla types the text over a flat, same words).

## Menu

- Main → Episode (mission-gated: shareware scolds past E1,
  registered opens E1–E3, `M_EPI4` arrives with retail) → Skill,
  Options (messages, mouse sens, SFX/music volume), ReadThis!,
  Load/Save, Quit with the shareware death jingle.
- Omitted until needed: graphic detail, screen size. (End Game
  is wired since the title state exists.)
- Thermo bars sit right of the label instead of below (layout
  simplification); dialog text is uppercase-only (the shareware
  `STCFN` font has no lowercase glyphs).

## Save/load

- Versioned pickle snapshots in `savegames/` (slot names included),
  not the vanilla binary format. Restoring rebuilds everything
  transient (blockmap index, thinkers clock, renderer) and remaps
  sector links onto a freshly loaded map, melting in like vanilla.

## Demos

- Vanilla demo compatibility (.lmp playback/record, version 109
  headers with per-mission episode clamp) plus the title attract
  loop with DEMO1-3. Experimental and off by default (`demos 1`
  in pydoom.cfg): streams consume fully and deterministically,
  scripted runs exit within a tic of vanilla with identical stats,
  but long IWAD demos still drift in monster-combat phase and need
  more development.

## Cheats

- Full classic set (`iddqd`/`idkfa`/`idfa`/`idclip`+alias/`idclev`/
  `idmus`/`iddt`/`idbehold`/`idmypos`/`idchoppers`) with vanilla
  messages, always on like the original.
- Two deliberate extras: `iddt` opens the automap if closed, and
  quit moved into the menu so `Q` stays free for `iddqd`. Dev keys
  (`N` noclip, `F` freeze, `X` readout, map hop) hide behind
  `--debug`.

## Audio: SFX

- 44100 Hz 16-bit stereo mixer (chocolate recipe), 8 channels with
  `sounds.c` priorities, same-origin restart, vanilla distance
  attenuation and real stereo pan (`STEREO_SWING`), silent no-op
  without a mixer. DS lumps upsample ×4 and duplicate to stereo
  on load.
- Chainsaw idle approximates the `S_SAW` 4+4 cadence at 8 tics
  with tails ringing out first; menu/error sounds are vanilla
  (`pistol`/`pstop`/`swtchn`/`stnmov`/`oof`).

## Audio: music

- MUS scores plus GENMIDI instruments drive 9 OPL2 voices with
  Chocolate-DMX logic (verified register-for-register), rendered
  event-exactly (short notes can never collapse into a chunk end)
  at 22050 Hz like chocolate's `opl.c`, through pip `PyOPL`
  (DOSBox synth), streamed stereo (mono duplicated: OPL2 has no
  pan) from a worker thread with ~4 s of buffered chunks.
- A one-pole DC blocker stands in for the Sound Blaster's
  AC-coupling capacitor; mute keeps the song running silently;
  without PyOPL the game stays silent (like LinuxDoom without
  its MUS server). Sampled (FluidSynth-style) backends are an
  explicit non-goal for now.

## Wipes, title, input

- Vanilla-faithful melt (random-walk curtain front on the menu
  stream, 1px lag then accelerating to 8px/tic, old frame sliding
  down, ~1.2 s in real-time tics); idle frames re-show the last
  step so 60fps never flashes. Menu open/close cuts instantly,
  like the original. Idle title falls into the IWAD demo loop.
- Fixed bindings, fixed mouse look curve on a slider; the messages
  toggle hides all HUD text including cheat confirmations.
- `P` (and Pause/Break, like vanilla) freezes the sim with the
  `M_PAUSE` patch while music plays on; `F1` opens Read This!;
  `F6`/`F9` quicksave to the last manual slot (or open the slots);
  options persist in `pydoom.cfg` (volumes, sens, messages).

## Randomness

- The 256-byte table verbatim from `m_random.c`, independent
  `P_Random`/`M_Random` streams, snapshot/restore for savegames.
  Plat random starts use the game stream (never `random`, which
  would desync demos across processes).

## OpenGL renderer parity (milestone H, phase 5 gate)

- Method: 320×200 GL readback vs PLAYPAL-applied software framebuffer,
  same fixed camera, same static mobjs (no sim): exact-match fraction
  + mean abs RGB diff. Gate: `tests/test_gl.py::test_parity_gate_e1m1_walk`
  (E1M1 start + 5-view scripted walk, GL 3.3+ context or skip).
- Measured (Mesa-class GL, thresholds in brackets):

| view (x, y, yaw) | exact [min] | mean [max] |
|---|---|---|
| start (1056, -3616, 90°) | 0.668 [0.60] | 5.20 [7.5] |
| pool (1328, -3291, 336°) | 0.630 [0.55] | 7.92 [10.5] |
| outdoor-sky (2000, -3291, 330°) | 0.563 [0.49] | 8.57 [11.0] |
| north-room (1169, -2274, 247°) | 0.278 [0.20] | 22.55 [26.0] |
| corridor (1056, -3000, 270°) | 0.429 [0.35] | 14.05 [17.0] |
| garden (1500, -3291, 336°) | 0.555 [0.48] | 9.20 [12.0] |

- Known classes (tolerance, not bugs): fuzz shimmer pattern
  approximated by frame+row (colors match); sky cylinder vs column
  drawer (horizon-locked, sub-texel edges); distance-light ramp
  rounding at column granularity; dark-quantization sensitivity
  (exact-match is brutal below colormap ~10: one row off flips
  every pixel); masked-post vs quad edges on fences.
- Accepted: no void concept — from outside the map (noclip/freecam)
  GL shows the true map where vanilla shows hall-of-mirrors; the
  player can never get there under collision.
- Open spots: north-room fence + dark interiors (same geometry,
  lighting/tier gaps under diagnosis on `opengl-renderer`).
