"""Item pickups (p_inter.c): walk-over specials feed PlayerState.

Mapping is by doomednum (1:1 with sprite in vanilla); monster drops,
which carry no doomednum, resolve by mobj type instead. Behaviors mirror
vanilla P_TouchSpecialThing/P_Give*: full ammo refuses, health bonuses
cap at 200, armor follows the hits formula, ironfeet/allmap duplicates
stay (invuln/visor/strength always refresh).

Removal from the world is the caller's job: touch_special_thing only
reports picked=True (collect_touched unlinks and unlists).
"""

from __future__ import annotations

from pydoom.fixed import FRACUNIT
from pydoom.info import MF_FLAGS
from pydoom.player import (
    AM_CELL,
    AM_CLIP,
    AM_MISL,
    AM_SHELL,
    CLIP_AMMO,
    GAMEMODE,
    GODHEALTH,
    KEY_BLUE,
    KEY_BSKULL,
    KEY_RED,
    KEY_RSKULL,
    KEY_YELLOW,
    KEY_YSKULL,
    MAXHEALTH,
    PW_ALLMAP,    PW_INFRARED,
    PW_INVIS,
    PW_INVULN,
    PW_IRONFEET,
    PW_STRENGTH,
    WP_BFG,
    WP_CHAINGUN,
    WP_CHAINSAW,
    WP_FIST,
    WP_MISSILE,
    WP_PISTOL,
    WP_PLASMA,
    WP_SHOTGUN,
    WP_SSG,
)

_MF_SPECIAL = MF_FLAGS["MF_SPECIAL"]
_MF_DROPPED = MF_FLAGS["MF_DROPPED"]
_MF_SHADOW = MF_FLAGS["MF_SHADOW"]

TICRATE = 35
INVULNTICS = 30 * TICRATE
INVISTICS = 60 * TICRATE
INFRATICS = 120 * TICRATE
IRONTICS = 60 * TICRATE

# Behavior specs: (kind, ...) per doomednum.
# ammo: (ammo_idx, clips, message)  clips=0 means dropped (half clip)
# body: (amount, message)  hbonus/abonus/armor/weapon/key/power/backpack/...
_BY_DOOMED = {
    2007: ("ammo", AM_CLIP, 1, "PICKED UP THE CLIP."),
    2048: ("ammo", AM_CLIP, 5, "PICKED UP THE BOX OF BULLETS."),
    2010: ("ammo", AM_MISL, 1, "PICKED UP A ROCKET."),
    2046: ("ammo", AM_MISL, 5, "PICKED UP A BOX OF ROCKETS."),
    2047: ("ammo", AM_CELL, 1, "PICKED UP AN ENERGY CELL."),
    17: ("ammo", AM_CELL, 5, "PICKED UP AN ENERGY CELL PACK."),
    2008: ("ammo", AM_SHELL, 1, "PICKED UP 4 SHOTGUN SHELLS."),
    2049: ("ammo", AM_SHELL, 5, "PICKED UP 20 SHOTGUN SHELLS."),
    8: ("backpack", "PICKED UP A BACKPACK FULL OF AMMO!"),
    2014: ("hbonus", "PICKED UP A HEALTH BONUS."),
    2015: ("abonus", "PICKED UP AN ARMOR BONUS."),
    2011: ("body", 10, "PICKED UP A STIMPICK."),
    2012: ("body", 25, "PICKED UP A MEDI-KIT."),
    2013: ("soul", "SUPERCHARGE!"),
    83: ("mega", "MEGASPHERE!"),
    2018: ("armor", 1, "PICKED UP THE ARMOR."),
    2019: ("armor", 2, "PICKED UP THE MEGAARMOR."),
    2001: ("weapon", WP_SHOTGUN, "YOU GOT THE SHOTGUN!"),
    2002: ("weapon", WP_CHAINGUN, "YOU GOT THE CHAINGUN!"),
    2003: ("weapon", WP_MISSILE, "YOU GOT THE ROCKET LAUNCHER!"),
    2004: ("weapon", WP_PLASMA, "YOU GOT THE PLASMA GUN!"),
    2005: ("weapon", WP_CHAINSAW, "A CHAINSAW!  FIND SOME MEAT!"),
    2006: ("weapon", WP_BFG, "YOU GOT THE BFG9000!  OH, YES."),
    82: ("weapon", WP_SSG, "YOU GOT THE SUPER SHOTGUN!"),
    5: ("key", KEY_BLUE, "PICKED UP A BLUE KEYCARD."),
    13: ("key", KEY_RED, "PICKED UP A RED KEYCARD."),
    6: ("key", KEY_YELLOW, "PICKED UP A YELLOW KEYCARD."),
    40: ("key", KEY_BSKULL, "PICKED UP A BLUE SKULL KEY."),
    38: ("key", KEY_RSKULL, "PICKED UP A RED SKULL KEY."),
    39: ("key", KEY_YSKULL, "PICKED UP A YELLOW SKULL KEY."),
    2022: ("power", PW_INVULN, INVULNTICS, "INVULNERABILITY!"),
    2023: ("berserk", "BERSERK!"),
    2024: ("power", PW_INVIS, INVISTICS, "PARTIAL INVISIBILITY"),
    2025: ("power", PW_IRONFEET, IRONTICS, "RADIATION SHIELDING SUIT"),
    2026: ("power", PW_ALLMAP, -1, "COMPUTER AREA MAP"),
    2045: ("power", PW_INFRARED, INFRATICS, "LIGHT AMPLIFICATION VISOR"),
}


