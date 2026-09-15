# Extensions

> ## ⚠️ WARNING — MODS ARE PYTHON SCRIPTS
> **A mod is arbitrary Python code and runs with YOUR full user
> privileges. There is NO sandbox: a mod can read, change or delete
> your files, exfiltrate data, or break your saves — exactly like any
> program you choose to run.**
>
> - **Install mods ONLY from sources you trust.** If in doubt, read
>   `mod.py` first: it is plain source code, a few hundred lines at
>   most. If you cannot read it, do not install it.
> - Never run the game (and therefore its mods) as administrator/root.
> - **Disclaimer of liability:** the pydoom authors accept NO
>   responsibility for damage, data loss, or any other harm caused by
>   third-party mods. You install and run them entirely AT YOUR OWN
>   RISK. Mods can also corrupt savegames and desync demos: back up
>   `savegames/` before trying new ones, and disable all mods before
>   reporting engine bugs.

Python mods for pydoom: gameplay tweaks, look/feel changes and map
scripting through a versioned hook bus. A mod never touches engine
globals — it only sees the documented fields in these docs. When a big
refactoring moves engine code, the adapter (`pydoom/ext.py` + viewer
insertion points) gets reintegrated, not the mods.

Docs in this folder:

- `README.md` (this file) — how mods work, lifecycle, rules.
- `hooks.md` — every hook: payload, sim/cosmetic, consume semantics.
- `audio.md` — `sfx_play` / `music_change` plus all sound and music IDs.
- `ids.md` — everything else: weapons, monsters, pickups, keys,
  ticcmd bits, palettes, line specials, menu protocol.

## Folder layout

```
mods/<id>/mod.toml   manifest (id, version, api_version, depends,
                     backend, sim_affecting, enabled_default, ...)
mods/<id>/mod.py     exposes MOD, an ext.Mod subclass
```

Bundled example: `mods/mouselook` (look up/down, OpenGL only,
depends on `no_mouse_forward`).

## Minimal mod

```toml
# mods/hi/mod.toml
id = "hi"
version = "0.1.0"
api_version = 1
description = "Greets the intermission"
depends = []
sim_affecting = false
enabled_default = true
backend = "any"  # any | opengl | software
```

```python
# mods/hi/mod.py
from pydoom.ext import Mod


class Hi(Mod):
    id = "hi"
    version = "0.1.0"

    def on_enable(self, api):
        api.on("level_exit", self._bye, priority=0)

    def _bye(self, ev):
        print(f"hi: leaving {ev.exited} for {ev.entering}")


MOD = Hi()
```

## Lifecycle

1. Viewer builds `ModManager`, discovers `mods/` (see
   `ext.default_mods_dir()` — next to the exe when frozen, so mods
   added after a PyInstaller build just work).
2. `refresh()` resolves in topological order: wrong `api_version`,
   missing/off dependency (`NEEDS <dep>`) and backend mismatch
   (`NEEDS opengl`) refuse loudly with a reason shown in the menu.
3. `on_enable(api)` subscribes hooks; `on_disable()` cleans up.
   Toggling in Options → Extension flips `user_on` and re-resolves
   (dependents cascade off with `NEEDS`).

## Dispatch rules

- `api.on(hook, fn, priority=0)`: higher priority runs first;
  unknown hook names raise immediately (fail fast at enable time).
- `event.consume()` stops the chain. For gating hooks (damage,
  pickup, pre_fire, line_activate, ...) consume also skips the
  vanilla action — see `hooks.md` per hook.
- A raising handler is logged (`ext: <id> hook <h> failed`) and
  skipped. A mod can never crash the game.
- Only documented event fields are API. Anything else is private
  even if reachable: touching it makes the mod broken by definition.
- Main thread only. Hot-path hooks (`pre_tic`, `post_overlay`)
  must stay allocation-free; no per-mobj/per-pixel hooks exist.

## Sim vs cosmetic

Hooks that can change the simulation are flagged sim (`hooks.md`).
Mods setting `sim_affecting = true` are skipped on those hooks while
`demo_guard` is on (playback/timedemo), and should be recorded in
demo headers. Cosmetic hooks (overlay, palette, automap, audio,
camera) always run.

## Extension menu

Options → Extension lists every discovered mod as
`id version ON/OFF (reason)`. Enter toggles (only when dependencies
and backend allow), Esc goes back. Reasons: `NEEDS <dep>`,
`NEEDS <backend>`, `BAD ...`, `ERROR ...`.
