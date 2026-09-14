"""Tests for glrender/light.py + upload.py (milestone H, phase 1 tail).

LUT builders and the batch planner are pure logic (headless-safe);
GlResources.create/delete needs a real GL context and skips without
one (headless CI never touches it, local runs verify byte-exact
upload via readback).
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydoom.glrender.light import (
    COLORMAP_BYTES,
    PALETTE_BYTES,
    colormap_lut,
    palette_lut,
    scalelight_lut,
    zlight_lut,
)
from pydoom.glrender.preprocess import build_planes, build_walls
from pydoom.glrender.textures import (
    build_flat_textures,
    build_wall_textures,
    flatnums_used,
    wall_texnums_used,
)
from pydoom.glrender.upload import GlResources, plan_wall_batches
from pydoom.mapdata import Map

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def load_e1m1_sets():
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    sky = texman.flat_num_for_name("F_SKY1")
    walls = build_walls(game_map, texman, sky)
    planes = build_planes(game_map, sky)
    wtex = build_wall_textures(texman, wall_texnums_used(walls))
    ftex = build_flat_textures(texman, flatnums_used(planes))
    cmap = colormap_lut(bytes(wad.cache_lump("COLORMAP")))
    pal = palette_lut(bytes(wad.read_lump("PLAYPAL")))
    return texman, walls, planes, wtex, ftex, cmap, pal


def test_upload_module_needs_no_gl():
    """Importing upload + planning batches imports no GL bindings."""
    before = set(sys.modules)
    plan_wall_batches([])
    new_gl = {m.split(".")[0] for m in set(sys.modules) - before}
    assert not (new_gl & {"OpenGL", "OpenGL_accelerate", "moderngl"})


def test_colormap_lut_is_first_32_maps():
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    raw = bytes(wad.cache_lump("COLORMAP"))
    assert len(colormap_lut(raw)) == COLORMAP_BYTES == 32 * 256
    assert colormap_lut(raw) == raw[:COLORMAP_BYTES]
    with pytest.raises(ValueError):
        colormap_lut(b"\x00" * 100)


def test_palette_lut_is_playpal_0():
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    raw = bytes(wad.read_lump("PLAYPAL"))
    assert len(palette_lut(raw)) == PALETTE_BYTES == 768
    assert palette_lut(raw) == raw[:PALETTE_BYTES]
    with pytest.raises(ValueError):
        palette_lut(raw, 99)


def test_light_luts_match_renderer_tables():
    """scalelight/zlight bytes equal the software tables exactly
    (the shader indexes the same rows/cols as _render_seg_loop and
    _map_plane)."""
    from pydoom.renderer import Renderer
    from pydoom.textures import TextureManager
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    texman = TextureManager(wad)
    game_map = Map.from_wad(wad, "E1M1")
    texman.resolve_map(game_map)
    renderer = Renderer(wad, texman)
    assert list(scalelight_lut()) == [
        v for row in renderer.scalelight for v in row]
    assert list(zlight_lut()) == [
        v for row in renderer.zlight for v in row]
    assert len(scalelight_lut()) == 48 * 16
    assert len(zlight_lut()) == 128 * 16


@requires_wad
def test_plan_wall_batches_cover_and_group():
    _texman, walls, _planes, wtex, _ftex, _cmap, _pal = load_e1m1_sets()
    index, batches, masked = plan_wall_batches(walls.quads)
    opaque_quads = [q for q in walls.quads if q.tier != "masked"]
    masked_quads = [q for q in walls.quads if q.tier == "masked"]
    assert len(index) == len(walls.quads) * 6
    # NOTE: every quad exactly once, in its own list; each batch one
    # texnum with valid triangles.
    seen = []
    for texnum, start, count in list(batches) + list(masked):
        assert count % 6 == 0 and count > 0
        for k in range(start, start + count, 6):
            q = int(index[k]) // 4
            assert walls.quads[q].texnum == texnum
            assert (int(index[k]), int(index[k + 1]),
                    int(index[k + 2]), int(index[k + 3]),
                    int(index[k + 4]), int(index[k + 5])) == (
                        4 * q, 4 * q + 1, 4 * q + 2,
                        4 * q, 4 * q + 2, 4 * q + 3)
            seen.append(q)
    assert sorted(seen) == list(range(len(walls.quads)))
    assert [b[0] for b in batches] == sorted(
        {q.texnum for q in opaque_quads})  # NOTE: deterministic order
    assert [b[0] for b in masked] == sorted(
        {q.texnum for q in masked_quads})
    # NOTE: one texnum may serve both lists (E1M1: 17 does); the
    # union covers every referenced texture exactly once in the set.
    assert ({b[0] for b in batches} | {b[0] for b in masked}
            == set(wtex.order))
    assert len(wtex.order) == 32  # E1M1 golden


def _gl_context():
    """Tiny local GL window (or skip): headless CI has no display."""
    import pygame
    pygame.init()
    try:
        pygame.display.set_mode((64, 64),
                                pygame.OPENGL | pygame.DOUBLEBUF)
    except Exception:  # noqa: BLE001 - any display failure skips
        pygame.quit()
        pytest.skip("no GL context")
        return None
    try:
        from OpenGL import GL
        ver = GL.glGetString(GL.GL_VERSION)
        if not ver or int(ver.split(b".")[0]) < 3:
            raise ValueError("need GL 3+")
    except Exception:  # noqa: BLE001 - any GL failure skips
        pygame.quit()
        pytest.skip("no GL 3+ context")
        return None
    return True


@requires_wad
def test_create_uploads_byte_exact():
    """Real context: buffers/textures exist and read back the exact
    bytes the CPU assembled (local only, skipped headless)."""
    if _gl_context() is None:
        return
    try:
        import numpy as np
        from OpenGL import GL
        _texman, walls, planes, wtex, ftex, cmap, pal = load_e1m1_sets()
        res = GlResources.create(walls, planes, wtex, ftex, cmap,
                                 pal)
        assert res is not None
        assert res.wall_vbo and res.wall_ibo and res.plane_vbo
        assert res.colormap_tex and res.palette_tex
        assert res.flat_array and res.plane_count > 0
        assert len(res.wall_textures) == len(wtex.order)
        assert res.flat_layers == ftex.index_of
        # NOTE: VBO byte sizes match the interleaved layouts.
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, res.wall_vbo)
        size = GL.glGetBufferParameteriv(GL.GL_ARRAY_BUFFER,
                                         GL.GL_BUFFER_SIZE)
        assert size == len(walls.quads) * 4 * 8 * 4
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, res.plane_vbo)
        size = GL.glGetBufferParameteriv(GL.GL_ARRAY_BUFFER,
                                         GL.GL_BUFFER_SIZE)
        assert size == res.plane_count * 7 * 4
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        assert res.wall_info == {
            t: (wtex.sizes[i][0], wtex.sizes[i][1], wtex.wraps[i])
            for i, t in enumerate(wtex.order)}
        # NOTE: texture readback equals the assembled blobs (the
        # wall RG texture needs an explicit output array: PyOpenGL
        # 3.1.10 has no GL_RG entry in its format table).
        texnum = wtex.order[0]
        GL.glBindTexture(GL.GL_TEXTURE_2D,
                         res.wall_textures[texnum])
        w, h = wtex.sizes[0]
        out = np.zeros((h, w, 2), dtype=np.uint8)
        GL.glGetTexImage(GL.GL_TEXTURE_2D, 0, GL.GL_RG,
                         GL.GL_UNSIGNED_BYTE, out)
        assert out.tobytes() == wtex.blobs[0]
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.colormap_tex)
        back = GL.glGetTexImage(GL.GL_TEXTURE_2D, 0, GL.GL_RED,
                                GL.GL_UNSIGNED_BYTE)
        assert bytes(back) == cmap
        GL.glBindTexture(GL.GL_TEXTURE_2D, res.palette_tex)
        back = GL.glGetTexImage(GL.GL_TEXTURE_2D, 0, GL.GL_RGB,
                                GL.GL_UNSIGNED_BYTE)
        assert bytes(back) == pal
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        res.delete()
        assert res.wall_vbo == 0 and res.wall_textures == {}
    finally:
        import pygame
        pygame.quit()
