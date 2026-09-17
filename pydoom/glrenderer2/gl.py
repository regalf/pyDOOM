"""Caching GL facade for glrenderer2 (P0 state-cache slice).

Same attribute names as OpenGL.GL: constants and uncached calls fall
through via module __getattr__ (lazy, so importing this never touches
GL and headless/sim stays import-clean). Redundant state changes are
skipped: program, active texture unit, per-unit texture binds,
VAO binds, cap toggles, depth mask, blend func, and uniform uploads
(last-value map by current program + location).

Single-context assumption: cached ids can be reused by GL after a
delete, so FrameRenderer2 resets the cache on construction and on
close (drop_gl_resources paths rebuild programs from scratch).

Tests inject a fake backend by assigning gl._real (restored to None
afterwards); stats count issued vs skipped calls for the profiler.
"""

from __future__ import annotations

__all__ = [
    "cache_reset",
    "delete_fence",
    "place_fence",
    "pop_group",
    "push_group",
    "stats",
    "wait_fence",
]

_real = None


def _api():
    """Real OpenGL.GL, bound on first use (never at import)."""
    global _real
    if _real is None:
        from OpenGL import GL as _r
        _real = _r
    return _real


def __getattr__(name: str):
    """Constants and uncached entry points pass straight through."""
    return getattr(_api(), name)


_program = 0
_active_unit = -1
_bound: dict = {}  # (unit, target) -> tex
_vao = 0
_caps: dict = {}  # cap -> bool
_depth_mask = True
_blend = None  # (src, dst) or None when blending disabled
_samplers: dict = {}  # unit -> sampler object (0 = none bound)
_uniforms: dict = {}  # (program, loc) -> last values
_stats = {"calls": 0, "skipped": 0}


def stats() -> dict:
    """Issued/skipped counters (read-only snapshot for the profiler)."""
    return dict(_stats)


def cache_reset() -> None:
    """Forget all cached state (context rebuilds, renderer swaps)."""
    global _program, _active_unit, _vao, _depth_mask, _blend
    _program = 0
    _active_unit = -1
    _bound.clear()
    _vao = 0
    _caps.clear()
    _depth_mask = True
    _blend = None
    _samplers.clear()
    _uniforms.clear()


def _hit() -> None:
    _stats["calls"] += 1


def _skip() -> None:
    _stats["skipped"] += 1


def glUseProgram(prog: int) -> None:
    global _program
    prog = int(prog)
    if prog == _program:
        _skip()
        return
    _program = prog
    _hit()
    _api().glUseProgram(prog)


def glActiveTexture(unit: int) -> None:
    global _active_unit
    unit = int(unit)
    if unit == _active_unit:
        _skip()
        return
    _active_unit = unit
    _hit()
    _api().glActiveTexture(unit)


def glBindTexture(target: int, tex: int) -> None:
    target, tex = int(target), int(tex)
    if _bound.get(_active_unit) == (target, tex):
        _skip()
        return
    _bound[_active_unit] = (target, tex)
    _hit()
    _api().glBindTexture(target, tex)


def glBindVertexArray(vao: int) -> None:
    global _vao
    vao = int(vao)
    if vao == _vao:
        _skip()
        return
    _vao = vao
    _hit()
    _api().glBindVertexArray(vao)


def glEnable(cap: int) -> None:
    cap = int(cap)
    if _caps.get(cap, False) is True:
        _skip()
        return
    _caps[cap] = True
    _hit()
    _api().glEnable(cap)


def glDisable(cap: int) -> None:
    cap = int(cap)
    if _caps.get(cap, False) is False:
        _skip()
        return
    _caps[cap] = False
    _hit()
    _api().glDisable(cap)


def glDepthMask(flag) -> None:
    global _depth_mask
    flag = bool(flag)
    if flag == _depth_mask:
        _skip()
        return
    _depth_mask = flag
    _hit()
    _api().glDepthMask(flag)


def glBlendFunc(src: int, dst: int) -> None:
    global _blend
    key = (int(src), int(dst))
    if key == _blend:
        _skip()
        return
    _blend = key
    _hit()
    _api().glBlendFunc(src, dst)


