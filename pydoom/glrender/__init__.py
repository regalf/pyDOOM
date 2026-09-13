"""OpenGL renderer (milestone H): native-res GL port of renderer.py.

Phase 0: only backend selection and context setup live here
(`state.py`); the software raster stays the reference and the
auto-fallback. Later phases add textures/preprocess/draw/light/
present modules behind the same facade, so tools/doom_view.py never
imports GL submodules directly.
"""

from pydoom.glrender.state import (
    OPENGL,
    SOFTWARE,
    resolve_api,
    try_init,
)

__all__ = ["OPENGL", "SOFTWARE", "resolve_api", "try_init"]
