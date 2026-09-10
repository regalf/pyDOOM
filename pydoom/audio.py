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

SAMPLE_RATE = 11025
# NOTE: DS data is UNSIGNED 8-bit (128 = silence); size +8 requests
# AUDIO_U8. Signed (-8) turns silence into full-scale DC: harsh noise.
MIXER_SIZE = 8
N_CHANNELS = 8
CLIP_DIST = 1200  # map units, vanilla S_CLIPPING_DIST
CLOSE_DIST = 160  # map units, vanilla S_CLOSE_DIST
STEREO_SWING = 96  # vanilla S_STEREO_SWING

# NOTE: (priority, link-singleton) from sounds.c for the sounds we use.
SFX = {
    "pistol": (64, False), "shotgn": (64, False), "rlaunc": (64, False),
    "firsht": (70, False), "claw": (70, False),
    "doropn": (100, False), "dorcls": (100, False),
    "stnmov": (119, False), "pstop": (100, False),
    "swtchn": (78, False), "swtchx": (78, False),
    "plpain": (96, False), "dmpain": (96, False), "popain": (96, False),
    "itemup": (78, True), "getpow": (60, False),
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

        NOTE: pygame.init() pre-opens the mixer at CD quality, which
        would misread our 11025 Hz 8-bit mono lumps (4x chipmunk
        bursts); enforce our spec whenever it mismatches.
        """
        self.wad = wad
        self.master = master
        if pygame is None:
            return False
        try:
            want = (SAMPLE_RATE, MIXER_SIZE, 1)
            if tuple(pygame.mixer.get_init() or ()) != want:
                pygame.mixer.quit()
                pygame.mixer.pre_init(SAMPLE_RATE, MIXER_SIZE, 1, 512)
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

    def sound(self, name: str):
        """Decoded mixer.Sound, cached (None when missing/silent)."""
        if name in self.cache:
            return self.cache[name]
        result = None
        if self.mixer is not None and self.wad is not None:
            try:
                pcm = decode_lump(self.wad.read_lump("DS" + name.upper()))
                result = self.mixer.Sound(buffer=pcm)
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
            vol, left, right = heard
        vol *= self.master
        if vol <= 0:
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
        ch.set_volume(left * vol, right * vol)
        ch.play(snd)
        return True

    def start_music(self, name: str) -> None:
        """Music stub (Linux reference: external MUS server, absent)."""
        _ = name
        return None


engine = SoundEngine()


def init(wad, master: float = 1.0) -> bool:
    return engine.init(wad, master)


def set_listener(x: int, y: int, angle_bam: int) -> None:
    engine.set_listener(x, y, angle_bam)


def play(name: str, x: int | None = None,
         y: int | None = None, origin=None) -> bool:
    return engine.play(name, x, y, origin)


def toggle_mute() -> bool:
    return engine.toggle_mute()


def start_music(name: str) -> None:
    engine.start_music(name)
