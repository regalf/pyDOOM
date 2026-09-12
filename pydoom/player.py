"""Player inventory (player_t lite): ammo, weapons, armor, keys, powers.

Health itself lives on the player mobj (mo.health); everything carried
goes here. Vanilla names and limits are kept so combat/pickup/doors can
share the same constants.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pydoom.fixed import FRACUNIT
from pydoom.ticcmd import Ticcmd

# Ammo types (ammotype_t order).
AM_CLIP, AM_SHELL, AM_CELL, AM_MISL = 0, 1, 2, 3
AMMO_NAMES = ("clip", "shell", "cell", "rocket")
MAX_AMMO = [200, 50, 50, 300]
# Clip size per ammo type (clipammo[]): box/ammo pickups scale by this.
CLIP_AMMO = [10, 4, 20, 1]

# Weapon slots (weapontype_t order); the viewer fires the pistol only,
# but ownership/pending are tracked vanilla-style for the weapons phase.
WP_FIST, WP_PISTOL, WP_SHOTGUN, WP_CHAINGUN, WP_MISSILE = 0, 1, 2, 3, 4
WP_PLASMA, WP_BFG, WP_CHAINSAW, WP_SSG = 5, 6, 7, 8
WEAPON_NAMES = ("fist", "pistol", "shotgun", "chaingun", "missile",
                "plasma", "bfg", "chainsaw", "ssg")
# Ammo type per weapon, -1 for none (weaponinfo[].ammo).
WEAPON_AMMO = (-1, AM_CLIP, AM_SHELL, AM_CLIP, AM_MISL,
               AM_CELL, AM_CELL, -1, AM_SHELL)

# Key bitmask (card_t order: cards then skulls, either opens its color).
KEY_BLUE, KEY_YELLOW, KEY_RED = 1, 2, 4
KEY_BSKULL, KEY_YSKULL, KEY_RSKULL = 8, 16, 32
KEY_COLORS = {"blue": KEY_BLUE | KEY_BSKULL,
              "yellow": KEY_YELLOW | KEY_YSKULL,
              "red": KEY_RED | KEY_RSKULL}

# Cheat flags (doomdef.h CF_): god and noclip live on PlayerState.cheats.
CF_NOCLIP, CF_GODMODE, CF_NOMOMENTUM = 1, 2, 4

# Powers (powertype_t names); tics remaining, strength is level-long.
PW_INVULN, PW_STRENGTH, PW_INVIS = "invuln", "strength", "invis"
PW_IRONFEET, PW_ALLMAP, PW_INFRARED = "ironfeet", "allmap", "infrared"

# NOTE: eye height above the feet (p_local.h VIEWHEIGHT); the p_user
# thinker walks viewheight toward this after stairs and landings.
VIEWHEIGHT = 41 * FRACUNIT

MAXHEALTH = 100
GODHEALTH = 200  # soulsphere/health-bonus cap (maxhealth stays 100)

# NOTE: set from the IWAD mission at boot (shareware/registered);
# plasma/BFG/SSG never spawn in Doom 1, and vanilla gates them out
# of the CheckAmmo fallback (p_pspr.c).
GAMEMODE = "shareware"


@dataclass(eq=False)
class PlayerState:
    """Everything a Doom player carries (player_t minus position)."""

    ammo: list = field(default_factory=lambda: [50, 0, 0, 0])
    maxammo: list = field(default_factory=lambda: list(MAX_AMMO))
    weapons: int = (1 << WP_FIST) | (1 << WP_PISTOL)
    readyweapon: int = WP_PISTOL
    pendingweapon: int = WP_PISTOL
    switchtics: int = 0  # raise delay while pending != ready
    backpack: bool = False
    armorpoints: int = 0
    armortype: int = 0  # 0 none, 1 green (1/3), 2 blue (1/2)
    keys: int = 0
    powers: dict = field(default_factory=dict)
    damagecount: int = 0  # NOTE: red palette flash, decays per tic
    bonuscount: int = 0  # NOTE: gold pickup flash, decays per tic
    cheats: int = 0  # CF_GODMODE/CF_NOCLIP bits (iddqd/idclip)
    killcount: int = 0  # NOTE: intermission tally, reset per level
    itemcount: int = 0  # NOTE: intermission tally, reset per level
    secretcount: int = 0  # NOTE: intermission tally, reset per level
    # NOTE: p_user thinker state (player_t minus the mobj link): eye
    # height walks toward VIEWHEIGHT, bob feeds view and gun sway,
    # usedown edges BT_USE, playerstate is PST_LIVE/DEAD/REBORN, and cmd
    # is the last built Ticcmd (friction reads its move axes).
    viewheight: int = VIEWHEIGHT
    deltaviewheight: int = 0
    bob: int = 0
    usedown: bool = False
    playerstate: int = 0  # PST_LIVE (p_user.PST_DEAD/PST_REBORN)
    cmd: Ticcmd | None = None

    def tick(self, player_mo=None) -> None:
        """P_PlayerThink counters: powers, palette flash countdowns."""
        if self.damagecount:
            self.damagecount -= 1
        if self.bonuscount:
            self.bonuscount -= 1
        if self.powers.get(PW_STRENGTH):
            self.powers[PW_STRENGTH] += 1
        for name in list(self.powers):
            if name in (PW_STRENGTH, PW_ALLMAP):
                continue
            left = self.powers[name] - 1
            if left <= 0:
                del self.powers[name]
                if name == PW_INVIS and player_mo is not None:
                    from pydoom.info import MF_FLAGS
                    player_mo.flags &= ~MF_FLAGS["MF_SHADOW"]
            else:
                self.powers[name] = left


def palette_index(ps) -> int:
    """ST_doPaletteStuff: PLAYPAL slot (0 normal, 1-8 red, 9-12 gold,
    13 radsuit) from the flash counters."""
    cnt = ps.damagecount
    if ps.powers.get(PW_STRENGTH):
        # NOTE: berserk red slowly fades as strength counts up.
        bzc = 12 - (ps.powers[PW_STRENGTH] >> 6)
        if bzc > cnt:
            cnt = bzc
    if cnt:
        return min((cnt + 7) >> 3, 7) + 1
    if ps.bonuscount:
        return min((ps.bonuscount + 7) >> 3, 3) + 9
    iron = ps.powers.get(PW_IRONFEET, 0)
    if iron > 4 * 32 or iron & 8:
        return 13
    return 0
