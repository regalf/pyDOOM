"""GL backend selection and context setup (milestone H, phase 0).

Two layers: resolve_api() is pure logic (unit tested, never touches
pygame or GL), try_init() performs the window/context dance and always
returns a usable software window on any failure (never raises out of
the viewer).
"""

from __future__ import annotations

import os

SOFTWARE = "software"
OPENGL = "opengl"


def resolve_api(want: str, sdl_video: str | None = None,
                frames_opt: int | None = None,
                timedemo: bool = False,
                have_moderngl: bool = False) -> tuple:
    """Pick the effective backend (pure: no pygame/GL imports).

    Returns (effective, reason). Anything but "opengl" requested means
    software; opengl falls back to software on headless/dummy video,
    smoke runs (--frames), timedemo (no draw) or a missing moderngl.
    """
    if want != OPENGL:
        return SOFTWARE, f"requested {want!r}"
    if (sdl_video or os.environ.get("SDL_VIDEODRIVER", "")) == "dummy":
        return SOFTWARE, "dummy SDL video (headless CI)"
    if frames_opt is not None:
        return SOFTWARE, "--frames smoke run (sim reference)"
    if timedemo:
        return SOFTWARE, "timedemo (no draw)"
    if not have_moderngl:
        return SOFTWARE, "moderngl missing"
    return OPENGL, "opengl requested and available"


def _have_moderngl() -> bool:
    """Import probe (no context created, no side effects)."""
    try:
        import moderngl  # noqa: F401
    except ImportError:
        return False
    return True


def try_init(width: int, height: int, want: str,
             frames_opt: int | None = None,
             timedemo: bool = False) -> tuple:
    """Create the viewer window for the wanted backend.

    Returns (screen, ctx, effective, reason): ctx is the moderngl
    context on the opengl path, else None. Every failure mode falls
    back to a plain software window and reports why; never raises.
    pygame.init() must have run before this call.
    """
    import pygame
    effective, reason = resolve_api(want, None, frames_opt, timedemo,
                                    _have_moderngl())
    if effective == SOFTWARE:
        return pygame.display.set_mode((width, height)), None, \
            SOFTWARE, reason
    try:
        screen = pygame.display.set_mode(
            (width, height), pygame.OPENGL | pygame.DOUBLEBUF)
    except Exception as exc:  # noqa: BLE001 - any window failure falls back
        screen = pygame.display.set_mode((width, height))
        return screen, None, SOFTWARE, f"opengl window failed ({exc})"
    try:
        import moderngl
        ctx = moderngl.create_context()
    except Exception as exc:  # noqa: BLE001 - any GL failure falls back
        screen = pygame.display.set_mode((width, height))
        return screen, None, SOFTWARE, f"gl context failed ({exc})"
    return screen, ctx, OPENGL, reason
