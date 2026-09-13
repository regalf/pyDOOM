"""Monster/weapon/world sounds (s_sound.c lite) over pygame.mixer.

DS lumps are 11025 Hz 8-bit mono with an 8-byte header and feed mixer
buffers directly. Attenuation/pan mirror S_AdjustSoundParams (silent
past 1200 units, full within 160, stereo swing 96); channels preempt
by sfx priority, link sounds never stack. Music stays a stub like the
Linux reference (external MUS server, absent here).

Everything no-ops without a mixer (or without the lump), so headless
tests and silent machines play identically, minus the noise.
"""

from __future__ import annotations

import math

try:
    import pygame
except ImportError:  # pragma: no cover - silent fallback below
    pygame = None  # type: ignore[assignment]

from pydoom import tables
from pydoom.angles import point_to_angle2
from pydoom.fixed import FRACUNIT

SAMPLE_RATE = 44100  # mixer spec (chocolate: 44.1 kHz SFX)
# NOTE: signed 16-bit stereo (chocolate i_sdlsound.c); DS lumps ride
# up through upsample_sfx on load, like SDL's audio conversion.
MIXER_SIZE = -16
MIXER_CHANNELS = 2
N_CHANNELS = 8
SFX_RATE = 11025  # native DS lump rate
OPL_RATE = 22050  # chip render rate (chocolate opl.c)
CLIP_DIST = 1200  # map units, vanilla S_CLIPPING_DIST
CLOSE_DIST = 160  # map units, vanilla S_CLOSE_DIST
STEREO_SWING = 96  # vanilla S_STEREO_SWING

# NOTE: (priority, link-singleton) from sounds.c for the sounds we use.
SFX = {
    "pistol": (64, False), "shotgn": (64, False), "rlaunc": (64, False),
    "plasma": (64, False), "bfg": (64, False),
    "firsht": (70, False), "claw": (70, False),
    "doropn": (100, False), "dorcls": (100, False),
    "stnmov": (119, False), "pstop": (100, False),
    "swtchn": (78, False), "swtchx": (78, False),
    "sgcock": (64, False),  # NOTE: intermission advance (no DS lump)
    "plpain": (96, False), "dmpain": (96, False), "popain": (96, False),
    "oof": (96, False),  # NOTE: menu error buzz (DSOOF is in the IWAD)
    "itemup": (78, True), "getpow": (60, False),
    "wpnup": (78, False),  # NOTE: weapon pickups (DSWPNUP is in the IWAD)
    "telept": (32, False),
    "posit1": (98, True), "posit2": (98, True), "posit3": (98, True),
    "bgsit1": (98, True), "sgtsit": (98, True), "brssit": (94, True),
    "sgtatk": (70, False),
    "podth1": (70, False), "podth2": (70, False),
    "bgdth1": (70, False), "sgtdth": (70, False), "brsdth": (32, False),
    "posact": (120, True), "bgact": (120, True), "dmact": (120, True),
    "noway": (78, False), "punch": (64, False),
    "sawidl": (118, False),
    "sawhit": (64, False), "sawful": (64, False),
    "pldeth": (32, False), "pdiehi": (32, False),
    "slop": (78, False),
    "rxplod": (70, False), "firxpl": (70, False), "barexp": (60, False),
}

# NOTE: projectile/barrel explosion voices (P_ExplodeMissile plays the
# deathsound; barrels scream through A_Scream on BEXP2 instead).
MISSILE_DEATHS = {
    "TROOPSHOT": "firxpl", "HEADSHOT": "firxpl", "BRUISERSHOT": "firxpl",
    "ROCKET": "barexp", "PLASMA": "firxpl", "BFG": "rxplod",
}
# NOTE: (wake, pain, death, idle) per monster, from mobjinfo seesound/
# painsound/deathsound/activesound. Missing lumps (Doom 2 cast) simply
# never play; attacks sound in their action code, like vanilla.
MONSTERS = {
    "POSSESSED": ("posit1", "popain", "podth1", "posact"),
    "SHOTGUY": ("posit2", "popain", "podth2", "posact"),
    "TROOP": ("bgsit1", "popain", "bgdth1", "bgact"),
    "SERGEANT": ("sgtsit", "dmpain", "sgtdth", "dmact"),
    "SHADOWS": ("sgtsit", "dmpain", "sgtdth", "dmact"),
    "BRUISER": ("brssit", "dmpain", "brsdth", "dmact"),
    "KNIGHT": ("brssit", "dmpain", "brsdth", "dmact"),
    "HEAD": ("cacsit", "dmpain", "cacdth", "dmact"),
    "SKULL": (None, "dmpain", "firxpl", "dmact"),
    "BARREL": (None, None, "barexp", None),
}


