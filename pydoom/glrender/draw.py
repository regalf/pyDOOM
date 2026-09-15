"""GL frame render (milestone H, phase 2, draw slice).

Camera from the fixed-point tables (never math.sin/cos: the basis
matches the software render_view bit for bit up to float rounding),
perspective matrices reproducing the vanilla focals exactly
(x-focal from R_InitTextureMapping, y-focal centerxfrac), then wall
batches plus the plane array. No sprites/masked/sky yet (Phase 3);
those pixels stay cleared black. Readback returns top-down RGB like
applying the PLAYPAL LUT to the software framebuffer.
"""

from __future__ import annotations

import ctypes
import math

import numpy as np

from pydoom import tables
from pydoom.fixed import FRACBITS, fixed_div
from pydoom.glrender import shaders
from pydoom.glrender.light import scalelight_lut, zlight_lut
from pydoom.glrender.sky import SKY_SEGS, sky_index
from pydoom.renderer import (
    FIELDOFVIEW,
    FUZZOFFSETS,
    MAXVISSPRITES,
    SCREENHEIGHT,
    SCREENWIDTH,
)

__all__ = ["FrameRenderer", "camera_frame", "focal_x_factor"]

NEAR, FAR = 0.5, 32768.0


def focal_x_factor() -> float:
    """Vanilla x-focal over centerxfrac (R_InitTextureMapping ratio,
    ~0.99924: the finetangent entry overshoots tan(45deg) a hair)."""
    centerxfrac = (SCREENWIDTH // 2) << FRACBITS
    focallength = fixed_div(
        centerxfrac,
        tables.finetangent[tables.FINEANGLES // 4 + FIELDOFVIEW // 2],
    )
    return focallength / float(centerxfrac)


def camera_frame(viewx: int, viewy: int, viewz: int, angle_bam: int,
                 w: int, h: int, pitch: float = 0.0) -> tuple:
    """ViewProj matrix + view basis (float64 math, float32 upload).

    x_ndc follows the vanilla x mapping (tan-based, x-focal above),
    y_ndc the vanilla y mapping (square pixels: y-focal 160 at
    320x200, i.e. proj11 = w/h for any window of the same aspect).
    Camera looks along +dir with up +z, from the fine tables.
    pitch (radians, +up) is freecam-only: vanilla has no vertical
    look, the game always passes 0 (bit-identical matrix).
    """
    dx = tables.finecosine(angle_bam >> 19) / 65536.0
    dy = tables.finesine[angle_bam >> 19] / 65536.0
    rx, ry = dy, -dx  # NOTE: screen-right (d cross up)
    ex, ey, ez = viewx / 65536.0, viewy / 65536.0, viewz / 65536.0
    cp, sp = math.cos(pitch), math.sin(pitch)
    view = np.array([
        [rx, ry, 0.0, -(rx * ex + ry * ey)],
        [-sp * dx, -sp * dy, cp, sp * dx * ex + sp * dy * ey - cp * ez],
        [-cp * dx, -cp * dy, -sp,
         cp * dx * ex + cp * dy * ey + sp * ez],
        [0.0, 0.0, 0.0, 1.0],
    ], dtype=np.float64)
    fx = focal_x_factor()
    fy = float(w) / float(h)
    proj = np.array([
        [fx, 0.0, 0.0, 0.0],
        [0.0, fy, 0.0, 0.0],
        [0.0, 0.0, (FAR + NEAR) / (NEAR - FAR),
         2.0 * FAR * NEAR / (NEAR - FAR)],
        [0.0, 0.0, -1.0, 0.0],
    ], dtype=np.float64)
    return ((proj @ view).astype(np.float32),
            (float(dx), float(dy)))


class FrameRenderer:
    """Programs + VAOs + light LUTs bound to one GlResources (one
    map). render() draws walls then planes; readback() returns
    top-down RGB for the software-framebuffer comparison."""

    def __init__(self, res, w: int, h: int) -> None:
        from OpenGL import GL
        self._res = res
        self._w, self._h = w, h
        self._uni: dict = {}
        self.wall_prog = shaders.compile_program(shaders.WALL_VERT,
                                                 shaders.WALL_FRAG)
        self.wall_double_prog = shaders.compile_program(
            shaders.WALL_VERT, shaders.WALL_FRAG_DOUBLE)
        self.plane_prog = shaders.compile_program(shaders.PLANE_VERT,
                                                  shaders.PLANE_FRAG)
        self.sprite_prog = shaders.compile_program(
            shaders.SPRITE_VERT, shaders.SPRITE_FRAG)
        self.fuzz_prog = shaders.compile_program(
            shaders.SPRITE_VERT, shaders.FUZZ_FRAG)
        self.sky_prog = shaders.compile_program(shaders.SKY_VERT,
                                                shaders.SKY_FRAG)
        self.psprite_prog = shaders.compile_program(
            shaders.PSPRITE_VERT, shaders.PSPRITE_FRAG)
        self.overlay_prog = shaders.compile_program(
            shaders.OVERLAY_VERT, shaders.OVERLAY_FRAG)
        self.text_prog = shaders.compile_program(shaders.TEXT_VERT,
                                                 shaders.TEXT_FRAG)
        self.auto_prog = shaders.compile_program(shaders.AUTO_VERT,
                                                 shaders.AUTO_FRAG)
        self._scalelight = res._upload_lut(scalelight_lut(), 48, 16,
                                           GL.GL_R8, GL.GL_RED)
        self._zlight = res._upload_lut(zlight_lut(), 128, 16,
                                       GL.GL_R8, GL.GL_RED)
        for prog, samplers in (
                (self.wall_prog, (("uWallTex", 0), ("uScaleLight", 1),
                                  ("uColormap", 2), ("uPalette", 3),
                                  ("uSectorLight", 6))),
                (self.wall_double_prog, (("uWallTex", 0),
                                         ("uScaleLight", 1),
                                         ("uColormap", 2),
                                         ("uPalette", 3),
                                         ("uSectorLight", 6))),
                (self.plane_prog, (("uFlatArray", 0), ("uZLight", 1),
                                   ("uColormap", 2), ("uPalette", 3),
                                   ("uSectorLight", 6))),
                (self.sprite_prog, (("uSpriteTex", 0),
                                    ("uColormap", 2),
                                    ("uPalette", 3))),
                (self.fuzz_prog, (("uSpriteTex", 0),
                                   ("uIndexTex", 4),
                                   ("uColormap", 2),
                                   ("uPalette", 3),
                                   ("uFuzzTex", 5))),
                (self.sky_prog, (("uSkyTex", 0),
                                 ("uColormap", 2),
                                 ("uPalette", 3))),
                (self.psprite_prog, (("uSpriteTex", 0),
                                     ("uPalette", 3))),
                (self.overlay_prog, (("uOverlay", 0),
                                     ("uPalette", 3))),
                (self.text_prog, (("uTextTex", 0),)),
                (self.auto_prog, ())):
            GL.glUseProgram(prog)
            for name, unit in samplers:
                GL.glUniform1i(self._loc(prog, name), unit)
        GL.glUseProgram(0)
        self._frame = 0
        self._fuzzlut = res._upload_lut(
            bytes(255 if v > 0 else 0 for v in FUZZOFFSETS),
            50, 1, GL.GL_R8, GL.GL_RED)
        (self._fbo, self._fb_color, self._fb_index,
         self._fb_depth) = self._make_fbo(w, h)
        self._spare_index = self._new_tex2d(w, h, GL.GL_R8,
                                            GL.GL_RED, None)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        self._wall_vao = self._make_vao(
            res.wall_vbo, 9,
            [(0, 3, 0), (1, 1, 3), (2, 1, 4), (3, 1, 5),
             (4, 1, 6), (5, 2, 7)],
            res.wall_ibo)
        self._plane_vao = self._make_vao(
            res.plane_vbo, 7,
            [(0, 3, 0), (1, 2, 3), (2, 1, 5), (3, 1, 6)], 0)
        # NOTE: sprite VBO/IBO are refilled per frame (dynamic
        # billboards, glBufferData resizes past the initial hint);
        # no 128-quad cap: every passing billboard draws depth-tested.
        self._sprite_vbo = self._new_dynamic(MAXVISSPRITES * 4 * 6)
        self._sprite_ibo = self._new_dynamic(MAXVISSPRITES * 6, True)
        self._sprite_vao = self._make_vao(
            self._sprite_vbo, 6, [(0, 3, 0), (1, 2, 3), (2, 1, 5)],
            self._sprite_ibo)
        # NOTE: sky cylinder (camera-following, refilled per frame);
        # index static (one per cylinder).
        from OpenGL import GL as _GL
        self._sky_vbo = self._new_dynamic((SKY_SEGS + 1) * 2 * 4)
        self._sky_ibo = int(_GL.glGenBuffers(1))
        _GL.glBindBuffer(_GL.GL_ELEMENT_ARRAY_BUFFER, self._sky_ibo)
        _GL.glBufferData(_GL.GL_ELEMENT_ARRAY_BUFFER,
                         sky_index().nbytes, sky_index(),
                         _GL.GL_STATIC_DRAW)
        _GL.glBindBuffer(_GL.GL_ELEMENT_ARRAY_BUFFER, 0)
        self._sky_vao = self._make_vao(
            self._sky_vbo, 4, [(0, 3, 0), (1, 1, 3)],
            self._sky_ibo)
        # NOTE: weapon psprite quad (screen-space, refilled per draw).
        self._psprite_vbo = self._new_dynamic(4 * 4)
        self._psprite_vao = self._make_vao(
            self._psprite_vbo, 4, [(0, 2, 0), (1, 2, 2)], 0)
        # NOTE: overlay index texture (320x200, refilled per frame)
        # plus an empty VAO (fullscreen triangle from gl_VertexID).
        self._overlay_tex = self._new_tex2d(SCREENWIDTH, SCREENHEIGHT,
                                            GL.GL_R8, GL.GL_RED, None)
        self._overlay_vao = int(GL.glGenVertexArrays(1))
        # NOTE: text/automap quads share one dynamic VBO layout
        # ([ndc2, uv-or-color]); text uses its VAO, automap its own.
        self._text_vbo = self._new_dynamic(4 * 4)
        self._text_vao = self._make_vao(
            self._text_vbo, 4, [(0, 2, 0), (1, 2, 2)], 0)
        self._text_texs: list = []
        self._auto_vbo = self._new_dynamic(4096 * 5)
        self._auto_vao = self._make_vao(
            self._auto_vbo, 5, [(0, 2, 0), (1, 3, 2)], 0)
        GL.glDisable(GL.GL_DITHER)  # NOTE: LSB-exact readback parity
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LESS)
        GL.glFrontFace(GL.GL_CCW)  # NOTE: wall fronts wind CCW (the
        GL.glCullFace(GL.GL_BACK)  # wall pass enables CULL_FACE)
        GL.glDisable(GL.GL_CULL_FACE)  # NOTE: planes/sprites/sky/overlay
        # render double-sided; only the wall pass culls
        GL.glDisable(GL.GL_BLEND)
        GL.glViewport(0, 0, w, h)

    def _loc(self, prog: int, name: str) -> int:
        from OpenGL import GL
        key = (prog, name)
        loc = self._uni.get(key)
        if loc is None:
            loc = int(GL.glGetUniformLocation(prog, name))
            self._uni[key] = loc
        return loc

    @staticmethod
    def _new_dynamic(nfloats: int, ints: bool = False) -> int:
        """Empty dynamic buffer (orphaned + refilled per frame)."""
        import numpy as np
        from OpenGL import GL
        buf = int(GL.glGenBuffers(1))
        dtype = np.uint32 if ints else np.float32
        target = (GL.GL_ELEMENT_ARRAY_BUFFER if ints
                  else GL.GL_ARRAY_BUFFER)
        GL.glBindBuffer(target, buf)
        GL.glBufferData(target,
                        np.zeros(nfloats, dtype=dtype).nbytes, None,
                        GL.GL_DYNAMIC_DRAW)
        GL.glBindBuffer(target, 0)
        return buf

    @staticmethod
    def _new_tex2d(w: int, h: int, internal: int, fmt: int,
                   data) -> int:
        from OpenGL import GL
        tex = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER,
                           GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER,
                           GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S,
                           GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T,
                           GL.GL_CLAMP_TO_EDGE)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, internal, w, h, 0, fmt,
                        GL.GL_UNSIGNED_BYTE, data)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return tex

    @classmethod
    def _make_fbo(cls, w: int, h: int) -> tuple:
        """RGB8 + R8-index targets with depth (readback identical to
        the default framebuffer; the index target feeds fuzz)."""
        from OpenGL import GL
        fbo = int(GL.glGenFramebuffers(1))
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, fbo)
        color = cls._new_tex2d(w, h, GL.GL_RGB8, GL.GL_RGB, None)
        index = cls._new_tex2d(w, h, GL.GL_R8, GL.GL_RED, None)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER,
                                  GL.GL_COLOR_ATTACHMENT0,
                                  GL.GL_TEXTURE_2D, color, 0)
        GL.glFramebufferTexture2D(GL.GL_FRAMEBUFFER,
                                  GL.GL_COLOR_ATTACHMENT1,
                                  GL.GL_TEXTURE_2D, index, 0)
        depth = int(GL.glGenRenderbuffers(1))
        GL.glBindRenderbuffer(GL.GL_RENDERBUFFER, depth)
        GL.glRenderbufferStorage(GL.GL_RENDERBUFFER,
                                 GL.GL_DEPTH_COMPONENT24, w, h)
        GL.glFramebufferRenderbuffer(GL.GL_FRAMEBUFFER,
                                     GL.GL_DEPTH_ATTACHMENT,
                                     GL.GL_RENDERBUFFER, depth)
        GL.glDrawBuffers(2, [GL.GL_COLOR_ATTACHMENT0,
                             GL.GL_COLOR_ATTACHMENT1])
        status = GL.glCheckFramebufferStatus(GL.GL_FRAMEBUFFER)
        if status != GL.GL_FRAMEBUFFER_COMPLETE:
            raise RuntimeError(f"FBO incomplete: {status:#x}")
        return fbo, color, index, depth

    @staticmethod
    def _make_vao(vbo: int, stride_floats: int, attribs: list,
                  ibo: int) -> int:
        from OpenGL import GL

        # NOTE: the WRAPPED glVertexAttribPointer is broken with
        # PyOpenGL_accelerate on 3.14 (raises "no valid context" for
        # every pointer form, including None); the raw entry point
        # passes the byte offset straight through (verified
        # glGetError() == 0). Revisit if PyOpenGL 4.x fixes it.
        from OpenGL.raw.GL.VERSION.GL_2_0 import glVertexAttribPointer as raw_pointer
        vao = int(GL.glGenVertexArrays(1))
        GL.glBindVertexArray(vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, vbo)
        for loc, size, offset in attribs:
            GL.glEnableVertexAttribArray(loc)
            raw_pointer(loc, size, GL.GL_FLOAT, False,
                        stride_floats * 4,
                        ctypes.c_void_p(offset * 4))
        if ibo:
            GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, ibo)
        GL.glBindVertexArray(0)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        return vao

    def _pal(self, prog: int, pal_index: int) -> None:
        from OpenGL import GL
        GL.glUniform1i(self._loc(prog, "uPalIndex"), int(pal_index))

    def render(self, viewx: int, viewy: int, viewz: int,
               angle_bam: int, extra_light: int = 0,
               fullbright: bool = False, sprites=None,
               sky=None, frame_no: int | None = None,
               psprites=None, pal_index: int = 0,
               pitch: float = 0.0) -> None:
        """Draw sky (optional) + walls + planes (+ optional sprite
        billboards, fuzz last over a complete backdrop, weapon
        psprites on top) for one camera (raises on GL error: silent
        corruption is worse than a loud test failure). Sprites draw
        last, depth-tested with depth writes on like everything else
        (Doom has no translucency, so order is irrelevant); psprites
        overdraw with no depth test like the software blit. sky is a
        (texture_id, tex_height) tuple or None; psprites a list of
        (tex_id, w, h, leftoff, topoff, bobx, boby) tuples in 320x200
        space. frame_no pins the fuzz shimmer counter (tests); None
        advances it per frame. pitch (radians, +up) is freecam-only
        (vanilla/game never look vertically)."""
        from OpenGL import GL
        res = self._res
        if frame_no is None:
            self._frame += 1
        else:
            self._frame = int(frame_no)
        vp, (dx, dy) = camera_frame(viewx, viewy, viewz, angle_bam,
                                    self._w, self._h, pitch)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if sky is not None:
            self.draw_sky(viewx, viewy, viewz, vp, pal_index, *sky)
        GL.glUseProgram(self.wall_prog)
        self._pal(self.wall_prog, pal_index)
        GL.glUniformMatrix4fv(self._loc(self.wall_prog, "uViewProj"),
                              1, True, vp)
        GL.glUniform2f(self._loc(self.wall_prog, "uViewPos"),
                       viewx / 65536.0, viewy / 65536.0)
        GL.glUniform2f(self._loc(self.wall_prog, "uViewDir"), dx, dy)
        GL.glUniform1i(self._loc(self.wall_prog, "uExtraLight"),
                       int(extra_light))
        GL.glUniform1i(self._loc(self.wall_prog, "uFullbright"),
                       int(bool(fullbright)))
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._scalelight)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.colormap_tex)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, res.palette_tex)
        GL.glActiveTexture(GL.GL_TEXTURE6)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.sector_tex)
        GL.glBindVertexArray(self._wall_vao)
        # NOTE: wall quads wind CCW seen from their front side
        # ((v1,bottom) (v2,bottom) (v2,top) with front RIGHT of
        # v1->v2), so backface culling drops exactly what the BSP
        # never draws: partner-seg backs that would otherwise
        # z-fight their coplanar fronts with a wrong (bright) light
        # row. Planes/sprites/sky keep double-sided rendering below.
        def _run(prog, batches) -> None:
            for texnum, start, count in batches:
                _w, h, wrap = res.wall_info[texnum]
                GL.glActiveTexture(GL.GL_TEXTURE0)
                GL.glBindTexture(GL.GL_TEXTURE_2D,
                                 res.wall_textures[texnum])
                GL.glUniform1f(self._loc(prog, "uWrap"),
                               float(wrap))
                GL.glUniform1f(self._loc(prog, "uTexH"),
                               float(h))
                GL.glDrawElements(GL.GL_TRIANGLES, count,
                                  GL.GL_UNSIGNED_INT,
                                  ctypes.c_void_p(start * 4))

        GL.glEnable(GL.GL_CULL_FACE)
        _run(self.wall_prog, res.wall_batches)
        # NOTE: masked mids ride the same program/VBO (alpha-tested
        # holes, depth written like opaque: Doom has no translucency,
        # so draw order among depth writers is irrelevant).
        _run(self.wall_prog, res.masked_batches)
        GL.glDisable(GL.GL_CULL_FACE)
        # NOTE: single-sided mids draw double-sided (no partner seg
        # covers the back; vanilla draws single-sided backs mirrored)
        # with front-equivalent lighting from WALL_FRAG_DOUBLE.
        GL.glUseProgram(self.wall_double_prog)
        self._pal(self.wall_double_prog, pal_index)
        GL.glUniformMatrix4fv(self._loc(self.wall_double_prog,
                                        "uViewProj"), 1, True, vp)
        GL.glUniform2f(self._loc(self.wall_double_prog, "uViewPos"),
                       viewx / 65536.0, viewy / 65536.0)
        GL.glUniform2f(self._loc(self.wall_double_prog, "uViewDir"),
                       dx, dy)
        GL.glUniform1i(self._loc(self.wall_double_prog,
                                 "uExtraLight"), int(extra_light))
        GL.glUniform1i(self._loc(self.wall_double_prog,
                                 "uFullbright"),
                       int(bool(fullbright)))
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._scalelight)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.colormap_tex)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, res.palette_tex)
        GL.glActiveTexture(GL.GL_TEXTURE6)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.sector_tex)
        GL.glBindVertexArray(self._wall_vao)
        _run(self.wall_double_prog, res.single_batches)
        GL.glUseProgram(self.plane_prog)
        self._pal(self.plane_prog, pal_index)
        GL.glUniformMatrix4fv(self._loc(self.plane_prog, "uViewProj"),
                              1, True, vp)
        GL.glUniform1f(self._loc(self.plane_prog, "uViewZ"),
                       viewz / 65536.0)
        GL.glUniform1f(self._loc(self.plane_prog, "uViewH"),
                       float(self._h))
        GL.glUniform1i(self._loc(self.plane_prog, "uExtraLight"),
                       int(extra_light))
        GL.glUniform1i(self._loc(self.plane_prog, "uFullbright"),
                       int(bool(fullbright)))
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, res.flat_array)
        GL.glActiveTexture(GL.GL_TEXTURE1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._zlight)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.colormap_tex)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, res.palette_tex)
        GL.glActiveTexture(GL.GL_TEXTURE6)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.sector_tex)
        GL.glBindVertexArray(self._plane_vao)
        if res.plane_count:
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, res.plane_count)
        if sprites:
            self.draw_sprites(res, sprites, vp, pal_index)
        if psprites:
            for args in psprites:
                self.draw_psprite(*args, pal_index)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in render")

    def draw_sky(self, viewx: int, viewy: int, viewz: int, vp,
                 pal_index: int, tex_id: int, tex_h: int) -> None:
        """Sky cylinder first (no depth write: walls overdraw it)."""
        from OpenGL import GL

        from pydoom.glrender.sky import build_sky_verts
        verts = build_sky_verts(viewx, viewy, viewz)
        GL.glUseProgram(self.sky_prog)
        self._pal(self.sky_prog, pal_index)
        GL.glUniformMatrix4fv(self._loc(self.sky_prog, "uViewProj"),
                              1, True, vp)
        GL.glUniform1f(self._loc(self.sky_prog, "uViewH"),
                       float(self._h))
        GL.glUniform1f(self._loc(self.sky_prog, "uTexH"),
                       float(tex_h))
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._res.colormap_tex)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, self._res.palette_tex)
        GL.glBindVertexArray(self._sky_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._sky_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts,
                        GL.GL_DYNAMIC_DRAW)
        GL.glDepthMask(GL.GL_FALSE)
        GL.glDrawElements(GL.GL_TRIANGLES, SKY_SEGS * 6,
                          GL.GL_UNSIGNED_INT, None)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glBindVertexArray(0)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in draw_sky")

    def draw_sprites(self, res, billboards, vp,
                       pal_index: int = 0) -> None:
        """Fill the dynamic VBO (normal billboards grouped by patch,
        fuzz quads grouped by patch in a trailing block) and draw:
        normal sprites first, then the fuzz pass over a copied index
        backdrop (the copy avoids a feedback loop on the attached
        target; one copy for all fuzz groups so overlapping spectres
        sample the same clean backdrop)."""
        import numpy as np
        from OpenGL import GL
        # NOTE: no MAXVISSPRITES cap: the dynamic VBO/IBO resize per
        # frame via glBufferData, every billboard draws depth-tested.
        groups: dict = {}
        fuzz_groups: dict = {}
        for bb in billboards:
            if bb.fuzz:
                fuzz_groups.setdefault(bb.lump, []).append(bb)
            else:
                groups.setdefault(bb.lump, []).append(bb)
        verts = np.zeros((len(billboards) * 4, 6), dtype=np.float32)
        index = np.zeros(len(billboards) * 6, dtype=np.uint32)
        pos = 0
        ranges = []

        def emit(bb) -> None:
            nonlocal pos
            v = pos // 6 * 4
            verts[v + 0] = (bb.left_x, bb.left_y, bb.z_bottom,
                            bb.u0, bb.v0, bb.colormap)
            verts[v + 1] = (bb.right_x, bb.right_y, bb.z_bottom,
                            bb.u1, bb.v0, bb.colormap)
            verts[v + 2] = (bb.right_x, bb.right_y, bb.z_top,
                            bb.u1, bb.v1, bb.colormap)
            verts[v + 3] = (bb.left_x, bb.left_y, bb.z_top,
                            bb.u0, bb.v1, bb.colormap)
            index[pos:pos + 6] = (v, v + 1, v + 2, v, v + 2, v + 3)
            pos += 6

        for lump in sorted(groups):
            start = pos
            for bb in groups[lump]:
                emit(bb)
            ranges.append((lump, start, pos - start))
        fuzz_ranges = []
        if fuzz_groups:
            for lump in sorted(fuzz_groups):
                start = pos
                for bb in fuzz_groups[lump]:
                    emit(bb)
                fuzz_ranges.append((lump, start, pos - start))
        GL.glUseProgram(self.sprite_prog)
        self._pal(self.sprite_prog, pal_index)
        GL.glUniformMatrix4fv(self._loc(self.sprite_prog, "uViewProj"),
                              1, True, vp)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.colormap_tex)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, res.palette_tex)
        GL.glBindVertexArray(self._sprite_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._sprite_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts,
                        GL.GL_DYNAMIC_DRAW)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self._sprite_ibo)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, index.nbytes,
                        index, GL.GL_DYNAMIC_DRAW)
        for lump, start, count in ranges:
            w, h = res.sprite_info[lump]
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D,
                             res.sprite_textures[lump])
            GL.glUniform1f(self._loc(self.sprite_prog, "uWrap"),
                           float(w))
            GL.glUniform1f(self._loc(self.sprite_prog, "uTexH"),
                           float(h))
            GL.glDrawElements(GL.GL_TRIANGLES, count,
                              GL.GL_UNSIGNED_INT,
                              ctypes.c_void_p(start * 4))
        if fuzz_ranges:
            self._copy_fuzz_backdrop()
            for lump, start, count in fuzz_ranges:
                self.draw_fuzz(lump, start, count, vp, pal_index,
                               copy=False)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in draw_sprites")

    def _copy_fuzz_backdrop(self) -> None:
        """Copy the index target to the spare texture (one copy for
        all fuzz groups: sampling the attached target would be a
        feedback loop, and re-copying per group would let later
        spectres sample earlier fuzz)."""
        from OpenGL import GL
        GL.glReadBuffer(GL.GL_COLOR_ATTACHMENT1)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._spare_index)
        GL.glCopyTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, 0, 0,
                               self._w, self._h)
        GL.glReadBuffer(GL.GL_COLOR_ATTACHMENT0)

    def draw_fuzz(self, lump: int, start: int, count: int, vp,
                    pal_index: int = 0, copy: bool = True) -> None:
        """Fuzz quads of one patch over the copied index backdrop.
        Transparent texels discard (software masked posts: only the
        monster shape shimmers). copy=False skips the backdrop copy
        (draw_sprites copies once for all groups)."""
        from OpenGL import GL
        if copy:
            self._copy_fuzz_backdrop()
        GL.glUseProgram(self.fuzz_prog)
        self._pal(self.fuzz_prog, pal_index)
        GL.glUniformMatrix4fv(self._loc(self.fuzz_prog, "uViewProj"),
                              1, True, vp)
        GL.glUniform1i(self._loc(self.fuzz_prog, "uFrame"),
                       int(self._frame))
        GL.glUniform1f(self._loc(self.fuzz_prog, "uViewH"),
                       float(self._h))
        w, h = self._res.sprite_info[int(lump)]
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D,
                         self._res.sprite_textures[int(lump)])
        GL.glUniform1f(self._loc(self.fuzz_prog, "uWrap"), float(w))
        GL.glUniform1f(self._loc(self.fuzz_prog, "uTexH"), float(h))
        GL.glActiveTexture(GL.GL_TEXTURE4)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._spare_index)
        GL.glActiveTexture(GL.GL_TEXTURE5)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._fuzzlut)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._res.colormap_tex)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, self._res.palette_tex)
        GL.glBindVertexArray(self._sprite_vao)
        GL.glDrawElements(GL.GL_TRIANGLES, count,
                          GL.GL_UNSIGNED_INT,
                          ctypes.c_void_p(start * 4))
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in draw_fuzz")

    def draw_psprite(self, tex_id: int, w: int, h: int,
                       leftoff: int, topoff: int, bobx: int,
                       boby: int, pal_index: int = 0) -> None:
        """Weapon sprite overdraw (vanilla draw_psprite anchor, raw
        indices, no depth test). x0/y0 live in 320x200 space and scale
        to native like the software blit."""
        import numpy as np
        from OpenGL import GL
        sx, sy = self._w / 320.0, self._h / 200.0
        x0, y0 = (1 + bobx - leftoff) * sx, (32 + boby - topoff) * sy
        x1, y1 = x0 + w * sx, y0 + h * sy
        verts = np.array([
            x0 / (self._w / 2) - 1.0, 1.0 - y0 / (self._h / 2), 0.0, 0.0,
            x0 / (self._w / 2) - 1.0, 1.0 - y1 / (self._h / 2), 0.0,
            float(h),
            x1 / (self._w / 2) - 1.0, 1.0 - y0 / (self._h / 2),
            float(w), 0.0,
            x1 / (self._w / 2) - 1.0, 1.0 - y1 / (self._h / 2),
            float(w), float(h),
        ], dtype=np.float32)
        GL.glUseProgram(self.psprite_prog)
        self._pal(self.psprite_prog, pal_index)
        GL.glUniform1f(self._loc(self.psprite_prog, "uWrap"),
                       float(w))
        GL.glUniform1f(self._loc(self.psprite_prog, "uTexH"),
                       float(h))
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, self._res.palette_tex)
        GL.glBindVertexArray(self._psprite_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._psprite_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts,
                        GL.GL_DYNAMIC_DRAW)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        GL.glEnable(GL.GL_DEPTH_TEST)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in draw_psprite")

    def blit_world(self) -> None:
        """Copy the FBO world frame to the window (1:1 NEAREST);
        overlay/text/automap draw on top of it afterwards."""
        from OpenGL import GL
        GL.glBindFramebuffer(GL.GL_READ_FRAMEBUFFER, self._fbo)
        GL.glBindFramebuffer(GL.GL_DRAW_FRAMEBUFFER, 0)
        GL.glBlitFramebuffer(0, 0, self._w, self._h,
                             0, 0, self._w, self._h,
                             GL.GL_COLOR_BUFFER_BIT, GL.GL_NEAREST)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in blit_world")

    def present_overlay(self, fb: np.ndarray, pal_index: int = 0,
                        ) -> None:
        """Blit the 320x200 index framebuffer over the world (menu /
        HUD / statusbar / melt art, palette-flashed uniformly).
        Index 255 is reserved transparent (verified unused by all
        overlay art) so sparse art floats over the live world.
        Draws to the WINDOW (default framebuffer), not the FBO."""
        from OpenGL import GL
        assert fb.shape == (SCREENHEIGHT, SCREENWIDTH)
        assert fb.dtype == np.uint8
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glUseProgram(self.overlay_prog)
        self._pal(self.overlay_prog, pal_index)
        GL.glUniform1i(self._loc(self.overlay_prog, "uFbW"),
                       SCREENWIDTH)
        GL.glUniform1i(self._loc(self.overlay_prog, "uFbH"),
                       SCREENHEIGHT)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._overlay_tex)
        # NOTE: pixel-store hygiene (upload_text sets UNPACK_ALIGNMENT
        # for RGBA rows; the overlay needs 1 here regardless of who ran
        # last, or odd-width rows would stride-shift into tiling).
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0, SCREENWIDTH,
                           SCREENHEIGHT, GL.GL_RED, GL.GL_UNSIGNED_BYTE,
                           np.ascontiguousarray(fb))
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, self._res.palette_tex)
        GL.glBindVertexArray(self._overlay_vao)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(GL.GL_FALSE)
        GL.glDrawArrays(GL.GL_TRIANGLES, 0, 3)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glBindVertexArray(0)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in present_overlay")

    def upload_text(self, rgba: bytes, w: int, h: int) -> int:
        """RGBA text image (pygame font surface with baked alpha)."""
        from OpenGL import GL
        assert len(rgba) == w * h * 4
        tex = int(GL.glGenTextures(1))
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MIN_FILTER,
                           GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_MAG_FILTER,
                           GL.GL_LINEAR)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_S,
                           GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D, GL.GL_TEXTURE_WRAP_T,
                           GL.GL_CLAMP_TO_EDGE)
        GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
        GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RGBA8, w, h, 0,
                        GL.GL_RGBA, GL.GL_UNSIGNED_BYTE, rgba)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        self._text_texs.append(tex)
        return tex

    def delete_text(self, tex_id: int) -> None:
        """Delete one upload_text texture (per-frame lines like the
        extra-HUD readout: values change every frame, so nothing is
        cached and the id must not linger in _text_texs)."""
        from OpenGL import GL
        GL.glDeleteTextures(1, [int(tex_id)])
        try:
            self._text_texs.remove(int(tex_id))
        except ValueError:  # NOTE: double delete is a no-op
            pass

    def draw_text_quad(self, tex_id: int, x: int, y: int, w: int,
                       h: int) -> None:
        """Topdown-pixel RGBA quad on the WINDOW (standard alpha
        compositing, like a pygame RGBA blit)."""
        import numpy as np
        from OpenGL import GL
        verts = np.array([
            x / (self._w / 2) - 1.0, 1.0 - y / (self._h / 2), 0.0, 0.0,
            x / (self._w / 2) - 1.0, 1.0 - (y + h) / (self._h / 2),
            0.0, 1.0,
            (x + w) / (self._w / 2) - 1.0, 1.0 - y / (self._h / 2),
            1.0, 0.0,
            (x + w) / (self._w / 2) - 1.0,
            1.0 - (y + h) / (self._h / 2), 1.0, 1.0,
        ], dtype=np.float32)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glUseProgram(self.text_prog)
        GL.glActiveTexture(GL.GL_TEXTURE0)
        GL.glBindTexture(GL.GL_TEXTURE_2D, tex_id)
        GL.glBindVertexArray(self._text_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._text_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts,
                        GL.GL_DYNAMIC_DRAW)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(GL.GL_FALSE)
        GL.glEnable(GL.GL_BLEND)
        GL.glBlendFunc(GL.GL_SRC_ALPHA, GL.GL_ONE_MINUS_SRC_ALPHA)
        GL.glDrawArrays(GL.GL_TRIANGLE_STRIP, 0, 4)
        GL.glDisable(GL.GL_BLEND)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glBindVertexArray(0)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in draw_text_quad")

    def draw_automap(self, segments) -> None:
        """Vector automap lines on the WINDOW (topdown px + 0-255 RGB
        tuples)."""
        import numpy as np
        from OpenGL import GL
        segs = list(segments)
        assert len(segs) * 2 <= 4096  # NOTE: dynamic VAO capacity
        verts = np.zeros((len(segs) * 2, 5), dtype=np.float32)
        for i, (x0, y0, x1, y1, r, g, b) in enumerate(segs):
            verts[i * 2] = (x0 / (self._w / 2) - 1.0,
                            1.0 - y0 / (self._h / 2),
                            r / 255.0, g / 255.0, b / 255.0)
            verts[i * 2 + 1] = (x1 / (self._w / 2) - 1.0,
                                1.0 - y1 / (self._h / 2),
                                r / 255.0, g / 255.0, b / 255.0)
        GL.glUseProgram(self.auto_prog)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glBindVertexArray(self._auto_vao)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self._auto_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, verts.nbytes, verts,
                        GL.GL_DYNAMIC_DRAW)
        GL.glDisable(GL.GL_DEPTH_TEST)
        GL.glDepthMask(GL.GL_FALSE)
        GL.glDrawArrays(GL.GL_LINES, 0, len(segs) * 2)
        GL.glDepthMask(GL.GL_TRUE)
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glBindVertexArray(0)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in draw_automap")

    def clear(self) -> None:
        """Clear the FBO (world frames start here)."""
        from OpenGL import GL
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

    def clear_window(self) -> None:
        """Clear the WINDOW (automap/text-only frames own it)."""
        from OpenGL import GL
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)

    def readback(self) -> np.ndarray:
        """Top-down RGB world frame (FBO color target)."""
        from OpenGL import GL
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        buf = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        GL.glReadPixels(0, 0, self._w, self._h, GL.GL_RGB,
                        GL.GL_UNSIGNED_BYTE, buf)
        return buf[::-1].copy()

    def readback_window(self) -> np.ndarray:
        """Top-down RGB presented frame (window backbuffer: world +
        overlay + text, what flip() shows)."""
        from OpenGL import GL
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, 0)
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        buf = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        GL.glReadPixels(0, 0, self._w, self._h, GL.GL_RGB,
                        GL.GL_UNSIGNED_BYTE, buf)
        GL.glBindFramebuffer(GL.GL_FRAMEBUFFER, self._fbo)
        return buf[::-1].copy()

    def close(self) -> None:
        try:
            from OpenGL import GL
            GL.glDeleteProgram(self.wall_prog)
            GL.glDeleteProgram(self.wall_double_prog)
            GL.glDeleteProgram(self.plane_prog)
            GL.glDeleteProgram(self.sprite_prog)
            GL.glDeleteProgram(self.fuzz_prog)
            GL.glDeleteProgram(self.psprite_prog)
            GL.glDeleteProgram(self.sky_prog)
            GL.glDeleteProgram(self.overlay_prog)
            GL.glDeleteProgram(self.text_prog)
            GL.glDeleteProgram(self.auto_prog)
            GL.glDeleteVertexArrays(4, [self._wall_vao,
                                        self._plane_vao,
                                        self._sprite_vao,
                                        self._sky_vao])
            GL.glDeleteVertexArrays(1, [self._psprite_vao])
            GL.glDeleteVertexArrays(1, [self._overlay_vao])
            GL.glDeleteVertexArrays(1, [self._text_vao])
            GL.glDeleteVertexArrays(1, [self._auto_vao])
            GL.glDeleteBuffers(4, [self._sprite_vbo,
                                   self._sprite_ibo,
                                   self._sky_vbo,
                                   self._sky_ibo])
            GL.glDeleteBuffers(1, [self._psprite_vbo])
            GL.glDeleteBuffers(1, [self._text_vbo])
            GL.glDeleteBuffers(1, [self._auto_vbo])
            GL.glDeleteTextures(1, [self._overlay_tex])
            if self._text_texs:
                GL.glDeleteTextures(len(self._text_texs),
                                    self._text_texs)
            GL.glDeleteTextures(2, [self._scalelight, self._zlight])
            GL.glDeleteTextures(1, [self._fuzzlut])
            GL.glDeleteTextures(3, [self._fb_color, self._fb_index,
                                    self._spare_index])
            GL.glDeleteRenderbuffers(1, [self._fb_depth])
            GL.glDeleteFramebuffers(1, [self._fbo])
        except Exception:  # noqa: BLE001, S110 - teardown never raises
            pass