def give_ammo(ps, ammo: int, num: int, skill: str = "normal") -> bool:
    """P_GiveAmmo: num clips (0 = dropped monster clip, half size)."""
    if ammo < 0 or ammo >= len(ps.ammo):
        return False
    if ps.ammo[ammo] >= ps.maxammo[ammo]:
        return False  # full: leave it on the floor
    if num:
        num *= CLIP_AMMO[ammo]
    else:
        # NOTE: dropped rockets round down to +0 yet still count as taken.
        num = CLIP_AMMO[ammo] // 2
    if skill in ("baby", "nightmare"):
        # NOTE: trainer mode doubles it; you'll need it on nightmare.
        num <<= 1
    oldammo = ps.ammo[ammo]
    ps.ammo[ammo] += num
    if ps.ammo[ammo] > ps.maxammo[ammo]:
        ps.ammo[ammo] = ps.maxammo[ammo]
    if oldammo:
        return True  # was loaded: keep the current gun
    # NOTE: dry pickup auto-arms a matching gun (not user selectable).
    from pydoom.weapons import SWITCH_TICS
    pick = None
    if ammo == AM_CLIP and ps.readyweapon == WP_FIST:
        pick = (WP_CHAINGUN if ps.weapons & (1 << WP_CHAINGUN)
                else WP_PISTOL)
    elif ammo == AM_SHELL and ps.readyweapon in (WP_FIST, WP_PISTOL):
        if ps.weapons & (1 << WP_SHOTGUN):
            pick = WP_SHOTGUN
    elif ammo == AM_CELL and ps.readyweapon in (WP_FIST, WP_PISTOL):
        if ps.weapons & (1 << WP_PLASMA):
            pick = WP_PLASMA
    elif ammo == AM_MISL and ps.readyweapon == WP_FIST:
        if ps.weapons & (1 << WP_MISSILE):
            pick = WP_MISSILE
    if pick is not None and ps.pendingweapon != pick:
        ps.pendingweapon = pick
        ps.switchtics = SWITCH_TICS
    return True


def give_body(ps, picker_mo, num: int) -> bool:
    """P_GiveBody: heal, capped at MAXHEALTH (spheres bypass elsewhere)."""
    if picker_mo.health >= MAXHEALTH:
        return False
    picker_mo.health += num
    if picker_mo.health > MAXHEALTH:
        picker_mo.health = MAXHEALTH
    return True


def give_armor(ps, armortype: int) -> bool:
    """P_GiveArmor: weaker suits never replace what you wear."""
    hits = armortype * 100
    if ps.armorpoints >= hits:
        return False
    ps.armortype = armortype
    ps.armorpoints = hits
    return True


def give_weapon(ps, weapon: int, dropped: bool, skill: str = "normal") -> bool:
    """P_GiveWeapon: new guns arm pending; owned guns convert to ammo."""
    from pydoom.player import WEAPON_AMMO
    ammo = WEAPON_AMMO[weapon]
    if ammo < 0:
        gaveammo = False
    elif dropped:
        gaveammo = give_ammo(ps, ammo, 1, skill)
    else:
        gaveammo = give_ammo(ps, ammo, 2, skill)
    if ps.weapons & (1 << weapon):
        gaveweapon = False
    else:
        gaveweapon = True
        ps.weapons |= 1 << weapon
        ps.pendingweapon = weapon
    return gaveweapon or gaveammo


