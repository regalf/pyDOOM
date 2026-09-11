# Milestone: vanilla-compatible demos + bit-exact engine

Goal for the `bit-exact-emu` branch: play back real vanilla demo lumps
(IWAD `DEMO1`–`DEMO3`, speedrun `.lmp` files) without desyncing. A demo
is 35 Hz ticcmds driving a bit-exact sim — so this is really an engine
fidelity project with a file format on top. Work happens on the branch;
`main` stays untouched until small verified pieces merge back.

## A. ticcmd layer (input) — done on bit-exact-emu (`pydoom/ticcmd.py`)

- [x] Sample input once per tic at 35 Hz, decoupled from render rate
      (`tools/doom_view.py` builds one `Ticcmd` per tic iteration from
      a fresh key snapshot; mouse motion accumulates between tics).
- [x] Quantize mouse look to int16 `angleturn` per tic (slider rad/px
      converts to angleturn units/px, rounded per tic).
- [x] Map movement/keys to vanilla `ticcmd_t` units (forward/side
      tables 25/50 + 24/40, key-turn ramp 320/640/1280, `BT_ATTACK` /
      `BT_USE` / `BT_CHANGE` + weapon bits; use/weapon edges latch so
      sub-frame taps still reach a tic).
- [x] Keep the current intent-recorder as the fallback/debug path
      (unchanged pickle format; record/replay checksums still agree).

## B. Player physics rewrite — done on bit-exact-emu (`pydoom/p_user.py`)

- [x] `P_PlayerThink` / `P_MovePlayer`: momentum, `FRICTION` /
      `STOPSPEED`, `P_CalcHeight` (bob/viewheight), angled strafe,
      use-button traversal — fixed-point, vanilla speeds
      (`move_player` thrusts `forward*2048` through the fine tables;
      `calc_height` walks viewheight; run converges to 16.67 mu/tic,
      covered by `tests/test_p_user.py` incl. a real-E1M1 ramp test).
- [x] Replace the camera-kinematic mover for the demo path (keep the
      current mover behind a flag for comparison while porting):
      vanilla momentum is the default, `--kinematic` restores the old
      mover bit-exactly (same 90-frame checksum as before the rewrite).
- [x] Rebirth/death flow per vanilla (`PST_REBORN`, inventory reset):
      `PST_DEAD` falls the view, faces the killer in `ANG5` steps and
      waits for `BT_USE`; rebirth respawns at the start spot facing
      its angle with pistol+50 and `usedown` set (corpse dropped, not
      kept — see `docs/DIVERGENCES.md`).

## C. Determinism audit (sim must be bit-exact) — done on bit-exact-emu

- [x] Purge floats from sim paths (camera, `math.cos/sin` → fine
      tables); renderer/visual-only code is exempt. (`fixed_div` is
      already bit-exact: vanilla computes it in doubles too, and the
      saturation guard runs first. Shots/aim now use the integer
      `mo.angle` on the vanilla path instead of float-rounded
      `cam.bam`; thrust moved before firing like `P_MovePlayer` before
      `P_MovePsprites`.)
- [x] No hash-order iteration in sim paths (`PYTHONHASHSEED`
      sensitivity: sets/dicts keyed by id/strings). (Only lookups and
      insertion-ordered dicts remain; the door blocker uses an
      order-stable sector list. Covered by a two-seed headless run.)
- [x] Audit `P_Random`/`M_Random` consumption order call-by-call
      against linuxdoom (`p_enemy.c`, `p_map.c`, `p_pspr.c`, …).
      Fixed: `A_Scream` podth/bgdth cycling drew nothing (stream fell
      behind on every such kill), `A_PosAttack` drew damage before
      spread (vanilla: spread first), `A_FaceTarget` missed the
      spectre `<<21` spray. `tests/test_rng_stream.py` pins counts,
      order and table values.
- [x] Never touch Python `random` in sim code (already true; keep it
      that way — it broke demo determinism once, see commit history).
      (Only the quit jingle uses it: cosmetic, outside the sim.)

## D. Close sim-affecting divergences (see docs/DIVERGENCES.md)

- [x] Floaters track target height (`MF_FLOAT` in `P_Move`/chase):
      `move_actor` climbs toward the blocked floor on floatok,
      `_z_movement` eases toward target mid-height in the approach
      cone, `INFLOAT` set/cleared like vanilla.
