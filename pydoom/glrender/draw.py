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

import numpy as np

from pydoom import tables
from pydoom.fixed import FRACBITS, fixed_div
from pydoom.glrender import shaders
from pydoom.glrender.light import scalelight_lut, zlight_lut
from pydoom.glrender.sky import SKY_SEGS, sky_index
from pydoom.renderer import (
    FIELDOFVIEW,
    MAXVISSPRITES,
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
                 w: int, h: int) -> tuple:
    """ViewProj matrix + view basis (float64 math, float32 upload).

    x_ndc follows the vanilla x mapping (tan-based, x-focal above),
    y_ndc the vanilla y mapping (square pixels: y-focal 160 at
    320x200, i.e. proj11 = w/h for any window of the same aspect).
    Camera looks along +dir with up +z, from the fine tables.
    """
    dx = tables.finecosine(angle_bam >> 19) / 65536.0
    dy = tables.finesine[angle_bam >> 19] / 65536.0
    rx, ry = dy, -dx  # NOTE: screen-right (d cross up)
    ex, ey, ez = viewx / 65536.0, viewy / 65536.0, viewz / 65536.0
    view = np.array([
        [rx, ry, 0.0, -(rx * ex + ry * ey)],
        [0.0, 0.0, 1.0, -ez],
        [-dx, -dy, 0.0, dx * ex + dy * ey],
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
        self.plane_prog = shaders.compile_program(shaders.PLANE_VERT,
                                                  shaders.PLANE_FRAG)
        self.sprite_prog = shaders.compile_program(
            shaders.SPRITE_VERT, shaders.SPRITE_FRAG)
        self.sky_prog = shaders.compile_program(shaders.SKY_VERT,
                                                shaders.SKY_FRAG)
        self._scalelight = res._upload_lut(scalelight_lut(), 48, 16,
                                           GL.GL_R8, GL.GL_RED)
        self._zlight = res._upload_lut(zlight_lut(), 128, 16,
                                       GL.GL_R8, GL.GL_RED)
        for prog, samplers in (
                (self.wall_prog, (("uWallTex", 0), ("uScaleLight", 1),
                                  ("uColormap", 2), ("uPalette", 3))),
                (self.plane_prog, (("uFlatArray", 0), ("uZLight", 1),
                                   ("uColormap", 2), ("uPalette", 3))),
                (self.sprite_prog, (("uSpriteTex", 0),
                                    ("uColormap", 2),
                                    ("uPalette", 3))),
                (self.sky_prog, (("uSkyTex", 0),
                                 ("uColormap", 2),
                                 ("uPalette", 3)))):
            GL.glUseProgram(prog)
            for name, unit in samplers:
                GL.glUniform1i(self._loc(prog, name), unit)
        GL.glUseProgram(0)
        self._wall_vao = self._make_vao(
            res.wall_vbo, 8,
            [(0, 3, 0), (1, 1, 3), (2, 1, 4), (3, 1, 5), (4, 2, 6)],
            res.wall_ibo)
        self._plane_vao = self._make_vao(
            res.plane_vbo, 7,
            [(0, 3, 0), (1, 2, 3), (2, 1, 5), (3, 1, 6)], 0)
        # NOTE: sprite VBO/IBO are refilled per frame (dynamic
        # billboards); sized for MAXVISSPRITES quads like the
        # software vissprite cap.
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
        GL.glDisable(GL.GL_DITHER)  # NOTE: LSB-exact readback parity
        GL.glEnable(GL.GL_DEPTH_TEST)
        GL.glDepthFunc(GL.GL_LESS)
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

    def render(self, viewx: int, viewy: int, viewz: int,
               angle_bam: int, extra_light: int = 0,
               fullbright: bool = False, sprites=None,
               sky=None) -> None:
        """Draw sky (optional) + walls + planes (+ optional sprite
        billboards) for one camera (raises on GL error: silent
        corruption is worse than a loud test failure). Sprites draw
        last, depth-tested with depth writes on like everything else
        (Doom has no translucency, so order is irrelevant). sky is a
        (texture_id, tex_height) tuple or None."""
        from OpenGL import GL
        res = self._res
        vp, (dx, dy) = camera_frame(viewx, viewy, viewz, angle_bam,
                                    self._w, self._h)
        GL.glClearColor(0.0, 0.0, 0.0, 1.0)
        GL.glClear(GL.GL_COLOR_BUFFER_BIT | GL.GL_DEPTH_BUFFER_BIT)
        if sky is not None:
            self.draw_sky(viewx, viewy, viewz, vp, *sky)
        GL.glUseProgram(self.wall_prog)
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
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.palette_tex)
        GL.glBindVertexArray(self._wall_vao)
        for texnum, start, count in res.wall_batches:
            _w, h, wrap = res.wall_info[texnum]
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D,
                             res.wall_textures[texnum])
            GL.glUniform1f(self._loc(self.wall_prog, "uWrap"),
                           float(wrap))
            GL.glUniform1f(self._loc(self.wall_prog, "uTexH"),
                           float(h))
            GL.glDrawElements(GL.GL_TRIANGLES, count,
                              GL.GL_UNSIGNED_INT,
                              ctypes.c_void_p(start * 4))
        # NOTE: masked mids ride the same program/VBO (alpha-tested
        # holes, depth written like opaque: Doom has no translucency,
        # so draw order among depth writers is irrelevant).
        for texnum, start, count in res.masked_batches:
            _w, h, wrap = res.wall_info[texnum]
            GL.glActiveTexture(GL.GL_TEXTURE0)
            GL.glBindTexture(GL.GL_TEXTURE_2D,
                             res.wall_textures[texnum])
            GL.glUniform1f(self._loc(self.wall_prog, "uWrap"),
                           float(wrap))
            GL.glUniform1f(self._loc(self.wall_prog, "uTexH"),
                           float(h))
            GL.glDrawElements(GL.GL_TRIANGLES, count,
                              GL.GL_UNSIGNED_INT,
                              ctypes.c_void_p(start * 4))
        GL.glUseProgram(self.plane_prog)
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
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.palette_tex)
        GL.glBindVertexArray(self._plane_vao)
        if res.plane_count:
            GL.glDrawArrays(GL.GL_TRIANGLES, 0, res.plane_count)
        if sprites:
            self.draw_sprites(res, sprites, vp)
        GL.glBindVertexArray(0)
        GL.glUseProgram(0)
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in render")

    def draw_sky(self, viewx: int, viewy: int, viewz: int, vp,
                 tex_id: int, tex_h: int) -> None:
        """Sky cylinder first (no depth write: walls overdraw it)."""
        from OpenGL import GL

        from pydoom.glrender.sky import build_sky_verts
        verts = build_sky_verts(viewx, viewy, viewz)
        GL.glUseProgram(self.sky_prog)
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
        GL.glBindTexture(GL.GL_TEXTURE_2D, self._res.palette_tex)
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

    def draw_sprites(self, res, billboards, vp) -> None:
        """Fill the dynamic VBO with billboards grouped by patch and
        draw them (depth-tested, depth written: order-free)."""
        import numpy as np
        from OpenGL import GL
        assert len(billboards) <= MAXVISSPRITES
        groups: dict = {}
        for bb in billboards:
            groups.setdefault(bb.lump, []).append(bb)
        verts = np.zeros((len(billboards) * 4, 6), dtype=np.float32)
        index = np.zeros(len(billboards) * 6, dtype=np.uint32)
        pos = 0
        ranges = []
        for lump in sorted(groups):
            start = pos
            for bb in groups[lump]:
                v = pos // 6 * 4
                verts[v + 0] = (bb.left_x, bb.left_y, bb.z_bottom,
                                bb.u0, bb.v0, bb.colormap)
                verts[v + 1] = (bb.right_x, bb.right_y, bb.z_bottom,
                                bb.u1, bb.v0, bb.colormap)
                verts[v + 2] = (bb.right_x, bb.right_y, bb.z_top,
                                bb.u1, bb.v1, bb.colormap)
                verts[v + 3] = (bb.left_x, bb.left_y, bb.z_top,
                                bb.u0, bb.v1, bb.colormap)
                index[pos:pos + 6] = (v, v + 1, v + 2,
                                      v, v + 2, v + 3)
                pos += 6
            ranges.append((lump, start, pos - start))
        GL.glUseProgram(self.sprite_prog)
        GL.glUniformMatrix4fv(self._loc(self.sprite_prog, "uViewProj"),
                              1, True, vp)
        GL.glActiveTexture(GL.GL_TEXTURE2)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.colormap_tex)
        GL.glActiveTexture(GL.GL_TEXTURE3)
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.palette_tex)
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
        err = GL.glGetError()
        if err != GL.GL_NO_ERROR:
            raise RuntimeError(f"GL error {err:#x} in draw_sprites")

    def readback(self) -> np.ndarray:
        """Top-down RGB framebuffer (software-fb layout)."""
        from OpenGL import GL
        GL.glPixelStorei(GL.GL_PACK_ALIGNMENT, 1)
        buf = np.zeros((self._h, self._w, 3), dtype=np.uint8)
        GL.glReadPixels(0, 0, self._w, self._h, GL.GL_RGB,
                        GL.GL_UNSIGNED_BYTE, buf)
        return buf[::-1].copy()

    def close(self) -> None:
        try:
            from OpenGL import GL
            GL.glDeleteProgram(self.wall_prog)
            GL.glDeleteProgram(self.plane_prog)
            GL.glDeleteProgram(self.sprite_prog)
            GL.glDeleteProgram(self.sky_prog)
            GL.glDeleteVertexArrays(4, [self._wall_vao,
                                        self._plane_vao,
                                        self._sprite_vao,
                                        self._sky_vao])
            GL.glDeleteBuffers(3, [self._sprite_vbo,
                                   self._sprite_ibo,
                                   self._sky_vbo])
            GL.glDeleteBuffers(1, [self._sky_ibo])
            GL.glDeleteTextures(2, [self._scalelight, self._zlight])
        except Exception:  # noqa: BLE001, S110 - teardown never raises
            pass
