"""GL backend selection and context setup (milestone H, phase 0).

Two layers: resolve_api() is pure logic (unit tested, never touches
pygame or GL), try_init() performs the window/context dance and always
returns a usable software window on any failure (never raises out of
the viewer).

Backend is PyOpenGL stable (PyOpenGL_accelerate optional speed-up,
never required): the pygame OPENGL window already owns the context,
so setup only verifies it answers a GL_VERSION query.
"""

from __future__ import annotations

import os

SOFTWARE = "software"
OPENGL = "opengl"


def resolve_api(want: str, sdl_video: str | None = None,
                frames_opt: int | None = None,
                timedemo: bool = False,
                have_gl: bool = False) -> tuple:
    """Pick the effective backend (pure: no pygame/GL imports).

    Returns (effective, reason). Anything but "opengl" requested means
    software; opengl falls back to software on headless/dummy video,
    smoke runs (--frames), timedemo (no draw) or a missing PyOpenGL.
    """
    if want != OPENGL:
        return SOFTWARE, f"requested {want!r}"
    if (sdl_video or os.environ.get("SDL_VIDEODRIVER", "")) == "dummy":
        return SOFTWARE, "dummy SDL video (headless CI)"
    if frames_opt is not None:
        return SOFTWARE, "--frames smoke run (sim reference)"
    if timedemo:
        return SOFTWARE, "timedemo (no draw)"
    if not have_gl:
        return SOFTWARE, "PyOpenGL missing"
    return OPENGL, "opengl requested and available"


def _have_gl() -> bool:
    """Import probe (no context created, no side effects).

    NOTE: only the pure-Python PyOpenGL is required here;
    PyOpenGL_accelerate merely speeds up GL calls when present.
    """
    try:
        import OpenGL.GL  # noqa: F401
    except ImportError:
        return False
    return True


def _gl_version() -> str | None:
    """GL_VERSION of the current context (None when unusable)."""
    try:
        from OpenGL import GL
        raw = GL.glGetString(GL.GL_VERSION)
    except Exception:  # noqa: BLE001 - any GL failure falls back
        return None
    if not raw:
        return None
    try:
        return raw.decode("ascii", "replace")
    except Exception:  # noqa: BLE001 - defensive, still falls back
        return None


def try_init(width: int, height: int, want: str,
             frames_opt: int | None = None,
             timedemo: bool = False) -> tuple:
    """Create the viewer window for the wanted backend.

    Returns (screen, gl_info, effective, reason): gl_info is the GL
    version string on the opengl path, else None. Every failure mode
    falls back to a plain software window and reports why; never
    raises. pygame.init() must have run before this call.
    """
    import pygame
    effective, reason = resolve_api(want, None, frames_opt, timedemo,
                                    _have_gl())
    if effective == SOFTWARE:
        return pygame.display.set_mode((width, height)), None, \
            SOFTWARE, reason
    try:
        screen = pygame.display.set_mode(
            (width, height), pygame.OPENGL | pygame.DOUBLEBUF)
    except Exception as exc:  # noqa: BLE001 - any window failure falls back
        screen = pygame.display.set_mode((width, height))
        return screen, None, SOFTWARE, f"opengl window failed ({exc})"
    # NOTE: the pygame window owns the context; PyOpenGL just talks to
    # it, so a GL_VERSION answer proves the path is live.
    ver = _gl_version()
    if ver is None:
        screen = pygame.display.set_mode((width, height))
        return screen, None, SOFTWARE, "gl context not answering"
    return screen, ver, OPENGL, f"{reason} (GL {ver})"