def glBindSampler(unit: int, sampler: int) -> None:
    """Sampler object per texture unit (v2 linear filtering); 0
    unbinds back to the texture's own params."""
    unit, sampler = int(unit), int(sampler)
    if _samplers.get(unit, 0) == sampler:
        _skip()
        return
    _samplers[unit] = sampler
    _hit()
    _api().glBindSampler(unit, sampler)


def _uniform(loc: int, values: tuple) -> bool:
    """True when the upload can be skipped: only when THIS program /
    location already holds THESE values (last-value map, not a
    seen-set: A,B,A must re-issue the final A, GL still holds B)."""
    key = (_program, int(loc))
    if _uniforms.get(key) == values:
        _skip()
        return True
    _uniforms[key] = values
    _hit()
    return False


def glUniform1i(loc: int, v: int) -> None:
    if _uniform(loc, (int(v),)):
        return
    _api().glUniform1i(loc, v)


def glUniform1f(loc: int, v: float) -> None:
    if _uniform(loc, (float(v),)):
        return
    _api().glUniform1f(loc, v)


def glUniform2f(loc: int, x: float, y: float) -> None:
    if _uniform(loc, (float(x), float(y))):
        return
    _api().glUniform2f(loc, x, y)


def glUniformMatrix4fv(loc: int, count: int, transpose,
                       value) -> None:
    try:
        key = (bool(transpose), bytes(value))
    except (TypeError, ValueError):
        key = (bool(transpose), repr(value))
    if _uniform(loc, key):
        return
    _api().glUniformMatrix4fv(loc, count, transpose, value)


def glUniform4fv(loc: int, count: int, value) -> None:
    """vec4 array upload (dynlight lists); last-value cached."""
    try:
        import numpy as _np
        key = bytes(_np.ascontiguousarray(value, dtype=_np.float32))
    except Exception:  # noqa: BLE001 - unhashable oddity: always issue
        _hit()
        _api().glUniform4fv(loc, count, value)
        return
    if _uniform(loc, ("4fv", key)):
        return
    _api().glUniform4fv(loc, count, value)


def place_fence():
    """Fence after the frame's dynamic draws (None when unsupported).

    The next frame waits on it before refilling dynamic VBOs; the
    per-frame orphan (glBufferData with data) keeps correctness when
    the wait expires, the fence just avoids the stall.
    """
    api = _api()
    try:
        return api.glFenceSync(api.GL_SYNC_GPU_COMMANDS_COMPLETE, 0)
    except Exception:  # noqa: BLE001 - GL without sync objects
        return None


def wait_fence(sync, timeout_ns: int = 1000000) -> bool:
    """True when the fence already signaled within the timeout (1 ms
    default: poll-ish, never blocks the frame; expiry just counts a
    stall and the orphan covers correctness). None sync: trivially
    signaled."""
    if sync is None:
        return True
    api = _api()
    try:
        done = api.glClientWaitSync(
            sync, api.GL_SYNC_FLUSH_COMMANDS_BIT, int(timeout_ns))
        signaled = (api.GL_ALREADY_SIGNALED,
                    api.GL_CONDITION_SATISFIED)
    except Exception:  # noqa: BLE001 - assume progress, never stall
        return True
    return done in signaled


def delete_fence(sync) -> None:
    """Drop a fence (teardown / superseded); None is a no-op."""
    if sync is None:
        return
    try:
        _api().glDeleteSync(sync)
    except Exception:  # noqa: BLE001 - teardown never raises
        pass


def push_group(name: str) -> None:
    """KHR debug group push (renderdoc shows passes by name).

    Silent no-op without KHR_debug: observability must never break
    rendering (older drivers, odd PyOpenGL builds).
    """
    try:
        api = _api()
        api.glPushDebugGroup(api.GL_DEBUG_SOURCE_APPLICATION,
                             0, -1, name)
    except Exception:  # noqa: BLE001 - see above
        pass


def pop_group() -> None:
    """KHR debug group pop (pairs with push_group; never raises)."""
    try:
        _api().glPopDebugGroup()
    except Exception:  # noqa: BLE001 - see above
        pass
