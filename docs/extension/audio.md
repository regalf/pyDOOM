# Audio for mods: `sfx_play` and `music_change`

```python
api.on("sfx_play", self._sfx)      # rename / silence any sound
api.on("music_change", self._mus)  # swap / hold any song
```

Both fire before the engine acts, on the only choke points to the
mixer (`audio.play`, `audio.music_play`). `consume()` silences
(`False`), assigning `ev.name` / `ev.song` swaps the lump. The options
volume always governs: renamed sounds resolve through `engine.play`,
so positional attenuation × `master` (sfx slider) × mute apply
identically — a mod cannot bypass user volume
(`test_renamed_sfx_obeys_master_volume` locks this).

UI ticks (`pstop`, `swtchn`, ...) pass through `sfx_play` too: filter
by name if you only want world sounds.

## SFX IDs (49, engine table)

Lump is always `DS` + uppercase name. Priority decides channel
stealing (higher wins).

| id | lump | prio | id | lump | prio |
|---|---|---|---|---|---|
| pistol | DSPISTOL | 64 | shotgn | DSSHOTGN | 64 |
| rlaunc | DSRLAUNC | 64 | plasma | DSPLASMA | 64 |
| bfg | DSBFG | 64 | firsht | DSFIRSHT | 70 |
| claw | DSCLAW | 70 | doropn | DSDOROPN | 100 |
| dorcls | DSDORCLS | 100 | pstart | DSPSTART | 100 |
| stnmov | DSSTNMOV | 119 | pstop | DSPSTOP | 100 |
| swtchn | DSSWTCHN | 78 | swtchx | DSSWTCHX | 78 |
| sgcock | DSSGCOCK | 64 | plpain | DSPLPAIN | 96 |
| dmpain | DSDMPAIN | 96 | popain | DSPOPAIN | 96 |
| oof | DSOOF | 96 | itemup | DSITEMUP | 78 |
| getpow | DSGETPOW | 60 | wpnup | DSWPNUP | 78 |
| telept | DSTELEPT | 32 | posit1 | DSPOSIT1 | 98 |
| posit2 | DSPOSIT2 | 98 | posit3 | DSPOSIT3 | 98 |
| bgsit1 | DSBGSIT1 | 98 | sgtsit | DSSGTSIT | 98 |
| brssit | DSBRSSIT | 94 | sgtatk | DSSGTATK | 70 |
| podth1 | DSPODTH1 | 70 | podth2 | DSPODTH2 | 70 |
| bgdth1 | DSBGDTH1 | 70 | sgtdth | DSSGTDTH | 70 |
| brsdth | DSBRSDTH | 32 | posact | DSPOSACT | 120 |
| bgact | DSBGACT | 120 | dmact | DSDMACT | 120 |
| noway | DSNOWAY | 78 | punch | DSPUNCH | 64 |
| sawidl | DSSAWIDL | 118 | sawhit | DSSAWHIT | 64 |
| sawful | DSSAWFUL | 64 | pldeth | DSPLDETH | 32 |
| pdiehi | DSPDIEHI | 32 | slop | DSSLOP | 78 |
| rxplod | DSRXPLOD | 70 | firxpl | DSFIRXPL | 70 |
| barexp | DSBAREXP | 60 | | | |

Present in the registered `doom.wad` but with **no engine ID yet**
(silent until the table grows): BDCLS, BDOPN, BGDTH2, BGSIT2, CACDTH,
CACSIT, CYBDTH, CYBSIT, HOOF, ITMBK, METAL, PODTH3, SAWUP, SKLATK,
SKLDTH, SPIDTH, SPISIT, TINK.

## Music IDs

Map songs follow `D_EXMY` (`song_for_map`, unknown maps fall back to
`D_E1M1`). This repo's registered WAD holds E1M1–E3M9 (27 maps, all
present, no E4):

D_E1M1 … D_E1M9, D_E2M1 … D_E2M9, D_E3M1 … D_E3M9.

Special songs and the triggers the engine uses (`music_change`
sees both lump and trigger):

| lump | triggers |
|---|---|
| D_INTRO | boot-title, demo-title, demo-stop, finale-title |
| map song | boot-level, load-game, new-game, attract-demo, inter-next |
| D_INTER | exit-inter |
| D_VICTOR | exit-finale |
| D_EXMY (idmus) | idmus |

In the WAD but unused by the engine: D_BUNNY (cast roll),
D_INTROA (alternate title). Both are valid `music_change` targets.

## Example: hit-sound + boss music

```python
def on_enable(self, api):
    api.on("damage", self._hit)          # sim hook, needs care
    api.on("music_change", self._mus)

def _hit(self, ev):
    if getattr(ev.source, "is_player", False):
        from pydoom import audio
        audio.play("posact")             # re-enters sfx_play, fine

def _mus(self, ev):
    if ev.trigger == "exit-inter":
        ev.song = "D_E3M8"               # custom intermission song
```