def give_backpack(ps, skill: str = "normal") -> bool:
    """Backpack: double max ammo once, plus one clip of everything."""
    if not ps.backpack:
        for i in range(len(ps.maxammo)):
            ps.maxammo[i] *= 2
        ps.backpack = True
    for i in range(len(ps.ammo)):
        give_ammo(ps, i, 1, skill)
    return True


def give_power(ps, picker_mo, name: str, tics: int) -> bool:
    """P_GivePower: invuln/invis/visor always refresh; strength always
    lands (=1, it counts up); ironfeet/allmap refuse duplicates."""
    if name in (PW_INVULN, PW_INVIS, PW_INFRARED):
        ps.powers[name] = tics
        if name == PW_INVIS and picker_mo is not None:
            picker_mo.flags |= _MF_SHADOW
        return True
    if name == PW_STRENGTH:
        ps.powers[name] = 1
        return True
    if ps.powers.get(name):
        return False
    ps.powers[name] = tics
    return True


def touch_special_thing(item, picker_mo, ps, ctx=None):
    """P_TouchSpecialThing: try one pickup. Returns (picked, message)."""
    from pydoom import audio
    from pydoom.info import MT_INDEX
    if picker_mo.health <= 0 or not getattr(picker_mo, "is_player", False):
        return False, None
    if item.dead or not (item.flags & _MF_SPECIAL):
        return False, None
    delta = item.z - picker_mo.z
    if delta > picker_mo.height or delta < -8 * FRACUNIT:
        return False, None  # out of reach
    dropped = bool(item.flags & _MF_DROPPED)
    spec = _BY_DOOMED.get(getattr(item, "doomednum", None))
    if spec is None:
        # Monster drops carry no doomednum; resolve by mobj type.
        by_mt = {MT_INDEX["CLIP"]: _BY_DOOMED[2007],
                 MT_INDEX["SHOTGUN"]: _BY_DOOMED[2001],
                 MT_INDEX["CHAINGUN"]: _BY_DOOMED[2002]}
        spec = by_mt.get(item.type)
    if spec is None:
        return False, None
    kind = spec[0]
    sound = None
    # NOTE: ext pickup (class/ammo mods): consume blocks the take
    # entirely (no apply, no tally, no sound).
    try:
        from pydoom import ext as _ext
        _mgr = _ext.current()
        if _mgr is not None:
            pev = _mgr.emit("pickup", item=item, player_mo=picker_mo,
                            ps=ps, kind=kind)
            if pev.consumed:
                return False, None
    except Exception:  # noqa: BLE001 - mods never break the sim
        pass
    if kind == "weapon":
        # NOTE: vanilla chimes wpnup here (p_inter.c), not itemup.
        sound = "wpnup"
    elif kind in ("power", "berserk", "soul", "mega"):
        sound = "getpow"
    elif kind in ("ammo", "body", "hbonus", "abonus", "armor", "backpack",
                  "key"):
        sound = "itemup"
    skill = getattr(ctx, "skill", "normal") if ctx is not None else "normal"
    picked, msg = _apply_touch(item, picker_mo, ps, spec, dropped, skill)
    if picked:
        ps.bonuscount += 6  # NOTE: BONUSADD gold flash per item
        if item.flags & MF_FLAGS["MF_COUNTITEM"]:
            ps.itemcount += 1  # NOTE: intermission tally
        if sound is not None:
            audio.play(sound, picker_mo.x, picker_mo.y, picker_mo)
    return picked, msg


