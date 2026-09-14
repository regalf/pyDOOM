"""GL resource upload (milestone H, phase 1, upload slice).

Moves the CPU-side preprocess output to the GPU: wall/plane VBOs
(+ wall IBO reordered by texture), per-texture wall images, the
stacked flat array, and the colormap/palette LUTs. No drawing yet
(shaders land in Phase 2); this proves the context path end to end
and pins GPU byte-exactness via readback tests.

Module import never touches GL (OpenGL imports live inside
create()): headless CI and the software path stay import-clean.
Every GL failure returns None instead of raising out of the viewer.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

__all__ = ["GlResources", "plan_wall_batches"]


def plan_wall_batches(quads) -> tuple:
    """Reorder wall indices by texture (deterministic: texnums sorted).

    Returns (index uint32 array, opaque batches, masked batches) with
    [(texnum, start, count)] each: opaque tiers draw in the opaque
    pass, masked mids in the transparent pass (same VBO/IBO, alpha
    tested, depth written like opaque since Doom has no
    translucency). Every quad lands in exactly one list.
    """
    opaque: dict = {}
    masked: dict = {}
    for q, quad in enumerate(quads):
        target = masked if quad.tier == "masked" else opaque
        target.setdefault(quad.texnum, []).append(q)

    def emit(groups: dict):
        index = np.zeros(sum(len(v) for v in groups.values()) * 6,
                         dtype=np.uint32)
        batches = []
        pos = 0
        for texnum in sorted(groups):
            start = pos
            for q in groups[texnum]:
                b = q * 4
                index[pos:pos + 6] = (b, b + 1, b + 2,
                                      b, b + 2, b + 3)
                pos += 6
            batches.append((texnum, start, pos - start))
        return index, batches

    o_index, o_batches = emit(opaque)
    m_index, m_batches = emit(masked)
    return np.concatenate((o_index, m_index)), o_batches, [
        (t, s + len(o_index), c) for t, s, c in m_batches]


@dataclass
class GlResources:
    """Live GL objects for one map (context must be current)."""

    wall_vbo: int = 0
    wall_ibo: int = 0
    wall_batches: list = field(default_factory=list)
    masked_batches: list = field(default_factory=list)
    wall_textures: dict = field(default_factory=dict)  # texnum -> id
    wall_info: dict = field(default_factory=dict)  # texnum -> (w,h,wrap)
    plane_vbo: int = 0
    plane_count: int = 0
    flat_array: int = 0
    flat_layers: dict = field(default_factory=dict)  # flatnum -> layer
    sprite_textures: dict = field(default_factory=dict)  # sprnum -> id
    sprite_info: dict = field(default_factory=dict)  # sprnum -> (w,h)
    colormap_tex: int = 0
    palette_tex: int = 0
    sector_tex: int = 0  # R8 W=numsectors x1: base lightnum per sector
    # (walls/planes sample it; flicker/strobe/movers re-upload only
    # this, never the geometry VBOs, for light changes)
    sector_count: int = 0

    @classmethod
    def create(cls, wall_geo, plane_geo, wall_tex, flat_tex,
               colormap: bytes, playpal: bytes, sprite_tex=None,
               sector_lights=None):
        """Upload everything; None (with best-effort cleanup) on any
        GL failure. wall_tex/flat_tex are WallTextureSet /
        FlatTextureSet; sprite_tex an optional SpriteTextureSet;
        colormap the light.py LUT bytes; playpal the FULL PLAYPAL
        lump (14 palettes: damage/bonus flashes ride uPalIndex);
        sector_lights the dynamic.sector_light_bases list (None
        uploads one dark texel: tests that never draw lit pixels)."""
        from OpenGL import GL
        created = cls()
        try:
            GL.glPixelStorei(GL.GL_UNPACK_ALIGNMENT, 1)
            cls._upload_walls(created, wall_geo, wall_tex)
            cls._upload_planes(created, plane_geo, flat_tex)
            if sprite_tex is not None:
                cls._upload_sprites(created, sprite_tex)
            created.colormap_tex = cls._upload_lut(
                colormap, 256, 32, GL.GL_R8, GL.GL_RED)
            created.palette_tex = cls._upload_palette(playpal)
            created.upload_sector_lights(sector_lights or [0])
        except Exception:  # noqa: BLE001 - any GL failure falls back
            created.delete()
            return None
        return created

    @staticmethod
    def _new_buffer(data: np.ndarray, target: int) -> int:
        from OpenGL import GL
        buf = GL.glGenBuffers(1)
        GL.glBindBuffer(target, buf)
        GL.glBufferData(target, data.nbytes, data, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(target, 0)
        return int(buf)

    @staticmethod
    def _wall_interleaved(wall_geo) -> np.ndarray:
        """Wall VBO rows: [x,y,z, u,texbase, sector,tweak01, nx,ny].

        V is texbase - z in the vertex shader (world-pinned, no view
        math needed); sector indexes the sector-light texture and
        tweak01 (0/1/2) folds the vanilla orient tweak into the
        shader row (double-clamped exactly like the old baked
        formula: clamp(base + tweak) then + extralight)."""
        arr = wall_geo.to_arrays()
        n = len(arr["positions"])
        inter = np.zeros((n, 9), dtype=np.float32)
        inter[:, 0:3] = arr["positions"]
        inter[:, 3] = arr["u"]
        inter[:, 4] = arr["texbase"]
        inter[:, 5] = arr["sector"]
        inter[:, 6] = arr["tweak"]
        inter[:, 7:9] = arr["normal"]
        return np.ascontiguousarray(inter)

    @staticmethod
    def _plane_interleaved(plane_geo, flat_layers: dict) -> tuple:
        """Plane VBO rows ([x,y,z, u,v, layer, sector]) minus sky tris
        (flat -1: the sky surface draws them; every tri is uniform so
        vertex filtering is safe). flat_layers maps flatnum -> array
        layer (all flats are prebuilt, so donut pic swaps resolve)."""
        arr = plane_geo.to_arrays()
        # NOTE: sector channel holds sector IDXs; the array wants no
        # remap (unlike flatnums, which map to LAYERs below).
        flats = arr["flat"].astype(np.int32)
        layers = np.array([flat_layers.get(int(f), -1)
                           for f in flats], dtype=np.int32)
        assert ((layers[0::3] == layers[1::3])
                & (layers[1::3] == layers[2::3])).all()
        keep = layers >= 0
        n = int(keep.sum())
        inter = np.zeros((n, 7), dtype=np.float32)
        inter[:, 0:3] = arr["positions"][keep]
        inter[:, 3:5] = arr["uv"][keep]
        inter[:, 5] = layers[keep].astype(np.float32)
        inter[:, 6] = arr["sector"][keep]
        return np.ascontiguousarray(inter), n

    @classmethod
    def _upload_walls(cls, created, wall_geo, wall_tex) -> None:
        from OpenGL import GL
        inter = cls._wall_interleaved(wall_geo)
        created.wall_vbo = cls._new_buffer(inter,
                                           GL.GL_ARRAY_BUFFER)
        index, batches, masked = plan_wall_batches(wall_geo.quads)
        created.wall_ibo = cls._new_buffer(index,
                                           GL.GL_ELEMENT_ARRAY_BUFFER)
        created.wall_batches = batches
        created.masked_batches = masked
        created.upload_wall_textures(wall_tex)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    def upload_wall_textures(self, wall_tex) -> None:
        """Upload WallTextureSet entries missing from wall_textures.

        Used once at create (via _upload_walls) and on GEO refreshes
        for tiers that were degenerate at load (closed-door masked
        mids): their texnums were never built, but movers can make
        the tier appear mid-game. Switch-pair textures are prebuilt
        at load instead (dynamic.switch_pair_texnums)."""
        from OpenGL import GL
        for texnum, blob, size, wrap in zip(
                wall_tex.order, wall_tex.blobs, wall_tex.sizes,
                wall_tex.wraps):
            if texnum in self.wall_textures:
                continue
            w, h = size
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RG8, w, h, 0,
                            GL.GL_RG, GL.GL_UNSIGNED_BYTE, blob)
            self.wall_textures[texnum] = int(tex)
            self.wall_info[texnum] = (w, h, wrap)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    @classmethod
    def _upload_planes(cls, created, plane_geo, flat_tex) -> None:
        from OpenGL import GL
        inter, n = cls._plane_interleaved(plane_geo,
                                          flat_tex.index_of)
        created.plane_vbo = cls._new_buffer(inter,
                                            GL.GL_ARRAY_BUFFER)
        created.plane_count = n
        layers = len(flat_tex.order)
        if layers:
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                               GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                               GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                               GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                               GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
            GL.glTexImage3D(GL.GL_TEXTURE_2D_ARRAY, 0, GL.GL_R8,
                            64, 64, layers, 0,
                            GL.GL_RED, GL.GL_UNSIGNED_BYTE,
                            flat_tex.blob)
            GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, 0)
            created.flat_array = int(tex)
            created.flat_layers = dict(flat_tex.index_of)

    def upload_sector_lights(self, levels) -> None:
        """(Re)upload base lightnums (R8 W=numsectors x1; values 0..15
        exact in a byte). Called at create and on every LIGHT/GEO
        refresh (flicker/strobe/movers); tiny (sectors << VBOs)."""
        from OpenGL import GL
        data = bytes(min(max(int(v), 0), 15) for v in levels)
        assert data, "map has no sectors"
        if not self.sector_tex:
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_R8,
                            len(data), 1, 0,
                            GL.GL_RED, GL.GL_UNSIGNED_BYTE, data)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
            self.sector_tex = int(tex)
            self.sector_count = len(data)
        else:
            assert len(data) == self.sector_count, (
                len(data), self.sector_count)
            GL.glBindTexture(GL.GL_TEXTURE_2D, self.sector_tex)
            GL.glTexSubImage2D(GL.GL_TEXTURE_2D, 0, 0, 0,
                               len(data), 1,
                               GL.GL_RED, GL.GL_UNSIGNED_BYTE, data)
            GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    def reupload_walls(self, wall_geo) -> None:
        """GEO refresh: orphan + refill the wall VBO/IBO in place (same
        buffer ids, so the wall VAO stays valid) and re-plan batches
        (movers change tier existence). Raises on GL error (the viewer
        falls back to software for the frame)."""
        from OpenGL import GL
        inter = self._wall_interleaved(wall_geo)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.wall_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, inter.nbytes, inter,
                        GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        index, batches, masked = plan_wall_batches(wall_geo.quads)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, self.wall_ibo)
        GL.glBufferData(GL.GL_ELEMENT_ARRAY_BUFFER, index.nbytes,
                        index, GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ELEMENT_ARRAY_BUFFER, 0)
        self.wall_batches = batches
        self.masked_batches = masked

    def reupload_planes(self, plane_geo, flat_layers: dict) -> None:
        """GEO refresh: orphan + refill the plane VBO in place (same
        id, VAO stays valid). flat_layers is the stable flatnum ->
        layer map from load (all flats prebuilt: donut pic swaps and
        sky-tag flips resolve without new textures)."""
        from OpenGL import GL
        inter, n = self._plane_interleaved(plane_geo, flat_layers)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, self.plane_vbo)
        GL.glBufferData(GL.GL_ARRAY_BUFFER, inter.nbytes, inter,
                        GL.GL_STATIC_DRAW)
        GL.glBindBuffer(GL.GL_ARRAY_BUFFER, 0)
        self.plane_count = n

    @classmethod
    def _upload_sprites(cls, created, sprite_tex) -> None:
        """One RG8 texture per sprite patch (same params as walls)."""
        from OpenGL import GL
        for spritenum, blob, (w, h) in zip(sprite_tex.order,
                                           sprite_tex.blobs,
                                           sprite_tex.sizes):
            tex = GL.glGenTextures(1)
            GL.glBindTexture(GL.GL_TEXTURE_2D, tex)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_WRAP_S, GL.GL_REPEAT)
            GL.glTexParameteri(GL.GL_TEXTURE_2D,
                               GL.GL_TEXTURE_WRAP_T, GL.GL_REPEAT)
            GL.glTexImage2D(GL.GL_TEXTURE_2D, 0, GL.GL_RG8, w, h, 0,
                            GL.GL_RG, GL.GL_UNSIGNED_BYTE, blob)
            created.sprite_textures[spritenum] = int(tex)
            created.sprite_info[spritenum] = (w, h)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)

    @staticmethod
    def _upload_palette(playpal: bytes) -> int:
        """Full PLAYPAL lump as a 256x14 RGB8 array (one layer per
        flash slot; uPalIndex selects, base palette is layer 0)."""
        from OpenGL import GL
        assert len(playpal) >= 14 * 768, len(playpal)
        tex = GL.glGenTextures(1)
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, tex)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                           GL.GL_TEXTURE_MIN_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                           GL.GL_TEXTURE_MAG_FILTER, GL.GL_NEAREST)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                           GL.GL_TEXTURE_WRAP_S, GL.GL_CLAMP_TO_EDGE)
        GL.glTexParameteri(GL.GL_TEXTURE_2D_ARRAY,
                           GL.GL_TEXTURE_WRAP_T, GL.GL_CLAMP_TO_EDGE)
        GL.glTexImage3D(GL.GL_TEXTURE_2D_ARRAY, 0, GL.GL_RGB8,
                        256, 1, 14, 0,
                        GL.GL_RGB, GL.GL_UNSIGNED_BYTE,
                        bytes(playpal[:14 * 768]))
        GL.glBindTexture(GL.GL_TEXTURE_2D_ARRAY, 0)
        return int(tex)

    @staticmethod
    def _upload_lut(blob: bytes, w: int, h: int, internal: int,
                    fmt: int) -> int:
        from OpenGL import GL
        tex = GL.glGenTextures(1)
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
                        GL.GL_UNSIGNED_BYTE, blob)
        GL.glBindTexture(GL.GL_TEXTURE_2D, 0)
        return int(tex)

    def delete(self) -> None:
        """Free everything (best-effort: a dead context must not
        raise out of level transitions)."""
        try:
            from OpenGL import GL
            ids = [self.wall_vbo, self.wall_ibo, self.plane_vbo]
            GL.glDeleteBuffers(3, [i for i in ids if i])
            tids = (list(self.wall_textures.values())
                    + list(self.sprite_textures.values())
                    + [self.flat_array, self.colormap_tex,
                        self.palette_tex, self.sector_tex])
            tids = [t for t in tids if t]
            if tids:
                GL.glDeleteTextures(len(tids), tids)
        except Exception:  # noqa: BLE001, S110 - dead context frees
            pass  # nothing (teardown must never raise either)
        finally:
            self.wall_vbo = self.wall_ibo = self.plane_vbo = 0
            self.wall_batches = []
            self.masked_batches = []
            self.wall_textures = {}
            self.sprite_textures = {}
            self.sprite_info = {}
            self.wall_info = {}
            self.plane_count = 0
            self.flat_array = 0
            self.flat_layers = {}
            self.colormap_tex = self.palette_tex = 0
            self.sector_tex = 0
            self.sector_count = 0