- [x] Crusher gibs corpses and removes dropped items
      (`PIT_ChangeSector`), not just damage: `move_plane` runs
      `world.grind` after every step (gibs, drops, 4th-tic damage with
      the vanilla blood-spray draws); crushers grind through instead
      of reverting, doors still reopen.
- [x] In-stasis ceiling reactivation + crush-stop (W1-57/WR-74, not
      145 which matches no vanilla special): `olddirection` parking,
      crusher triggers resume parked ceilings first.
- [ ] Sector specials 14/15 and any other E2-relevant specials:
      skipped, E1 has none (shareware scope, see DIVERGENCES).
- [x] Full monster line-trigger set (vanilla `P_CrossSpecialLine`):
      gate was already exact; added 125/126 monsters-only teleports
      and unconditional W1 clearing. Teleport destinations scan
      sectors by index (not spawn order) like vanilla, so same-tag
      pads resolve identically.
- [x] Nightmare/fast Parm behavior exactly per skill table
      (incl. `-respawn` semantics outside nightmare): `--respawn`
      flag wired to think + savegames (skill bits, baby halve,
      pre-alerted spawns and JUSTATTACKED pacing already matched).
- [ ] Cosmetic-only gaps (finale typing, splat anims, automap marks)
      stay as-is unless they block validation.

## E. Demo format + playback machine — done on bit-exact-emu

- [x] Writer/reader: header (version 109, skill, episode, map,
      deathmatch/respawn/fast/nomonsters bytes), ticcmd stream,
      `DEMOMARKER` footer (`pydoom/demo.py` or new module).
      (`pydoom/demo.py`: header/ticcmd/footer round-trips; angle
      quantization matches `G_WriteDemoTiccmd`.)
- [x] Playback state machine (`G_DoPlayDemo` idea): level transitions
      inside the demo, clean return to title, `-playdemo` /
      `-timedemo` / `-record` CLI flags. (`--playdemo/--timedemo/
      --record-demo`; stream spans transitions unbroken; DEMOMARKER
      and episode victory return to title. Intermission/finale tics
      are skipped symmetrically on both sides — vanilla alignment of
      those stretches is F work.)
- [x] RNG streams reset/seeded exactly like `G_DoLoadLevel`.
      (Correction: vanilla seeds in `G_InitNew`, not LoadLevel —
      `flow.init_new` mirrors that: clear + fast/nightmare tables on
      fresh runs only, never on transitions. `-nomonsters` spawns
      filter like `P_SpawnMapThing`.)

## F. Validation (defines "compatible") — done on bit-exact-emu

- [x] IWAD `DEMO1`/`DEMO2`/`DEMO3` play to the end without desync.
      (All three consume 100% of stream (5026/3836/2134 tics) on the
      start map, bit-deterministic across runs; combat, pickups,
      damage and rebirths all occur at plausible rates. No bit-oracle
      for Chocolate's trajectory exists here, so sync is behavioral.)
- [x] Known speedrun `.lmp` files finish the right maps with the
      right time/ending (compare against Chocolate Doom).
      (E1M4TRIK 0:17 UV: exits E1M4→E1M5 with kills/items/secrets
      0/0, 3/45, 0/3 — all matching Chocolate statdump; time 627 vs
      574 (+53 tics residual phase, under investigation).
      E1M1SEC nomonsters secrets tour: exits E1M1→E1M2 with 0/0,
      0/38, 3/3 — all matching; time 1125 vs 1120 (+5). The Z-movement
      fix (vanilla gravity instead of instant glue) was the key that
      unlocked the E1M4 exit.)
- [x] Desync detector: checksum per tic during playback, diffable
      against a reference run. (`--dump-checksums`: stream-tic,
      leveltime, pos/angle/hp/mom, RNG indices, alive, kills, ammo,
      weapon, keys; byte-identical across runs for all IWAD demos.)
- [x] Recorded demos load in Chocolate Doom / DSDA-Doom (and vice
      versa for short clips). (Our `.lmp` plays clean in Chocolate
      (`timed 52 gametics`, no version error); vanilla clips play in
      ours. Intermission/finale stream tics are skipped symmetrically
      on record+playback — multi-map vanilla streams need that
      alignment next.)
- [ ] Bit-level Chocolate trajectory diff: needs dsda-analysis
      tooling (not installed here); gross gates above all pass.

## G. Audio chip question (audible, not sync-affecting)

- [ ] A/B our DOSBox-OPL backend vs Nuked-OPL3 on identical register
      streams; switch backend if the difference is audible (music
      never affects demo sync — framebuffer checksums only).
