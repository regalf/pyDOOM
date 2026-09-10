"""Classic Doom cheat codes (m_cheat.c / st_stuff.h STSTR_*).

Sequence matching (cht_CheckCheat) lives in CheatEngine.feed; the actual
effects mirror the C switch: god toggles CF_GODMODE and resets health,
idkfa/idfa hand out guns/ammo/armor (+keys for kfa), behold grants powers
with pickup durations (P_GivePower). Map warps, music changes and automap
cycling are viewer-side (returned as events), like G_DeferedInitNew.
"""

from collections import deque

from pydoom.pickup import (
    INFRATICS,
    INVISTICS,
    INVULNTICS,
    IRONTICS,
    give_power,
)
from pydoom.player import (
    CF_GODMODE,
    KEY_BLUE,
    KEY_BSKULL,
    KEY_RED,
    KEY_RSKULL,
    KEY_YELLOW,
    KEY_YSKULL,
    PW_ALLMAP,
    PW_INFRARED,
    PW_INVIS,
    PW_INVULN,
    PW_IRONFEET,
    PW_STRENGTH,
    WP_CHAINSAW,
)

GOD_ON = "Degreelessness Mode On"
GOD_OFF = "Degreelessness Mode Off"
KFA_ADDED = "Very Happy Ammo Added"
FA_ADDED = "Ammo (no keys) Added"
NOCLIP_ON = "No Clipping Mode ON"
NOCLIP_OFF = "No Clipping Mode OFF"
BEHOLD_HINT = "inVuln, Str, Inviso, Rad, Allmap, or Lite-amp"
CHOPPERS = "Mercenary"
CLEV = "Changing Level..."
MUS = "Music Change"
MYPOS_FMT = "ang={a};x,y=({x},{y})"

IDKFA_ARMOR, IDKFA_CLASS = 200, 2  # dehacked idfa_armor defaults

# behold letter -> (power, tics, got-message), mirroring SPEC strings.
BEHOLD = {
    "v": (PW_INVULN, INVULNTICS, "INVULNERABILITY!"),
    "s": (PW_STRENGTH, 1, "BERSERK!"),
    "i": (PW_INVIS, INVISTICS, "PARTIAL INVISIBILITY"),
    "r": (PW_IRONFEET, IRONTICS, "RADIATION SHIELDING SUIT"),
    "a": (PW_ALLMAP, -1, "COMPUTER AREA MAP"),
    "l": (PW_INFRARED, INFRATICS, "LIGHT AMPLIFICATION VISOR"),
}

# code -> arg chars (0 = fires immediately).
CODES = {
    "iddqd": 0, "idkfa": 0, "idfa": 0, "idclip": 0, "idspispopd": 0,
    "idchoppers": 0, "iddt": 0, "idmypos": 0,
    "idclev": 2, "idmus": 2, "idbehold": 1,
}


class CheatEngine:
    """Feeds typed chars, fires (name, arg) events on full matches."""

    def __init__(self) -> None:
        self.buf: deque = deque(maxlen=16)
        self._collect: list | None = None  # [name, need, got]

    def feed(self, ch: str) -> list[tuple[str, str]]:
        """Offer one typed char; returns fired events (usually none)."""
        ch = ch.lower()
        if len(ch) != 1 or not ch.isalnum():
            return []
        if self._collect is not None:
            name, need, got = self._collect
            got += ch
            if len(got) >= need:
                self._collect = None
                self.buf.clear()
                return [(name, got[:need])]
            self._collect[1] = need  # keep waiting
            self._collect[2] = got
            return []
        self.buf.append(ch)
        tail = "".join(self.buf)
        for code, need in CODES.items():
            if tail.endswith(code):
                self.buf.clear()
                if need:
                    self._collect = [code, need, ""]
                    return []
                return [(code, "")]
        return []


def apply_god(ps, player_mo) -> str:
    """iddqd: toggle godmode; switching on restores full health."""
    ps.cheats ^= CF_GODMODE
    if ps.cheats & CF_GODMODE:
        if player_mo is not None:
            player_mo.health = 100
        return GOD_ON
    return GOD_OFF


def _full_loadout(ps, with_keys: bool) -> None:
    ps.weapons = (1 << 9) - 1  # fist thru ssg, plasma/BFG included
    for i in range(len(ps.ammo)):
        ps.ammo[i] = ps.maxammo[i]
    ps.armorpoints = IDKFA_ARMOR
    ps.armortype = IDKFA_CLASS
    if with_keys:
        ps.keys |= (KEY_BLUE | KEY_YELLOW | KEY_RED | KEY_BSKULL
                    | KEY_YSKULL | KEY_RSKULL)


def apply_kfa(ps) -> str:
    """idkfa: guns, full ammo, blue 200 armor, every key."""
    _full_loadout(ps, True)
    return KFA_ADDED


def apply_fa(ps) -> str:
    """idfa: same loadout, no keys."""
    _full_loadout(ps, False)
    return FA_ADDED


def apply_choppers(ps) -> str:
    """idchoppers: the chainsaw, raised immediately."""
    ps.weapons |= 1 << WP_CHAINSAW
    ps.pendingweapon = WP_CHAINSAW
    return CHOPPERS


def apply_behold(ps, player_mo, letter: str) -> str:
    """idbehold<v/s/i/r/a/l>: power with its pickup duration."""
    spec = BEHOLD.get(letter.lower())
    if spec is None:
        return BEHOLD_HINT
    name, tics, msg = spec
    give_power(ps, player_mo, name, tics)
    return msg
