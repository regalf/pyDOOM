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
