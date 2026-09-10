# Milestone: vanilla-compatible demos + bit-exact engine

Goal for the `bit-exact-emu` branch: play back real vanilla demo lumps
(IWAD `DEMO1`–`DEMO3`, speedrun `.lmp` files) without desyncing. A demo
is 35 Hz ticcmds driving a bit-exact sim — so this is really an engine
fidelity project with a file format on top. Work happens on the branch;
`main` stays untouched until small verified pieces merge back.

## A. ticcmd layer (input)

- [ ] Sample input once per tic at 35 Hz, decoupled from render rate
      (`tools/doom_view.py`: input currently polled per display frame,
      mouse turning applied instantly in the event handler).
- [ ] Quantize mouse look to int16 `angleturn` per tic.
- [ ] Map movement/keys to vanilla `ticcmd_t` units (forward/side
      speeds, `BT_ATTACK`/`BT_USE`/weapon-change bits).
- [ ] Keep the current intent-recorder as the fallback/debug path.

## B. Player physics rewrite

- [ ] `P_PlayerThink` / `P_MovePlayer`: momentum, `FRICTION` /
      `STOPSPEED`, `P_CalcHeight` (bob/viewheight), angled strafe,
      use-button traversal — fixed-point, vanilla speeds.
- [ ] Replace the camera-kinematic mover for the demo path (keep the
      current mover behind a flag for comparison while porting).
- [ ] Rebirth/death flow per vanilla (`PST_REBORN`, inventory reset).

## C. Determinism audit (sim must be bit-exact)

- [ ] Purge floats from sim paths (camera, `math.cos/sin` → fine
      tables); renderer/visual-only code is exempt.
- [ ] No hash-order iteration in sim paths (`PYTHONHASHSEED`
      sensitivity: sets/dicts keyed by id/strings).
- [ ] Audit `P_Random`/`M_Random` consumption order call-by-call
      against linuxdoom (`p_enemy.c`, `p_map.c`, `p_pspr.c`, …).
- [ ] Never touch Python `random` in sim code (already true; keep it
      that way — it broke demo determinism once, see commit history).

## D. Close sim-affecting divergences (see docs/DIVERGENCES.md)

- [ ] Floaters track target height (`MF_FLOAT` in `P_Move`/chase).
- [ ] Crusher gibs corpses and removes dropped items
      (`PIT_ChangeSector`), not just damage.
- [ ] In-stasis ceiling reactivation + crush-stop (145).
- [ ] Sector specials 14/15 and any other E2-relevant specials.
- [ ] Full monster line-trigger set (vanilla `P_CrossSpecialLine`).
- [ ] Nightmare/fast Parm behavior exactly per skill table
      (incl. `-respawn` semantics outside nightmare).
- [ ] Cosmetic-only gaps (finale typing, splat anims, automap marks)
      stay as-is unless they block validation.

## E. Demo format + playback machine

- [ ] Writer/reader: header (version 109, skill, episode, map,
      deathmatch/respawn/fast/nomonsters bytes), ticcmd stream,
      `DEMOMARKER` footer (`pydoom/demo.py` or new module).
- [ ] Playback state machine (`G_DoPlayDemo` idea): level transitions
      inside the demo, clean return to title, `-playdemo` /
      `-timedemo` / `-record` CLI flags.
- [ ] RNG streams reset/seeded exactly like `G_DoLoadLevel`.

## F. Validation (defines "compatible")

- [ ] IWAD `DEMO1`/`DEMO2`/`DEMO3` play to the end without desync.
- [ ] Known speedrun `.lmp` files finish the right maps with the
      right time/ending (compare against Chocolate Doom).
- [ ] Desync detector: checksum per tic during playback, diffable
      against a reference run.
- [ ] Recorded demos load in Chocolate Doom / DSDA-Doom (and vice
      versa for short clips).

## G. Audio chip question (audible, not sync-affecting)

- [ ] A/B our DOSBox-OPL backend vs Nuked-OPL3 on identical register
      streams; switch backend if the difference is audible (music
      never affects demo sync — framebuffer checksums only).
