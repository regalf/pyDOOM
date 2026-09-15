"""Extension API v0 (rough cut): hook bus + manifests with dependencies.

Mods live in mods/<id>/{mod.toml,mod.py} and only see the fields
documented in extensions-roadmap.md (never engine globals): when a big
refactoring moves code, the adapter here and in the viewer gets
reintegrated, not the mods. A raising mod is logged and skipped, never
a crash.
"""

import importlib.util
import os
import sys
import tomllib

API_VERSION = 1

_current = None


def set_current(mgr) -> None:
    """The viewer registers the live manager (combat reuses it for on_kill)."""
    global _current
    _current = mgr


def current():
    return _current


def default_mods_dir() -> str:
    """Where third-party mods live.

    Frozen (PyInstaller onedir): next to the exe, so mods dropped in
    after the build just work, no rebuild. Source tree: repo mods/.
    Mods are runtime-loaded source (.toml + .py), never frozen in.
    """
    if getattr(sys, "frozen", False):
        return os.path.join(os.path.dirname(sys.executable), "mods")
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "mods")


# NOTE: hook -> sim? Only on_kill touches sim objects (the drop), but the
# pickup sweep is XY-only (pickup.collect_touched never looks at z), so
# animating z is cosmetic. It stays flagged sim because it gets the real
# mobj.
HOOKS = ("map_load", "pre_tic", "post_tic", "on_kill",
         "mouse_motion", "camera", "post_overlay", "settings",
         "build_ticcmd", "damage", "pickup", "pre_fire", "post_fire",
         "player_think", "line_activate", "level_exit", "palette_flash",
         "automap_draw", "sfx_play", "music_change", "backend_changed",
         "map_unload", "sector_crush", "teleport", "statusbar",
         "demo_start", "demo_stop")
SIM_HOOKS = frozenset({"on_kill", "build_ticcmd", "damage", "pickup",
                       "pre_fire", "post_fire", "player_think",
                       "line_activate", "sector_crush", "teleport"})

# NOTE: mouse_motion also carries sens_rad_per_px (slider mapping, same
# factor the viewer feeds ticcmd): look mods scale by it instead of a
# private constant, so the options slider drives them too.
SETTINGS_FIELDS = ("mouse_sens", "mouse_rad_per_px", "sfx_vol", "mus_vol")


def settings_snapshot(s) -> dict:
    """Documented settings copy for the settings hook (read-only: mods
    must never write these back; the menu owns them). Duck-typed so ext
    never imports the menu."""
    sens = int(getattr(s, "mouse_sens", 4))
    return {"mouse_sens": sens,
            "mouse_rad_per_px": 0.0004 + sens * 0.0006,
            "sfx_vol": int(getattr(s, "sfx_vol", 8)),
            "mus_vol": int(getattr(s, "mus_vol", 8))}


class Event:
    """Hook container: readable/writable fields + consume()."""

    def __init__(self, hook: str, **fields):
        self.hook = hook
        self.__dict__["fields"] = dict(fields)
        self.consumed = False

    def consume(self) -> None:
        self.consumed = True

    def __getattr__(self, name: str):
        try:
            return self.fields[name]
        except KeyError:
            raise AttributeError(name) from None

    def __setattr__(self, name: str, value) -> None:
        if name in ("hook", "consumed"):
            object.__setattr__(self, name, value)
        else:
            self.fields[name] = value


class ExtApi:
    """The view a mod gets in on_enable: only on(), nothing else."""

    def __init__(self, manager, owner: str):
        self._manager = manager
        self._owner = owner

    def on(self, hook: str, fn, priority: int = 0) -> None:
        if hook not in HOOKS:
            raise ValueError(f"unknown hook: {hook}")
        self._manager._table.setdefault(hook, []).append(
            (owner_priority_key(priority), self._owner, fn))

    def text(self, fb, text: str, x: int, y: int) -> None:
        """Cosmetic helper: writes on the framebuffer via the menu font."""
        menu = self._manager.menu
        if menu is not None:
            menu.draw_text(fb, text, x, y)


