"""Dynamic sector sync (milestone H, dynamic-sectors slice).

The GL world bakes wall quads + plane tris once per map, but the sim
mutates sectors live (doors, plats, floors, crushers, donuts, light
flicker/strobe, switch texture swaps). This module diffs the live
map against a snapshot per presented frame and classifies the change:

* CLEAN: nothing moved (steady state: zero GL work).
* LIGHT: only lightlevels moved (flicker/strobe every few tics):
  the wall/plane shaders read base lightnums from the sector-light
  texture, so only that tiny texture re-uploads (no VBO touch).
* GEO: heights, flat pics or sidedef texnums moved (doors, plats,
  donuts, switches): the viewer rebuilds walls + re-emits planes
  from the cached fan topology and re-uploads both VBOs.

Pure CPU, no GL imports (uploading stays in upload.py / the viewer).
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "CLEAN",
    "GEO",
    "LIGHT",
    "DynamicState",
    "sector_light_bases",
    "switch_pair_texnums",
]

CLEAN, LIGHT, GEO = 0, 1, 2


def sector_light_bases(game_map) -> list:
    """Base lightnum per sector (lightlevel>>4 clamped, the static
    part of the wall/plane row; extralight/tweak fold in shader).
    Matches the old baked values exactly (PlaneTri.light rule)."""
    return [min(max(s.lightlevel >> 4, 0), 15)
            for s in game_map.sectors]


def switch_pair_texnums(game_map, texman) -> set:
    """Switch-pair texnums to prebuild (both directions).

    P_ChangeSwitchTexture swaps sidedef texnums at runtime; the
    swapped-in texture must already have a GL texture or the batch
    lookup fails mid-frame. Pairs follow the vanilla SW1*/SW2*
    convention (doors.py switchlist); missing names are skipped
    (the sim never swaps to a texture TEXTURE1/2 lacks either)."""
    out = set()
    ntex = len(texman.textures)
    for side in game_map.sides:
        for texnum in (side.toptexture, side.midtexture,
                       side.bottomtexture):
            if not (0 < texnum < ntex):
                continue
            name = texman.textures[texnum].name
            pair = None
            if name.startswith("SW1"):
                pair = "SW2" + name[3:]
            elif name.startswith("SW2"):
                pair = "SW1" + name[3:]
            if pair is None:
                continue
            other = texman.check_texture_num_for_name(pair)
            if other > 0:
                out.add(other)
    return out


@dataclass
class DynamicState:
    """Snapshot of every runtime-mutable map field the GL world bakes
    (plus the cached plane fan topology for cheap re-emission)."""

    fans: list = field(default_factory=list)
    sec_floor: list = field(default_factory=list)
    sec_ceil: list = field(default_factory=list)
    sec_light: list = field(default_factory=list)
    sec_floorpic: list = field(default_factory=list)
    sec_ceilpic: list = field(default_factory=list)
    side_top: list = field(default_factory=list)
    side_mid: list = field(default_factory=list)
    side_bot: list = field(default_factory=list)

    @classmethod
    def take(cls, game_map) -> DynamicState:
        """Snapshot now (map load / level transition). Computes the
        Fraction-heavy fan topology once (shared with the initial
        plane build via emit_planes)."""
        from pydoom.glrender.preprocess import leaf_sector_fans
        return cls(
            fans=leaf_sector_fans(game_map),
            sec_floor=[s.floorheight for s in game_map.sectors],
            sec_ceil=[s.ceilingheight for s in game_map.sectors],
            sec_light=[s.lightlevel for s in game_map.sectors],
            sec_floorpic=[s.floorpic for s in game_map.sectors],
            sec_ceilpic=[s.ceilingpic for s in game_map.sectors],
            side_top=[s.toptexture for s in game_map.sides],
            side_mid=[s.midtexture for s in game_map.sides],
            side_bot=[s.bottomtexture for s in game_map.sides],
        )

    def diff(self, game_map) -> int:
        """Classify live-vs-snapshot drift, then re-snap (so the next
        frame is CLEAN unless the sim moves something again).

        Reads only (never mutates the sim: demo checksums and the
        software path are untouched)."""
        geo = (
            [s.floorheight for s in game_map.sectors]
            != self.sec_floor
            or [s.ceilingheight for s in game_map.sectors]
            != self.sec_ceil
            or [s.floorpic for s in game_map.sectors]
            != self.sec_floorpic
            or [s.ceilingpic for s in game_map.sectors]
            != self.sec_ceilpic
            or [s.toptexture for s in game_map.sides]
            != self.side_top
            or [s.midtexture for s in game_map.sides]
            != self.side_mid
            or [s.bottomtexture for s in game_map.sides]
            != self.side_bot
        )
        light = ([s.lightlevel for s in game_map.sectors]
                 != self.sec_light)
        self.sec_floor = [s.floorheight for s in game_map.sectors]
        self.sec_ceil = [s.ceilingheight for s in game_map.sectors]
        self.sec_light = [s.lightlevel for s in game_map.sectors]
        self.sec_floorpic = [s.floorpic for s in game_map.sectors]
        self.sec_ceilpic = [s.ceilingpic for s in game_map.sectors]
        self.side_top = [s.toptexture for s in game_map.sides]
        self.side_mid = [s.midtexture for s in game_map.sides]
        self.side_bot = [s.bottomtexture for s in game_map.sides]
        if geo:
            return GEO
        if light:
            return LIGHT
        return CLEAN