def _apply_touch(item, picker_mo, ps, spec, dropped, skill="normal"):
    """The per-kind give table; returns (picked, message)."""
    kind = spec[0]
    if kind == "ammo":
        _, ammo, clips, msg = spec
        if not give_ammo(ps, ammo, 0 if dropped else clips, skill):
            return False, None
        return True, msg
    if kind == "body":
        if not give_body(ps, picker_mo, spec[1]):
            return False, None
        if spec[1] == 25:  # NOTE: medikit wording depends on need.
            msg = ("PICKED UP A MEDIKIT THAT YOU REALLY NEED!"
                   if picker_mo.health < 25 else spec[2])
        else:
            msg = spec[2]
        return True, msg
    if kind == "hbonus":
        if picker_mo.health >= GODHEALTH:
            return False, None
        picker_mo.health += 1
        if picker_mo.health > GODHEALTH:
            picker_mo.health = GODHEALTH
        return True, spec[1]
    if kind == "abonus":
        # NOTE: always taken (sets green from scratch), capped at 200.
        ps.armorpoints += 1
        if ps.armorpoints > GODHEALTH:
            ps.armorpoints = GODHEALTH
        if not ps.armortype:
            ps.armortype = 1
        return True, spec[1]
    if kind == "soul":
        # NOTE: always taken, even at 200 (wasted).
        picker_mo.health += 100
        if picker_mo.health > GODHEALTH:
            picker_mo.health = GODHEALTH
        return True, spec[1]
    if kind == "mega":
        # NOTE: commercial-only; shareware leaves the sphere alone.
        if GAMEMODE != "commercial":
            return False, None
        picker_mo.health = GODHEALTH
        give_armor(ps, 2)
        return True, spec[1]
    if kind == "armor":
        if not give_armor(ps, spec[1]):
            return False, None
        return True, spec[2]
    if kind == "weapon":
        if not give_weapon(ps, spec[1], dropped, skill):
            return False, None
        return True, spec[2]
    if kind == "backpack":
        give_backpack(ps, skill)
        return True, spec[1]
    if kind == "key":
        fresh = not (ps.keys & spec[1])
        ps.keys |= spec[1]
        return True, spec[2] if fresh else None
    if kind == "berserk":
        # NOTE: always lands, and arms the fists (vanilla switches you).
        give_power(ps, picker_mo, PW_STRENGTH, 1)
        give_body(ps, picker_mo, 100)
        if ps.readyweapon != WP_FIST:
            from pydoom.weapons import SWITCH_TICS
            ps.pendingweapon = WP_FIST
            ps.switchtics = SWITCH_TICS
        return True, spec[1]
    if kind == "power":
        _, name, tics, msg = spec
        if not give_power(ps, picker_mo, name, tics):
            return False, None
        return True, msg
    return False, None


def collect_touched(physics, picker_mo, ps, ctx=None):
    """Walk-over sweep: touch every reachable special sharing our blocks.

    Returns the last pickup message, if any. Picked items are unlinked,
    unlisted and marked dead (the viewer sweeps dead mobjs anyway).
    """
    from pydoom.physics import MAPBLOCKSHIFT, MAXRADIUS
    index = physics.things
    if index is None:
        return None
    if picker_mo.health <= 0 or not getattr(picker_mo, "is_player", False):
        return None
    bm = physics.map.blockmap
    r = picker_mo.radius
    xl = (picker_mo.x - r - bm.orgx - MAXRADIUS) >> MAPBLOCKSHIFT
    xh = (picker_mo.x + r - bm.orgx + MAXRADIUS) >> MAPBLOCKSHIFT
    yl = (picker_mo.y - r - bm.orgy - MAXRADIUS) >> MAPBLOCKSHIFT
    yh = (picker_mo.y + r - bm.orgy + MAXRADIUS) >> MAPBLOCKSHIFT
    message = None
    for bx in range(xl, xh + 1):
        for by in range(yl, yh + 1):
            for th in list(index.iter_block(bx, by)):
                if th is picker_mo or th.dead:
                    continue
                if not (th.flags & _MF_SPECIAL):
                    continue
                # NOTE: PIT_CheckThing reach: radii must overlap, else
                # the sweep margin would vacuum the room (~68u vs 36u).
                blockdist = th.radius + picker_mo.radius
                if abs(th.x - picker_mo.x) >= blockdist \
                        or abs(th.y - picker_mo.y) >= blockdist:
                    continue
                picked, msg = touch_special_thing(th, picker_mo, ps, ctx)
                if picked:
                    index.unlink(th)
                    th.dead = True
                    mobjs = getattr(ctx, "mobjs", None) if ctx is not None else None
                    if mobjs is not None and th in mobjs:
                        mobjs.remove(th)
                    if msg is not None:
                        message = msg
    return message