def owner_priority_key(priority: int) -> int:
    return -int(priority)


class Mod:
    """Mod base: metadata as attributes, hooks in on_enable."""

    id = "unnamed"
    version = "0.0.0"
    api = API_VERSION
    depends: tuple = ()
    backend = "any"  # any | opengl | software (manifest wins over this)
    sim_affecting = False
    description = ""

    def on_enable(self, api: ExtApi) -> None:
        pass

    def on_disable(self) -> None:
        pass


class ModRecord:
    def __init__(self, meta: dict, mod: Mod | None):
        self.meta = meta
        self.mod = mod
        self.state = "off"  # on | off | refused | error
        self.reason = ""
        self.user_on = bool(meta.get("enabled_default", True))


class ModManager:
    """Discovers mods/, resolves depends, dispatches hooks safely."""

    def __init__(self, mods_dir: str):
        self.mods_dir = mods_dir
        self.records: dict[str, ModRecord] = {}
        self._table: dict[str, list] = {}
        self._order: list[str] = []
        self.menu = None  # injected by the viewer (for ExtApi.text)
        self.demo_guard = False  # True during playback/timedemo: skip sim
        self.backend = "any"  # unknown until viewer set_backend()

    def set_backend(self, name: str) -> None:
        """Live backend switch (viewer video/fallback path): re-resolves."""
        if name not in ("opengl", "software"):
            raise ValueError(f"unknown backend: {name}")
        if name == self.backend:
            return
        old = self.backend
        self.backend = name
        print(f"ext: backend {name}")
        self.refresh()
        for mid, ver, state, reason in self.status():
            print(f"ext: {mid} {ver} {state}"
                  f"{' (' + reason + ')' if reason else ''}")
        # NOTE: after the re-gate (newly enabled mods hear it too).
        self.emit("backend_changed", old=old, new=name)

    # -- discovery and resolution --

    def discover(self) -> None:
        if not os.path.isdir(self.mods_dir):
            return
        for entry in sorted(os.listdir(self.mods_dir)):
            path = os.path.join(self.mods_dir, entry)
            toml = os.path.join(path, "mod.toml")
            code = os.path.join(path, "mod.py")
            if not (os.path.isfile(toml) and os.path.isfile(code)):
                continue
            try:
                with open(toml, "rb") as fh:
                    meta = dict(tomllib.load(fh))
            except Exception as exc:  # noqa: BLE001 - broken manifest
                rec = ModRecord({"id": entry}, None)
                rec.state, rec.reason = "error", f"BAD MANIFEST: {exc}"
                self.records[entry] = rec
                continue
            mid = str(meta.get("id", entry))
            mod = self._load_module(mid, code)
            if mod is None:
                continue
            meta.setdefault("depends", [])
            self.records[mid] = ModRecord(meta, mod)

    def _load_module(self, mid: str, code: str) -> Mod | None:
        try:
            spec = importlib.util.spec_from_file_location(
                f"pydoom_mods_{mid}", code)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            mod = getattr(module, "MOD", None)
            if not isinstance(mod, Mod):
                raise TypeError("mod.py must expose MOD (ext.Mod subclass)")
            return mod
        except Exception as exc:  # noqa: BLE001 - broken mod, never crash
            rec = ModRecord({"id": mid}, None)
            rec.state, rec.reason = "error", f"BAD MODULE: {exc}"
            self.records[mid] = rec
            print(f"ext: {mid} refused ({rec.reason})")
            return None

    def refresh(self) -> None:
        """(Re)activate by depends + user_on, in topological order."""
        for mid in self._topo_order():
            rec = self.records[mid]
            if rec.mod is None:
                continue  # error already recorded in discover/load
            if rec.meta.get("api_version", 1) != API_VERSION:
                self._set_state(rec, "refused",
                                f"API v{rec.meta.get('api_version', '?')}")
                continue
            missing = self._missing_dep(rec)
            if missing is not None:
                self._set_state(rec, "refused", f"NEEDS {missing}")
                continue
            want = rec.meta.get("backend",
                                getattr(rec.mod, "backend", "any"))
            if want not in ("any", "opengl", "software"):
                self._set_state(rec, "refused", f"BAD backend {want}")
                continue
            # NOTE: backend "any" means not yet known (viewer sets it at
            # boot): never refuse on backend grounds until it is real.
            if want != "any" and self.backend != "any" \
                    and want != self.backend:
                self._set_state(rec, "refused", f"NEEDS {want}")
                continue
            if not rec.user_on:
                self._set_state(rec, "off", "")
                continue
            self._set_state(rec, "on", "")

    def _missing_dep(self, rec: ModRecord) -> str | None:
        mod = rec.mod
        wants = list(getattr(mod, "depends", ()))
        wants += [d for d in rec.meta.get("depends", []) if d not in wants]
        for dep in wants:
            other = self.records.get(dep)
            if other is None or other.state != "on":
                return dep
        return None

    def _topo_order(self) -> list[str]:
        order, seen, temp = [], set(), set()

        def visit(mid: str) -> None:
            if mid in seen or mid not in self.records:
                return
            if mid in temp:
                return  # cycle: partial activation beats a crash
            temp.add(mid)
            rec = self.records[mid]
            if rec.mod is not None:
                for dep in getattr(rec.mod, "depends", ()):
                    visit(dep)
                for dep in rec.meta.get("depends", []):
                    visit(dep)
            temp.discard(mid)
            seen.add(mid)
            order.append(mid)

        for mid in sorted(self.records):
            visit(mid)
        return order

    def _set_state(self, rec: ModRecord, state: str, reason: str) -> None:
        old = rec.state
        rec.state, rec.reason = state, reason
        if old == "on" and state != "on":
            self._drop_handlers(rec)
            try:
                rec.mod.on_disable()
            except Exception as exc:  # noqa: BLE001 - never crash
                print(f"ext: {rec.meta.get('id')} on_disable: {exc}")
        elif old != "on" and state == "on":
            try:
                rec.mod.on_enable(ExtApi(self, rec.meta.get("id", "?")))
            except Exception as exc:  # noqa: BLE001 - never crash
                rec.state, rec.reason = "error", str(exc)
                print(f"ext: {rec.meta.get('id')} on_enable: {exc}")

    def _drop_handlers(self, rec: ModRecord) -> None:
        mid = rec.meta.get("id")
        for hook in list(self._table):
            self._table[hook] = [h for h in self._table[hook]
                                 if h[1] != mid]

    # -- runtime --

    def emit(self, hook: str, **fields) -> Event:
        ev = Event(hook, **fields)
        for _negprio, owner, fn in sorted(self._table.get(hook, []),
                                          key=lambda h: (h[0], h[1])):
            rec = self.records.get(owner)
            if rec is None or rec.state != "on":
                continue
            if self.demo_guard and hook in SIM_HOOKS \
                    and rec.mod.sim_affecting:
                continue
            try:
                fn(ev)
            except Exception as exc:  # noqa: BLE001 - broken mod, skip
                print(f"ext: {owner} hook {hook} failed: {exc}")
            if ev.consumed:
                break
        return ev

    def toggle(self, mid: str) -> str:
        """User flip (from the menu): returns the resulting state."""
        rec = self.records.get(mid)
        if rec is None or rec.mod is None:
            return "error"
        rec.user_on = not rec.user_on
        self.refresh()
        return rec.state

    def status(self) -> list[tuple]:
        """Rows for the Extension menu: (id, version, state, reason)."""
        rows = []
        for mid in sorted(self.records):
            rec = self.records[mid]
            meta = rec.meta
            mod = rec.mod
            rows.append((mid, getattr(mod, "version", meta.get("version", "?"))
                         if mod is not None else meta.get("version", "?"),
                         rec.state, rec.reason))
        return rows
