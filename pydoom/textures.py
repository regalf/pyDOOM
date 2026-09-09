"""Wall textures, patches and flats (port of r_data.c texture/flat parts).

Covers R_InitTextures, R_GenerateLookup, R_GenerateComposite,
R_GetColumn, R_InitFlats, R_FlatNumForName, R_CheckTextureNumForName,
R_TextureNumForName and R_InitSpriteLumps (header info only).

Data representations:

* A patch lump is decoded into a :class:`Patch`: header dims plus, per
  column, the list of posts ``(topdelta, pixels)`` exactly as stored.
* :meth:`TextureManager.get_column` returns ``(pixels, mask)``: full
  texture-height bytes plus a 1=opaque mask. This merges what the C
  code spreads across direct lump pointers (single-patch columns) and
  the composite cache (multi-patch columns).

Documented deviations from the C rendering path:

* Single-patch columns that are transparent in places are returned
  decoded (gaps masked out). The C ``R_DrawColumn`` instead reads the
  raw lump bytes including post headers as pixels in that case (a
  vanilla glitch); output is identical for well-formed opaque columns.
* No zone memory: decoded patches and composited textures are cached
  in plain dicts. ``PU_CACHE`` purging does not exist yet.
* Tall patches (topdelta == 0xFF continuation, a later Boom invention)
  are not supported, matching linuxdoom-1.10 which terminates columns
  at the first 0xFF.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

from pydoom.fixed import FRACBITS
from pydoom.mapdata import Map
from pydoom.wad import WadFile

__all__ = [
    "TextureError",
    "Patch",
    "Texture",
    "TextureManager",
]

_PATCH_HDR = struct.Struct("<hhhh")  # width, height, leftoffset, topoffset
_COL_OFS = struct.Struct("<i")
_PNAMES_COUNT = struct.Struct("<i")
_TEX_COUNT = struct.Struct("<i")
_TEX_OFS = struct.Struct("<i")
# maptexture_t header: name[8], masked(int), width, height, columndir(int),
# patchcount. mappatch_t: originx, originy, patch, stepdir, colormap.
_MTEX_HDR = struct.Struct("<8sihhih")
_MTEX_PATCH = struct.Struct("<hhhhh")

FLAT_SIZE = 64 * 64
NO_TEXTURE = "-"


class TextureError(Exception):
    """Equivalent of the R_* I_Error aborts (missing patch/texture/flat)."""


@dataclass(eq=False)
class Patch:
    """A decoded patch/sprite lump (patch_t + post lists)."""

    width: int = 0
    height: int = 0
    leftoffset: int = 0
    topoffset: int = 0
    # columns[x] = list of (topdelta, pixels) posts, top to bottom.
    columns: list[list[tuple[int, bytes]]] = field(default_factory=list)

    def column_pixels(self, x: int) -> tuple[bytes, bytes]:
        """Full-height (pixels, opaque-mask) for one patch column."""
        canvas = bytearray(self.height)
        mask = bytearray(self.height)
        for topdelta, pixels in self.columns[x]:
            for i, px in enumerate(pixels):
                y = topdelta + i
                if 0 <= y < self.height:
                    canvas[y] = px
                    mask[y] = 1
        return bytes(canvas), bytes(mask)


def decode_patch(data: bytes) -> Patch:
    """Decode a patch lump (also used for sprites) into a :class:`Patch`."""
    if len(data) < _PATCH_HDR.size:
        raise TextureError(f"patch lump too short: {len(data)} bytes")
    width, height, left, top = _PATCH_HDR.unpack_from(data, 0)
    if width <= 0 or height <= 0:
        raise TextureError(f"bad patch dims {width}x{height}")
    columns: list[list[tuple[int, bytes]]] = []
    for x in range(width):
        ofs = _COL_OFS.unpack_from(data, _PATCH_HDR.size + x * 4)[0]
        posts: list[tuple[int, bytes]] = []
        while True:
            if ofs + 1 > len(data):
                raise TextureError("patch column runs past lump end")
            topdelta = data[ofs]
            if topdelta == 0xFF:
                break
            if ofs + 3 > len(data):
                raise TextureError("patch post header runs past lump end")
            length = data[ofs + 1]
            # Layout: topdelta, length, pad, pixels[length], pad.
            pixels = data[ofs + 3 : ofs + 3 + length]
            if len(pixels) != length:
                raise TextureError("patch post runs past lump end")
            posts.append((topdelta, bytes(pixels)))
            ofs += length + 4
        columns.append(posts)
    return Patch(
        width=width, height=height, leftoffset=left, topoffset=top,
        columns=columns,
    )


@dataclass(eq=False)
class Texture:
    """A composited wall texture (texture_t): patch list in draw order."""

    name: str = ""
    width: int = 0
    height: int = 0
    patches: list[tuple[int, int, int]] = field(default_factory=list)
    # (originx, originy, patch lump index)
    widthmask: int = 0
    # Per-column cache, filled on first get_column: (pixels, mask).
    _columns: list[tuple[bytes, bytes] | None] | None = field(
        default=None, repr=False, compare=False
    )


class TextureManager:
    """Owns PNAMES, TEXTURE1/2, flats and sprite lump ranges (R_InitData)."""

    def __init__(self, wad: WadFile) -> None:
        self.wad = wad
        self.patch_lumps = self._load_pnames(wad)
        self.textures = self._load_textures(wad)
        self.by_name = {t.name: i for i, t in enumerate(self.textures)}
        # R_InitFlats / R_InitSpriteLumps ranges (backwards lookup, like C).
        self.firstflat = wad.get_num_for_name("F_START") + 1
        self.lastflat = wad.get_num_for_name("F_END") - 1
        self.numflats = self.lastflat - self.firstflat + 1
        self.firstsprite = wad.get_num_for_name("S_START") + 1
        self.lastsprite = wad.get_num_for_name("S_END") - 1
        self.numsprites = self.lastsprite - self.firstsprite + 1
        self._patches: dict[int, Patch] = {}

    # -- init (R_InitTextures / R_InitFlats / R_InitSpriteLumps) --

    @staticmethod
    def _load_pnames(wad: WadFile) -> list[int]:
        data = wad.cache_lump("PNAMES")
        count = _PNAMES_COUNT.unpack_from(data, 0)[0]
        lumps = []
        for i in range(count):
            name = data[4 + i * 8 : 4 + i * 8 + 8].split(b"\x00")[0].decode("ascii")
            lumps.append(wad.check_num_for_name(name))
        return lumps

    def _load_textures(self, wad: WadFile) -> list[Texture]:
        blobs: list[bytes] = [wad.cache_lump("TEXTURE1")]
        if wad.check_num_for_name("TEXTURE2") != -1:
            blobs.append(wad.cache_lump("TEXTURE2"))
        textures: list[Texture] = []
        for blob in blobs:
            count = _TEX_COUNT.unpack_from(blob, 0)[0]
            for i in range(count):
                offset = _TEX_OFS.unpack_from(blob, 4 + i * 4)[0]
                if offset > len(blob):
                    raise TextureError("R_InitTextures: bad texture directory")
                name, _masked, width, height, _cold, patchcount = (
                    _MTEX_HDR.unpack_from(blob, offset)
                )
                patches = []
                for j in range(patchcount):
                    ox, oy, patch, _step, _cmap = _MTEX_PATCH.unpack_from(
                        blob, offset + _MTEX_HDR.size + j * _MTEX_PATCH.size
                    )
                    lump = self.patch_lumps[patch]
                    if lump == -1:
                        tname = name.split(b"\x00")[0].decode("ascii")
                        raise TextureError(
                            f"R_InitTextures: Missing patch in texture {tname}"
                        )
                    patches.append((ox, oy, lump))
                # Largest power of two <= width, minus one (C loop).
                mask = 1
                while mask * 2 <= width:
                    mask <<= 1
                textures.append(
                    Texture(
                        name=name.split(b"\x00")[0].decode("ascii").upper(),
                        width=width,
                        height=height,
                        patches=patches,
                        widthmask=mask - 1,
                    )
                )
        return textures

    # -- name lookup (R_FlatNumForName / R_CheckTextureNumForName / ...) --

    def check_texture_num_for_name(self, name: str | bytes) -> int:
        """Texture index, 0 for the "-" NoTexture marker, -1 if missing."""
        if isinstance(name, bytes):
            name = name.split(b"\x00")[0].decode("ascii")
        name = name.split("\x00")[0].upper()
        if name.startswith(NO_TEXTURE):
            return 0
        for i, tex in enumerate(self.textures):
            if tex.name == name:
                return i
        return -1

    def texture_num_for_name(self, name: str | bytes) -> int:
        i = self.check_texture_num_for_name(name)
        if i == -1:
            raise TextureError(f"R_TextureNumForName: {name} not found")
        return i

    def flat_num_for_name(self, name: str | bytes) -> int:
        if isinstance(name, bytes):
            name = name.split(b"\x00")[0].decode("ascii")
        i = self.wad.check_num_for_name(name.split("\x00")[0].upper())
        if i == -1:
            raise TextureError(f"R_FlatNumForName: {name} not found")
        return i - self.firstflat

    # -- pixel access (R_GetColumn / flats / patches) --

    def get_patch(self, lump: int) -> Patch:
        cached = self._patches.get(lump)
        if cached is None:
            cached = decode_patch(self.wad.cache_lump(lump))
            self._patches[lump] = cached
        return cached

    def get_column(self, texnum: int, col: int) -> tuple[bytes, bytes]:
        """Decoded (pixels, opaque-mask) column, col wrapped by widthmask."""
        tex = self.textures[texnum]
        if tex._columns is None:
            tex._columns = self._generate_columns(tex)
        return tex._columns[col & tex.widthmask]  # type: ignore[index]

    def _generate_columns(
        self, tex: Texture
    ) -> list[tuple[bytes, bytes]]:
        # Mirrors R_GenerateLookup + R_GenerateComposite: columns covered
        # by exactly one patch reference it directly (ignoring originy,
        # like the C renderer does); multi-patch columns are composited
        # back-to-front with origins applied.
        covering: list[list[tuple[int, int]]] = [[] for _ in range(tex.width)]
        patch_objs = [self.get_patch(lump) for _, _, lump in tex.patches]
        for pi, ((ox, _oy, _lump), patch) in enumerate(
            zip(tex.patches, patch_objs)
        ):
            x1, x2 = ox, ox + patch.width
            for x in range(max(x1, 0), min(x2, tex.width)):
                covering[x].append((pi, x - x1))
        out: list[tuple[bytes, bytes]] = []
        for x in range(tex.width):
            cov = covering[x]
            if len(cov) == 1:
                _ox, _oy, lump = tex.patches[cov[0][0]]
                pixels, mask = self.get_patch(lump).column_pixels(cov[0][1])
                out.append(
                    (pixels[: tex.height].ljust(tex.height, b"\x00"),
                     mask[: tex.height].ljust(tex.height, b"\x00"))
                )
            elif not cov:
                raise TextureError(
                    f"R_GenerateLookup: column without a patch ({tex.name})"
                )
            else:
                canvas = bytearray(tex.height)
                mask = bytearray(tex.height)
                for pi, px in cov:
                    ox, oy, lump = tex.patches[pi]
                    for topdelta, pixels in self.get_patch(lump).columns[px]:
                        for i, pix in enumerate(pixels):
                            y = oy + topdelta + i
                            if 0 <= y < tex.height:
                                canvas[y] = pix
                                mask[y] = 1
                out.append((bytes(canvas), bytes(mask)))
        return out

    def get_flat(self, flatnum: int) -> bytes:
        """Raw 64x64 flat pixels."""
        return bytes(self.wad.cache_lump(self.firstflat + flatnum))

    def get_sprite_patch(self, spritenum: int) -> Patch:
        return self.get_patch(self.firstsprite + spritenum)

    # -- map name resolution (fills the MVP placeholders in mapdata) --

    def resolve_map(self, game_map: Map) -> None:
        """Fill side texture numbers and sector flat numbers from names."""
        for side in game_map.sides:
            side.toptexture = self.texture_num_for_name(side.top_name or "-")
            side.midtexture = self.texture_num_for_name(side.mid_name or "-")
            side.bottomtexture = self.texture_num_for_name(
                side.bottom_name or "-"
            )
        for sector in game_map.sectors:
            sector.floorpic = self.flat_num_for_name(sector.floorpic_name)
            sector.ceilingpic = self.flat_num_for_name(sector.ceilingpic_name)


def texture_height_fixed(tex: Texture) -> int:
    """textureheight[] lookup (height in fixed-point)."""
    return tex.height << FRACBITS
