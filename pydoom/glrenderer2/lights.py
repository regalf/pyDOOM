"""Dynamic point lights for glrenderer2 v2 (step 6, opt-in).

Pure CPU side (no GL imports, fully unit tested): the viewer builds
one entry per live light each frame, FrameRenderer2.set_lights packs
the first MAX_LIGHTS into uniforms. World units everywhere (map
units, same space as the wall/plane shaders). Muzzle flash always
first (the light the player aims by); sprites stay on the vanilla
path at v0 (like GZDoom's CPU-summed sprite lights, later).
"""

from __future__ import annotations

__all__ = [
    "MAX_LIGHTS",
    "missile_light",
    "muzzle_light",
    "pack_lights",
]

MAX_LIGHTS = 8

_MUZZLE_RADIUS = 144.0
_MUZZLE_COLOR = (1.0, 0.75, 0.45, 1.0)

_MISSILE_STYLES = {
    # NOTE: keyed by caller-side style name (the viewer maps mobj
    # types via MT_INDEX); radius in world units, color rgb 0..1.
    "rocket": (112.0, (1.0, 0.55, 0.2, 0.9)),
    "plasma": (80.0, (0.35, 0.6, 1.0, 0.9)),
    "bfg": (144.0, (0.4, 1.0, 0.4, 0.9)),
}
_MISSILE_DEFAULT = (96.0, (1.0, 0.7, 0.35, 0.9))


def muzzle_light(x: float, y: float, z: float) -> tuple:
    """Muzzle-flash light (x, y, z, radius, r, g, b, intensity)."""
    r, g, b, i = _MUZZLE_COLOR
    return (float(x), float(y), float(z), _MUZZLE_RADIUS, r, g, b, i)


def missile_light(x: float, y: float, z: float,
                  style: str = "rocket") -> tuple:
    """Flying-projectile light; unknown styles get the warm default."""
    radius, (r, g, b, i) = _MISSILE_STYLES.get(style, _MISSILE_DEFAULT)
    return (float(x), float(y), float(z), radius, r, g, b, i)


def pack_lights(entries) -> list:
    """Cap the frame's lights (muzzle first: caller orders)."""
    return [tuple(e) for e in list(entries)[:MAX_LIGHTS]]
