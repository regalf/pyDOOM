# Simplifications & divergences

pyDOOM aims at a faithful recreation of linuxdoom-1.10, but some
corners are deliberately simplified where the original costs more
than it gives. This file lists every known one, by area. Rules:

- Everything **reachable in E1M1–E1M9 is implemented** — machine
  checked: every linedef special (1,2,5,7,8,9,11,16,18,20,22,23,
  26–28,31–34,35,36,46,48,51,62,63,70,76,82,86,88,90,91,97,98,103)
  and every sector special (0,1,2,3,7,8,9,11,12,13,16) occurring in
  the episode has a handler.
- Each item below is also marked with a `NOTE:` comment at the exact
  code location.

## Scope: shareware episode 1

- Only `DOOM1.WAD` maps (E1M1–E1M9) load; `GAMEMODE` is `"shareware"`,
  so plasma/BFG/SSG never spawn and are gated out of the CheckAmmo
  fallback, and the Doom-2-only megasphere is refused on pickup.
- No episodes 2–3 content: no E2/E3 monsters, skies, or MAPxy routing
  (`flow.py` knows E1 only, plus the E1M3→E1M9 secret exit).
- No multiplayer: `MF_NOTDMATCH` things always spawn, no frags,
  no deathmatch exits, no `-respawn` outside nightmare.

## Player body and movement

- The camera drives a player mobj directly (no separate physics
  body, hence no self-collision); `MF_NOCLIP` re-syncs floor/sector
  when clipping back in.
- No momentum: movement sets position per tic (effectively permanent
  `CF_NOMOMENTUM`), no acceleration/friction curve, no key rebinding,
  no mouse-button or joystick setup.
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

- Full E1 roster with sight (`REJECT` + validator), sound propagation,
  chase/attack/pain/death, infighting, boss death with tag 666,
  nightmare respawn (~12 s, teleport fog, `reactiontime 18`).
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
  no separate chaingun sound). Plasma/BFG fire fine but stay silent
  and sprite-less: the shareware WAD has no such lumps and the
  renderer/audio skip missing ones quietly.

## Doors, platforms, ceilings, crushers

- Full families (manual, blazing, lifts, stairs 8/16, donuts, plats
  incl. perpetual, fast/silent crush-and-raise, lowerers, raisers).
- Vanilla quirks preserved: `lowerAndCrush` (type 44) grinds without
  hurting (`crush` stays false in the original), grinding crushers
  slow to `CEILSPEED/8` (fast ones never do).
- Not wired: in-stasis reactivation, crush-stop (145), gibbing
  corpses and removing dropped items under a crusher (damage only),
  switches 41/43/49 (stub message). Monster line triggers are the
  vanilla subset (39, 97, 125, 126, 4, 10, 88).

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

- E1 routing with secret exit, `PST_REBORN` fresh-start warps,
  `strip_for_next_level` carry, E1M8 burn-out exit sector.
- Teleports refuse a blocked landing with no fog at all
  (`P_TeleportMove`), telefrag first, back side shut, fog and
  facing from the destination pad.

## Intermission and finale

- Two vanilla screens over the `WIMAP0` world map: staged tally
  (kills/items/secret climb +2/tic with pistol ticks and explosion
  thumps, 1 s pauses, time/par +3, any key hurries) then the
  entering map (taken-map splats, blinking YOU ARE HERE arrow,
  4 s hold). Kill-less maps read 100% instead of faulting like
  vanilla would, and any key (not just attack/use) hurries.
- E1M8 melts to black with the `E1TEXT` payoff as an overlay;
  any key returns to the title (vanilla types the text over a
  flat, same words).

## Menu

- Main → Episode (shareware scolds on 2/3, vanilla-true) → Skill,
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
  sector links onto a freshly loaded map.

## Demos

- Own input format (events plus movement intent, ticcmd spirit) at
  fixed steps with framebuffer checksums — deterministic across
  runs, verified record-vs-replay equal. No vanilla demo
  compatibility (that needs ticcmd physics) and no title attract
  loop yet.

## Cheats

- Full classic set (`iddqd`/`idkfa`/`idfa`/`idclip`+alias/`idclev`/
  `idmus`/`iddt`/`idbehold`/`idmypos`/`idchoppers`) with vanilla
  messages, always on like the original.
- Two deliberate extras: `iddt` opens the automap if closed, and
  quit moved into the menu so `Q` stays free for `iddqd`. Dev keys
  (`N` noclip, `F` freeze, `X` readout, map hop) hide behind
  `--debug`.

## Audio: SFX

- 11025 Hz 8-bit mono mixer (vanilla spec), 8 channels with
  `sounds.c` priorities, same-origin restart, vanilla distance
  attenuation and stereo pan, silent no-op without a mixer.
- Chainsaw idle approximates the `S_SAW` 4+4 cadence at 8 tics
  with tails ringing out first; menu/error sounds are vanilla
  (`pistol`/`pstop`/`swtchn`/`stnmov`/`oof`).

## Audio: music

- MUS scores plus GENMIDI instruments drive 9 OPL2 voices with
  Chocolate-DMX logic (verified register-for-register), rendered
  event-exactly (short notes can never collapse into a chunk end)
  through pip `PyOPL` (DOSBox synth) at mixer rate, streamed from
  a worker thread with ~1 s of buffered chunks.
- A one-pole DC blocker stands in for the Sound Blaster's
  AC-coupling capacitor; mute keeps the song running silently;
  without PyOPL the game stays silent (like LinuxDoom without
  its MUS server). OPL3 stereo, 44100 Hz rendering, and sampled
  (FluidSynth-style) backends are explicit non-goals for now.

## Wipes, title, input

- Melt only (the vanilla default); menu open/close cuts instantly,
  like the original. Title is a static `TITLEPIC` (no demo loop
  behind it yet); any key opens the menu.
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
