# ID tables (everything else)

Engine constants mods compare against. Generated from the sources
(`pydoom/player.py`, `weapons.py`, `info.py`, `pickup.py`,
`ticcmd.py`, `doors.py`); sounds and music live in `audio.md`.

## Weapons (`pre_fire` / `post_fire`, `KEYMAP`)

| id | name | ammo | cost/shot | select key |
|---|---|---|---|---|
| 0 | fist | — | — | 1 |
| 1 | pistol | bullets | 1 | 2 |
| 2 | shotgun | shells | 1 | 3 |
| 3 | chaingun | bullets | 1 | 4 |
| 4 | rocket launcher | rockets | 1 | 5 |
| 5 | plasma gun | cells | 1 | 6 |
| 6 | BFG9000 | cells | 40 | 7 |
| 7 | chainsaw | — | — | 1 |
| 8 | super shotgun | shells | 2 | 3 |

Ammo indices: 0 bullets (max 200), 1 shells (max 50), 2 cells
(max 300), 3 rockets (max 50). Backpack doubles the maxima once.

## Monsters (`damage`, `on_kill`: `target_type`, drops)

`MT_INDEX` name, map doomednum, spawn health. Drops on death:
POSSESSED→clip, SHOTGUY→shotgun, WOLFSS→clip, CHAINGUN→chaingun.

| name | doomed | hp | name | doomed | hp |
|---|---|---|---|---|---|
| POSSESSED | 3004 | 20 | SHOTGUY | 9 | 30 |
| CHAINGUY | 65 | 70 | TROOP | 3001 | 60 |
| SERGEANT | 3002 | 150 | SHADOWS | 58 | 150 |
| HEAD | 3005 | 400 | BRUISER | 3003 | 1000 |
| KNIGHT | 69 | 500 | SKULL | 3006 | 100 |
| SPIDER | 7 | 3000 | BABY | 68 | 500 |
| CYBORG | 16 | 4000 | PAIN | 71 | 400 |
| WOLFSS | 84 | 50 | UNDEAD | 66 | 300 |
| FATSO | 67 | 600 | VILE | 64 | 700 |
| KEEN | 72 | 100 | BARREL | 2035 | 20 |

## Pickups (`pickup.kind`, by map doomednum)

| doomed | kind | effect | message |
|---|---|---|---|
| 2007 / 2048 | ammo | 1 / 5 clips bullets | PICKED UP THE CLIP. / BOX OF BULLETS. |
| 2010 / 2046 | ammo | 1 / 5 rockets | PICKED UP A ROCKET. / BOX OF ROCKETS. |
| 2047 / 17 | ammo | 1 / 5 cells | ENERGY CELL. / CELL PACK. |
| 2008 / 2049 | ammo | 1 / 5 shells (4/20 shells) | SHOTGUN SHELLS. / 20 SHOTGUN SHELLS. |
| 8 | backpack | double max ammo + clips | BACKPACK FULL OF AMMO! |
| 2014 / 2015 | hbonus / abonus | +1 hp / +1 armor | HEALTH / ARMOR BONUS. |
| 2011 / 2012 | body | +10 / +25 hp | STIMPACK. / MEDI-KIT. |
| 2013 / 83 | soul / mega | supercharge / megasphere | SUPERCHARGE! / MEGASPHERE! |
| 2018 / 2019 | armor | green (1/3) / blue (1/2) | ARMOR. / MEGAARMOR. |
| 2001 / 2002 / 2003 | weapon | shotgun / chaingun / rocket | YOU GOT THE ...! |
| 2004 / 2005 / 2006 | weapon | plasma / chainsaw / BFG | ... |
| 82 | weapon | super shotgun | YOU GOT THE SUPER SHOTGUN! |
| 5 / 13 / 6 | key | blue / red / yellow card | ... KEYCARD. |
| 40 / 38 / 39 | key | blue / red / yellow skull | ... SKULL KEY. |
| 2022 | power invuln | 30s godmode | INVULNERABILITY! |
| 2023 | berserk | ×10 fists | BERSERK! |
| 2024 | power invis | 60s partial | PARTIAL INVISIBILITY |
| 2025 | power ironfeet | 60s radsuit | RADIATION SHIELDING SUIT |
| 2026 | power allmap | permanent full map | COMPUTER AREA MAP |
| 2045 | power infrared | 120s light amp | LIGHT AMPLIFICATION VISOR |

Power IDs (`ps.powers` keys): `invuln`, `strength`, `invis`,
`ironfeet`, `allmap`, `infrared`. Key bits: blue 1, yellow 2,
red 4, blue-skull 8, yellow-skull 16, red-skull 32. Cheat bits:
noclip 1, godmode 2, nomomentum 4.

## Ticcmd (`build_ticcmd`)

Buttons bitmask: ATTACK 1, USE 2, CHANGE 4, WEAPONMASK 56,
WEAPONSHIFT 3, SPECIAL 128. Moves clamp at ±50 (`MAXPLMOVE`);
`angleturn` is int16, counter-clockwise positive.

## Palettes (`palette_flash`, slots 0–13)

0 normal; 1–8 red damage ramp (`(damagecount+7)>>3 + 1`, capped);
9–12 gold bonus ramp; 13 radsuit (`ironfeet`).

## Lines (`line_activate`)

`kind` is `use`, `shoot` or `cross`. Common specials (full tables in
`pydoom/doors.py`): 1 manual door; 11 exit, 51 secret exit,
52 walk-over exit; 24/46/47 shootables; 39/97/125/126 teleports
(125/126 monsters-only). `exit_kind` is `"normal"` or `"secret"`.

## Menu protocol (viewer ↔ menu)

`("ext_open",)` rebuilds the Extension list and opens it;
`("ext_toggle", id)` flips one mod. Mod states: `on`, `off`,
`refused` (`NEEDS <dep>`, `NEEDS <backend>`, `BAD ...`),
`error` (`BAD MANIFEST`, `BAD MODULE`, handler message).

## Settings snapshot (`settings`, read-only)

`mouse_sens` (slider int), `mouse_rad_per_px`
(`0.0004 + sens * 0.0006`, same factor as turning),
`sfx_vol` / `mus_vol` (0–15).