def decode_lump(data: bytes) -> bytes:
    """Raw 8-bit PCM out of a DS lump (8-byte DMX header)."""
    if len(data) < 8:
        raise ValueError("sound lump too short")
    _format, _rate, samples = int.from_bytes(data[0:2], "little"), \
        int.from_bytes(data[2:4], "little"), int.from_bytes(data[4:8],
                                                             "little")
    pcm = data[8:8 + samples]
    if len(pcm) != samples:
        raise ValueError("sound lump truncated")
    return bytes(pcm)


def upsample_sfx(pcm8: bytes) -> bytes:
    """DS lump (11025 Hz mono u8) to mixer spec (44100 stereo s16).

    Linear x4 in time (what SDL_ConvertAudioFormat does for
    chocolate), unsigned 8-bit to signed 16-bit, mono duplicated.
    """
    import numpy as np
    src = (np.frombuffer(bytes(pcm8), dtype=np.uint8).astype(np.float64)
           - 128.0) * 256.0
    n = len(src)
    if n == 0:
        return b""
    if n == 1:
        up = np.repeat(src, (SAMPLE_RATE // SFX_RATE))
    else:
        up = np.interp(np.arange((n - 1) * 4 + 1) / 4.0,
                       np.arange(n), src)
    s16 = np.clip(np.round(up), -32768, 32767).astype("<i2")
    return np.stack([s16, s16], axis=1).tobytes()


def attenuate(lx: int, ly: int, la_bam: int, sx: int,
              sy: int) -> tuple[float, float, float] | None:
    """Vanilla S_AdjustSoundParams: (vol01, left01, right01) or None
    when inaudible. Positions are fixed-point, angle is BAM."""
    dx = abs(lx - sx) >> 16
    dy = abs(ly - sy) >> 16
    # NOTE: vanilla fast approx distance, in map units.
    dist = dx + dy - (min(dx, dy) >> 1)
    if dist > CLIP_DIST:
        return None
    diff = (point_to_angle2(lx, ly, sx, sy) - (la_bam & 0xFFFFFFFF)) \
        & 0xFFFFFFFF
    sep = 128 - (STEREO_SWING * tables.finesine[diff >> 19] >> 16)
    if dist < CLOSE_DIST:
        vol = 1.0
    else:
        vol = (CLIP_DIST - dist) / (CLIP_DIST - CLOSE_DIST)
    if vol <= 0:
        return None
    pan = max(-1.0, min(1.0, (sep - 128) / 128))
    return vol, vol * (1 - max(0.0, pan)), vol * (1 - max(0.0, -pan))


class SoundEngine:
    """Mixer + cache + priority channels (s_sound.c channel logic)."""

    def __init__(self) -> None:
        self.wad = None
        self.mixer = None
        self.master = 1.0
        self.muted = False
        self.cache: dict[str, object | None] = {}
        self.slots: list = []  # (name, priority, channel)
        self.listener = (0, 0, 0)

    def init(self, wad, master: float = 1.0) -> bool:
        """Attach the WAD and bring up the mixer. False = silent mode.

        NOTE: pygame.init() pre-opens whatever the desktop wants;
        enforce CD quality whenever it mismatches.
        """
        self.wad = wad
        self.master = master
        if pygame is None:
            return False
        try:
            want = (SAMPLE_RATE, MIXER_SIZE, MIXER_CHANNELS)
            if tuple(pygame.mixer.get_init() or ()) != want:
                pygame.mixer.quit()
                pygame.mixer.pre_init(SAMPLE_RATE, MIXER_SIZE,
                                      MIXER_CHANNELS, 512)
                pygame.mixer.init()
            pygame.mixer.set_num_channels(N_CHANNELS)
        except Exception:
            self.mixer = None
            return False
        self.mixer = pygame.mixer
        self.slots = []
        return True

    def set_listener(self, x: int, y: int, angle_bam: int) -> None:
        self.listener = (x, y, angle_bam)

    def toggle_mute(self) -> bool:
        self.muted = not self.muted
        return self.muted

    def playing(self, names, origin=None) -> bool:
        """True while any of these sounds rings from origin (lets attack
        tails finish before the idle rev kicks back in)."""
        if isinstance(names, str):
            names = (names,)
        for entry in self.slots:
            if entry is None:
                continue
            name, _prio, ch, org = entry
            if name not in names:
                continue
            if origin is not None and org is not origin:
                continue
            try:
                if ch.get_busy():
                    return True
            except Exception:
                continue
        return False

    def sound(self, name: str):
        """Decoded mixer.Sound, cached (None when missing/silent)."""
        if name in self.cache:
            return self.cache[name]
        result = None
        if self.mixer is not None and self.wad is not None:
            try:
                pcm = decode_lump(self.wad.read_lump("DS" + name.upper()))
                result = self.mixer.Sound(buffer=upsample_sfx(pcm))
            except Exception:
                result = None
        self.cache[name] = result
        return result

    def play(self, name: str, x: int | None = None,
             y: int | None = None, origin=None) -> bool:
        """S_StartSound: positional (or full-volume UI) one-shot.

        Same origin restarts its channel instead of stacking (chainsaw
        revving, multi-pellet victims); when full, vanilla kicks the
        first slot at least as hot, quirks included.
        """
        if self.mixer is None or self.muted:
            return False
        spec = SFX.get(name)
        if spec is None:
            return False
        priority, _link = spec
        snd = self.sound(name)
        if snd is None:
            return False
        vol = left = right = 1.0
        if x is not None and y is not None:
            lx, ly, la = self.listener
            heard = attenuate(lx, ly, la, x, y)
            if heard is None:
                return False
            vol, left, right = heard  # NOTE: vanilla S_AdjustSoundParams
        left = min(max(left * self.master, 0.0), 1.0)
        right = min(max(right * self.master, 0.0), 1.0)
        if left <= 0 and right <= 0:
            return False
        channels = [self.mixer.Channel(i) for i in range(N_CHANNELS)]
        while len(self.slots) < N_CHANNELS:
            self.slots.append(None)
        pick = None
        for i, ch in enumerate(channels):
            entry = self.slots[i]
            if entry is None or not ch.get_busy():
                pick = i
                break
            if origin is not None and entry[3] is origin:
                ch.stop()
                pick = i
                break
        if pick is None:
            for i, ch in enumerate(channels):
                if self.slots[i][1] >= priority:
                    ch.stop()
                    pick = i
                    break
            if pick is None:
                return False  # NOTE: nothing kickable, sorry Charlie
        ch = channels[pick]
        self.slots[pick] = (name, priority, ch, origin)
        # NOTE: stereo mixer, so the two-arg pan lands (S_AdjustSound).
        ch.set_volume(left, right)
        ch.play(snd)
        return True

    def start_music(self, name: str) -> None:
        """Music stub (Linux reference: external MUS server, absent)."""
        _ = name
        return None


engine = SoundEngine()

verbose = False  # terminal chatter (viewer sets it from --debug)


def init(wad, master: float = 1.0) -> bool:
    return engine.init(wad, master)


def set_listener(x: int, y: int, angle_bam: int) -> None:
    engine.set_listener(x, y, angle_bam)


def play(name: str, x: int | None = None,
         y: int | None = None, origin=None) -> bool:
    return engine.play(name, x, y, origin)


def toggle_mute() -> bool:
    muted = engine.toggle_mute()
    music_sync_mute()
    return muted


def start_music(name: str) -> None:
    engine.start_music(name)


# -- streamed OPL music (oplmusic.py song thread + one mixer channel) --

_music_player = None
_music_channel = None
_music_wad = None
_music_main: list = []
_music_perc: list = []
_music_vol = 8  # options slider 0-15 (mus_vol finally does something)
music_log: list = []  # (lump, trigger) per song start, newest last


def music_init(wad) -> bool:
    """Parse GENMIDI, reserve a mixer channel, start the song thread."""
    global _music_player, _music_channel, _music_wad
    global _music_main, _music_perc
    from pydoom.genmidi import parse_genmidi
    from pydoom.oplmusic import MusicPlayer
    _music_wad = wad
    try:
        _music_main, _music_perc = parse_genmidi(wad.read_lump("GENMIDI"))
    except Exception:
        return False
    _music_player = MusicPlayer()
    _music_player.set_volume(_music_vol * 127 // 15)
    _music_player.set_muted(engine.muted)
    if engine.mixer is not None:
        try:
            engine.mixer.set_num_channels(N_CHANNELS + 1)
            _music_channel = engine.mixer.Channel(N_CHANNELS)
        except Exception:
            _music_channel = None
    return True


def music_play(lump_name: str, trigger: str = "") -> bool:
    """Loop a D_ lump (map songs, title, intermission, idmus).

    Every start lands in music_log (and stdout) so a glance proves
    which song the game picked and why.
    """
    if _music_player is None or _music_wad is None:
        return False
    try:
        data = bytes(_music_wad.read_lump(lump_name))
    except Exception:
        return False
    _music_player.play_song(data, _music_main, _music_perc,
                            _music_vol * 127 // 15)
    music_log.append((lump_name, trigger))
    if verbose:
        print(f"music: {lump_name} [{trigger}]")
    return True


def music_stop() -> None:
    if _music_player is not None:
        _music_player.stop()


def _feed_action(channel: str) -> str:
    """play (idle), queue (one max) or skip: the backlog cap, unit
    tested with a fake channel (no mixer needed)."""
    try:
        busy = channel.get_busy()
    except Exception:
        return "skip"
    if not busy:
        return "play"
    try:
        if channel.get_queue() is not None:
            return "skip"  # NOTE: channel fed; backpressure parks worker
    except Exception:
        return "skip"
    return "queue"


def music_pump() -> None:
    """Feed the music channel (main-thread only, never blocks).

    Idle channels get play(), busy ones at most one queued chunk:
    the backlog never grows, so song switches land tight.
    """
    if _music_player is None or _music_channel is None:
        return
    action = _feed_action(_music_channel)
    if action == "skip":
        return
    chunk = _music_player.pump()
    if chunk is None:
        return
    try:
        import pygame
        sound = pygame.mixer.Sound(buffer=chunk)
        if action == "queue":
            _music_channel.queue(sound)
        else:
            _music_channel.play(sound)
    except Exception:
        pass


def music_set_volume(index: int) -> None:
    """Options slider 0-15 straight into the OPL voice math."""
    global _music_vol
    index = max(0, min(15, index))
    if index == _music_vol:
        return
    _music_vol = index
    if _music_player is not None:
        _music_player.set_volume(index * 127 // 15)


def music_sync_mute() -> None:
    if _music_player is not None:
        _music_player.set_muted(engine.muted)


def music_status() -> dict:
    """Song-thread readout for the debug HUD (backend, queue depth,
    pumped/starved counters, worker health)."""
    player = _music_player
    if player is None:
        return {"backend": "none"}
    try:
        depth = player._out.qsize()
    except Exception:
        depth = -1
    return {"backend": type(player.backend).__name__,
            "queue": depth,
            "pumped": player.pumped,
            "starved": player.starved,
            "alive": player._thread.is_alive(),
            "error": repr(player.error) if player.error else None}


def music_shutdown() -> None:
    global _music_player, _music_channel
    if _music_player is not None:
        _music_player.close()
        _music_player = None
    _music_channel = None
