"""glrenderer2: state-cached OpenGL renderer (Openglv2).

V1 parity path, driven through the caching GL facade (gl.py) with a
frame profiler (profile.py): same programs, same pixels, fewer GL
calls. FrameRenderer2 mirrors v1's FrameRenderer method for method
(same constructor, same public calls) so the viewer swaps one line.
"""

from pydoom.glrenderer2 import gl as _glmod  # noqa: F401 (re-export)
from pydoom.glrenderer2.gl import cache_reset, stats
from pydoom.glrenderer2.profile import Profiler

__all__ = ["Profiler", "cache_reset", "stats"]
