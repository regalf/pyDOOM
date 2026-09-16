# Hooks

Every hook: name, kind, payload fields, where it fires, what `consume()`
does. Kind `sim` = skipped for `sim_affecting` mods while `demo_guard`
is on (playback/timedemo). Kind `fx` = cosmetic, always runs. Only the
listed fields are API; `consume()` stops later handlers in priority
order (higher `priority=` first).

## Lifecycle / flow (fx unless noted)

| hook | payload | fires | consume |
|---|---|---|---|
| `map_load` | `{map, skill}` | end of `load_map` (boot, transitions, warps, snapshots, new game) | n/a (notify) |
| `map_unload` | `{map}` | top of `load_map` when the marker changes | n/a |
| `level_exit` | `{exited, entering, secret}` read-only | exit switch, after `flow.next_map` | n/a |
| `demo_start` | `{mode}`: `record`/`playback`/`timedemo`/`attract` | recorder armed, reader armed, attract armed | n/a |
| `demo_stop` | `{mode}` | recorder flushed, playback over/stopped | n/a |
| `gamestate` | `{old, new}` (`level`/`menu`/`wipe`/`title`/`inter`/`finale`; `old` None at boot) | change-detected, every frame | n/a |
| `backend_changed` | `{old, new}` (`any` = unknown at boot) | `ModManager.set_backend`, after the re-gate | n/a |
| `settings` | `{mouse_sens, mouse_rad_per_px, sfx_vol, mus_vol}` read-only | change-detected, every frame | n/a |

## Tic / player (mixed)

| hook | payload | fires | consume |
|---|---|---|---|
| `pre_tic` | `{tic}` | top of each sim tic | stops chain |
| `post_tic` | `{tic}` | after leveltime closes the tic | stops chain |
| `build_ticcmd` | `{forwardmove, sidemove, angleturn, buttons}` mutable ints | live packets only (playback bypasses); the filtered packet is recorded | stops chain (packet keeps current values) |
| `player_think` | `{tic}` | after the whole vanilla player block, before mobjs think | stops chain |
| `aim` | `{target}` bool: center-line hitscan hot | after `player_think`, only when a mod listens (one ray/tic, skipped otherwise) | n/a (notify; exact line, no auto-aim spread) |

`build_ticcmd` ranges: moves ±`MAXPLMOVE` (50), `angleturn` int16
(CCW+), `buttons` bitmask — see `ids.md`. Broken (non-int) values are
ignored, the packet survives.

## Combat (sim)

| hook | payload | fires | consume |
|---|---|---|---|
| `damage` | `{target, inflictor, source, amount}` mutable, clamped ≥ 0 | `damage_mobj`, pre-armor/pre-skill | skips everything: no armor, thrust, pain, death |
| `on_kill` | `{target_type, drop, fall_from}`; `drop` exposes `x,y,z,floorz,momz,type,dead,flags` | `kill_mobj` after the clip/shotgun drop spawns (`drop` None for dropless kills) | n/a (notify; mutate `drop` instead) |
| `pickup` | `{item, player_mo, ps, kind}` (`kind`: ammo/body/hbonus/abonus/armor/weapon/key/power/backpack/berserk/soul/mega) | `touch_special_thing` before apply | blocks the take (no tally, no sound) |
| `pre_fire` | `{weapon, held}` | `weapons.fire`, before alert/queue | no pull: no alert, no queue, no ammo spent |
| `post_fire` | `{weapon}` | `weapons._shoot`, ammo already spent | n/a |

`fall_from` is mid-victim height in fixed units (captured before the
corpse squish). `damage` sees the raw amount: baby-skill halving,
godmode/invuln, armor and thrust all run on the mutated value after.

## World (sim)

| hook | payload | fires | consume |
|---|---|---|---|
| `line_activate` | `{line, special, kind, side, is_player, mover}`; `kind`: `use`/`shoot`/`cross` | the three `World` entry points, after vanilla gating, before dispatch | blocks the vanilla action |
| `sector_crush` | `{victim, sector}` | `grind_sector` crush tick, before the 10 damage | spares this victim this tic (no damage, no blood) |
| `teleport` | `{mover, line}` (read `line.tag`) | `World.teleport`, after missile/side guards | refuses the hop |

## Camera / input (fx)

| hook | payload | fires | consume |
|---|---|---|---|
| `mouse_motion` | `{dx, dy, consume_x, consume_y, sens_rad_per_px}` | `MOUSEMOTION` in level; `dy` is negated (up+) | axis dropped from the ticcmd (`no_mouse_forward` eats Y) |
| `camera` | `{viewz, pitch, keys_up, keys_down, handled}` | after every vanilla height path | `handled=True` wins (viewer applies `viewz`/`pitch` when alive) |

## Frame / video (fx)

| hook | payload | fires | consume |
|---|---|---|---|
| `post_overlay` | `{fb}` 200x320 uint8, after the `last_fb` copy | every presented frame, before present | n/a (draw on it; never leaks into wipes/snapshots) |
| `statusbar` | `{fb}` | `render_scene`, after the vanilla strip | n/a (draws over the bar, under messages) |
| `palette_flash` | `{palette}` mutable, clamped 0–13 | `player.palette_index` (both backends) | n/a |
| `automap_draw` | `{marks, amap}`; append `(x, y)` fixed world coords | automap open, every frame, before draw/collect | n/a |

HUD helpers (`ExtApi`, usable in `post_overlay` / `statusbar`):

- `api.text(fb, text, x, y)` — red STCFN string via the menu font.
- `api.image(fb, "hud/icon.png", x, y) -> bool` — blits a PNG from the
  mod's own folder onto the index framebuffer: 1:1, clipped, only zero
  alpha skipped, converted once to PLAYPAL indices and cached.
  Declared `[assets] images` in `mod.toml` are preloaded at boot:
  a missing/corrupt entry refuses the mod (`BAD ASSET <path>`) and
  the game starts without it. Undeclared paths lazy-load on first
  use; any failure logs (`ext: <id> bad image ...`) and skips
  (`False`), never raises. Being indices, the art follows
  palette flashes like vanilla HUD. World sprites/walls are out of
  scope (API v2).

## Audio (fx)

| hook | payload | fires | consume |
|---|---|---|---|
| `sfx_play` | `{name, x, y}`; rename swaps the lump | module `audio.play`, the single choke to the mixer | silences (returns `False`) |
| `music_change` | `{song, trigger}`; rename swaps the lump | `audio.music_play` | keeps the current song (returns `False`) |

Renamed sounds still go through `engine.play`: positional
attenuation × `master` (options slider) × mute apply identically.
See `audio.md`.
