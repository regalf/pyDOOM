"""WAD file reader.

Port of ``w_wad.h`` / ``w_wad.c`` (linuxdoom-1.10) with one pragmatic
simplification: no zone memory (``z_zone``) and no permanently open
handles. Lumps are read on demand from disk and cached in a ``bytes``
dict.

WAD layout (all little-endian, see ``m_swap.h``: on x86 hosts SHORT/LONG
are identity)::

    header:   4s identification ("IWAD"/"PWAD"), i numlumps, i infotableofs
    directory: per lump: i filepos, i size, 8s name
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field

__all__ = [
    "LumpNotFoundError",
    "LumpInfo",
    "WadFile",
    "MAP_LUMP_ORDER",
]

_HEADER_STRUCT = struct.Struct("<4sii")
_DIR_ENTRY_STRUCT = struct.Struct("<ii8s")

#: Map lump order, see the enum in doomdata.h (ML_LABEL..ML_BLOCKMAP).
MAP_LUMP_ORDER = [
    "LABEL",
    "THINGS",
    "LINEDEFS",
    "SIDEDEFS",
    "VERTEXES",
    "SEGS",
    "SSECTORS",
    "NODES",
    "SECTORS",
    "REJECT",
    "BLOCKMAP",
]


class LumpNotFoundError(KeyError):
    """Equivalent of ``I_Error("W_GetNumForName: %s not found!")``."""


def _normalize_query(name: str | bytes) -> bytes:
    """Normalize a lump name for case-insensitive comparison."""
    if isinstance(name, str):
        name = name.encode("ascii", errors="strict")
    return name.upper().rstrip(b"\x00")


@dataclass
class LumpInfo:
    """Matches ``lumpinfo_t`` plus the source file index."""

    name: str
    position: int
    size: int
    file_index: int = 0
    raw_name: bytes = field(default=b"", repr=False, compare=False)


class WadFile:
    """Combined WAD directory from one or more files (IWAD + optional PWADs).

    Like the C code, lookup scans *backwards*: files added later
    (typically PWADs) override same-named lumps from earlier ones.
    """

    def __init__(self, paths: str | list[str]) -> None:
        if isinstance(paths, str):
            paths = [paths]
        self.paths: list[str] = list(paths)
        self.lumps: list[LumpInfo] = []
        self._cache: dict[int, bytes] = {}
        for file_index, path in enumerate(self.paths):
            self._add_file(path, file_index)
        if not self.lumps:
            raise ValueError("W_InitFiles: no files found")

    # -- directory loading (W_AddFile) --

    def _add_file(self, path: str, file_index: int) -> None:
        with open(path, "rb") as f:
            if path.lower().endswith(".wad"):
                header = f.read(_HEADER_STRUCT.size)
                if len(header) < _HEADER_STRUCT.size:
                    raise ValueError(f"Wad file {path}: truncated header")
                ident, numlumps, infotableofs = _HEADER_STRUCT.unpack(header)
                if ident not in (b"IWAD", b"PWAD"):
                    raise ValueError(
                        f"Wad file {path} doesn't have IWAD or PWAD id"
                    )
                f.seek(infotableofs)
                directory = f.read(_DIR_ENTRY_STRUCT.size * numlumps)
                if len(directory) < _DIR_ENTRY_STRUCT.size * numlumps:
                    raise ValueError(f"Wad file {path}: truncated directory")
                for i in range(numlumps):
                    filepos, size, raw = _DIR_ENTRY_STRUCT.unpack_from(
                        directory, i * _DIR_ENTRY_STRUCT.size
                    )
                    self.lumps.append(
                        LumpInfo(
                            name=raw.split(b"\x00")[0].decode("ascii"),
                            position=filepos,
                            size=size,
                            file_index=file_index,
                            raw_name=raw,
                        )
                    )
            else:
                # Single file = one lump named after the file base name
                # (see ExtractFileBase in w_wad.c).
                import os

                base = os.path.basename(path)
                base = base.split(".")[0][:8].upper()
                f.seek(0, 2)
                size = f.tell()
                self.lumps.append(
                    LumpInfo(
                        name=base,
                        position=0,
                        size=size,
                        file_index=file_index,
                        raw_name=base.encode("ascii"),
                    )
                )

    # -- lookup (W_CheckNumForName / W_GetNumForName) --

    def __len__(self) -> int:
        return len(self.lumps)

    def check_num_for_name(self, name: str | bytes) -> int:
        """Lump index, or -1 if missing. Scans backwards (PWAD override)."""
        key = _normalize_query(name)
        for i in range(len(self.lumps) - 1, -1, -1):
            if _normalize_query(self.lumps[i].raw_name) == key:
                return i
        return -1

    def get_num_for_name(self, name: str | bytes) -> int:
        i = self.check_num_for_name(name)
        if i == -1:
            q = name if isinstance(name, str) else name.decode("ascii", "replace")
            raise LumpNotFoundError(f"W_GetNumForName: {q} not found!")
        return i

    # C-style aliases.
    W_CheckNumForName = check_num_for_name
    W_GetNumForName = get_num_for_name

    # -- reading (W_LumpLength / W_ReadLump / W_CacheLumpNum) --

    def lump_length(self, lump: int) -> int:
        return self.lumps[lump].size

    def read_lump(self, lump: int | str | bytes) -> bytes:
        """Read the raw bytes of a lump (by index or by name)."""
        if isinstance(lump, (str, bytes)):
            lump = self.get_num_for_name(lump)
        info = self.lumps[lump]  # IndexError if out of range, like the C abort
        with open(self.paths[info.file_index], "rb") as f:
            f.seek(info.position)
            data = f.read(info.size)
        if len(data) < info.size:
            raise IOError(
                f"W_ReadLump: only read {len(data)} of {info.size} on lump {lump}"
            )
        return data

    def cache_lump(self, lump: int | str | bytes) -> bytes:
        """Cached version of :meth:`read_lump` (see ``W_CacheLumpNum``)."""
        if isinstance(lump, (str, bytes)):
            lump = self.get_num_for_name(lump)
        cached = self._cache.get(lump)
        if cached is None:
            cached = self.read_lump(lump)
            self._cache[lump] = cached
        return cached

    # C-style aliases.
    W_LumpLength = lump_length
    W_ReadLump = read_lump
    W_CacheLumpNum = cache_lump

    # -- map helpers --

    def is_map_marker(self, index: int) -> bool:
        """True if the lump is a map marker (ExMx / MAPxx)."""
        name = _normalize_query(self.lumps[index].raw_name).decode("ascii")
        if len(name) == 4 and name[0] == "E" and name[2] == "M":
            return name[1].isdigit() and name[3].isdigit()
        if len(name) == 5 and name.startswith("MAP"):
            return name[3:].isdigit()
        return False

    def map_lump_indices(self, marker: str) -> dict[str, int]:
        """Lump indices of a map from its marker (e.g. ``"E1M1"``).

        Replicates the ``P_SetupLevel`` assumption: the 10 lumps follow
        the marker in ML_THINGS..ML_BLOCKMAP order.
        """
        base = self.get_num_for_name(marker)
        names = MAP_LUMP_ORDER[1:]  # excluding LABEL
        if base + len(names) >= len(self.lumps):
            raise LumpNotFoundError(f"Map {marker}: following lumps missing")
        return {
            kind: base + 1 + i for i, kind in enumerate(names)
        }

    def list_maps(self) -> list[str]:
        """All map markers in the WAD, in directory order."""
        return [
            lump.name
            for i, lump in enumerate(self.lumps)
            if self.is_map_marker(i)
        ]
