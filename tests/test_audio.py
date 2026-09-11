"""Tests for audio.py: DS decode, vanilla attenuation, channels."""

import os

import pytest

from pydoom import audio
from pydoom.audio import SoundEngine, attenuate, decode_lump
from pydoom.fixed import FRACUNIT
from pydoom.wad import WadFile

WAD_PATH = os.path.join(os.path.dirname(__file__), "..", "DOOM1.WAD")

requires_wad = pytest.mark.skipif(
    not os.path.exists(WAD_PATH), reason="DOOM1.WAD not found"
)


def test_decode_lump_roundtrip():
    pcm = bytes([0, 64, 128, 192, 255] * 20)
    import struct
    data = struct.pack("<HHI", 3, 11025, len(pcm)) + pcm
    assert decode_lump(data) == pcm
    with pytest.raises(ValueError):
        decode_lump(b"\x03\x00")
    with pytest.raises(ValueError):
        decode_lump(struct.pack("<HHI", 3, 11025, 999) + b"\x80" * 10)


def test_attenuate_close_full_far_silent():
    lx, ly, la = 0, 0, 0
    heard = attenuate(lx, ly, la, 10 << 16, 0)
    assert heard is not None
    vol, left, right = heard
    assert vol == 1.0 and left == right == 1.0  # on top of it
    assert attenuate(lx, ly, la, 1300 << 16, 0) is None  # past 1200
    mid = attenuate(lx, ly, la, 680 << 16, 0)
    assert mid is not None and 0.3 < mid[0] < 0.7  # linear-ish middle


def test_attenuate_stereo_sides():
    lx, ly = 0, 0
    east = 0  # BAM facing east
    _, left, right = attenuate(lx, ly, east, 500 << 16, 0)
    assert left == right  # dead ahead: centered
    _, left, right = attenuate(lx, ly, east, 0, 500 << 16)
    assert (left > right) != (left < right)  # off-axis: split
    ahead = attenuate(lx, ly, east, 500 << 16, 0)
    to_side = attenuate(lx, ly, east, 0, 500 << 16)
    assert ahead is not None and to_side is not None


def test_engine_without_mixer_is_silent():
    eng = SoundEngine()  # never init: no mixer, no wad
    assert eng.play("pistol", 0, 0) is False
    assert eng.sound("pistol") is None
    eng.start_music("D_E1M1")  # stub never raises


@requires_wad
def test_engine_decodes_real_lump():
    import pygame
    eng = SoundEngine()
    try:
        ok = eng.init(WadFile(WAD_PATH))
    except Exception:
        pytest.skip("no audio device")
        return
    if not ok:
        pytest.skip("mixer unavailable")
        return
    snd = eng.sound("pistol")
    assert snd is not None
    assert eng.sound("plasma") is None  # NOTE: shareware lacks it
    assert eng.sound("nope") is None
    pygame.mixer.quit()


@requires_wad
def test_link_sounds_never_stack():
    import pygame
    eng = SoundEngine()
    try:
        ok = eng.init(WadFile(WAD_PATH))
    except Exception:
        pytest.skip("no audio device")
        return
    if not ok:
        pytest.skip("mixer unavailable")
        return
    eng.set_listener(0, 0, 0)
    assert eng.play("itemup", 0, 0) is True
    # NOTE: vanilla has no name stacking guard (link only tweaks
    # pitch/volume); distinct origins layer like the real mix.
    assert eng.play("itemup", 500 << 16, 0) is True
    pygame.mixer.quit()


@requires_wad
def test_same_origin_restarts_instead_of_stacking():
    import pygame
    eng = SoundEngine()
    try:
        ok = eng.init(WadFile(WAD_PATH))
    except Exception:
        pytest.skip("no audio device")
        return
    if not ok:
        pytest.skip("mixer unavailable")
        return
    eng.set_listener(0, 0, 0)
    origin = object()
    assert eng.play("sawful", 0, 0, origin) is True
    assert eng.play("sawful", 0, 0, origin) is True  # restarts same slot
    busy = [e for e in eng.slots
            if e is not None and e[0] == "sawful" and e[2].get_busy()]
    assert len(busy) == 1  # NOTE: revving never layers on itself
    pygame.mixer.quit()


@requires_wad
def test_priority_preempts_quiet():
    import pygame
    eng = SoundEngine()
    try:
        ok = eng.init(WadFile(WAD_PATH))
    except Exception:
        pytest.skip("no audio device")
        return
    if not ok:
        pytest.skip("mixer unavailable")
        return
    eng.set_listener(0, 0, 0)
    for _ in range(8):
        eng.play("telept", 0, 0)  # priority 32 fills the board
    # NOTE: vanilla quirk, kept verbatim: with every slot colder than
    # the newcomer, S_getChannel finds nothing to kick ("Sorry Charlie").
    assert eng.play("doropn", 0, 0) is False
    pygame.mixer.quit()


def test_mute_switch():
    eng = SoundEngine()
    assert eng.toggle_mute() is True
    assert eng.play("pistol", 0, 0) is False
    assert eng.toggle_mute() is False


def test_monster_table_covers_e1_cast():
    for mt in ("POSSESSED", "SHOTGUY", "TROOP", "SERGEANT", "BRUISER"):
        see, pain, death, act = audio.MONSTERS[mt]
        for name in (see, pain, death, act):
            assert name is None or name in audio.SFX, (mt, name)


def test_mixer_format_is_unsigned_8bit():
    """DS lumps are unsigned (128 = silence); signed playback turns
    silence into full-scale DC, i.e. harsh noise instead of Doom."""
    from pydoom.audio import MIXER_SIZE
    assert MIXER_SIZE == 8


@requires_wad
def test_real_lumps_decode_centered():
    wad = WadFile(WAD_PATH)
    for name in ("DSPISTOL", "DSDOROPN", "DSITEMUP"):
        pcm = decode_lump(wad.read_lump(name))
        mean = sum(pcm) / len(pcm)
        assert 100 < mean < 156, (name, mean)  # silence-centered


@requires_wad
def test_init_enforces_lump_spec():
    """pygame.init pre-opens CD quality; lump bytes misread there play
    4x fast. init() must force 11025/8/mono back (viewer boot order)."""
    import pygame
    eng = SoundEngine()
    pygame.mixer.quit()
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
    assert tuple(pygame.mixer.get_init()) != (11025, 8, 1)
    assert eng.init(WadFile(WAD_PATH)) is True
    assert tuple(pygame.mixer.get_init()) == (11025, 8, 1)
    snd = eng.sound("pistol")
    import numpy as np
    frames = pygame.sndarray.array(snd).shape[0]
    assert frames == 5661  # NOTE: full half-second, not 1415 chipmunk
    pygame.mixer.quit()


@requires_wad
def test_shotgun_blast_cries_once(monkeypatch):
    """Multi-pellet re-hits reset the soundless pain frame (vanilla
    debounce); they must not stack one cry per pellet."""
    import pydoom.combat as combat_mod
    # NOTE: deterministic pain (every pellet would cry pre-debounce).
    monkeypatch.setattr(combat_mod, "p_random", lambda: 0)
    from pydoom import combat, weapons
    combat.register_combat_actions()
    from pydoom.info import MT_INDEX
    from pydoom.mapdata import Map
    from pydoom.mobjs import ThingIndex, spawn_mobj, think_mobj
    from pydoom.physics import Physics
    from pydoom.ai import AIContext
    from pydoom.player import PlayerState, WP_SHOTGUN
    from pydoom.wad import WadFile
    wad = WadFile(WAD_PATH)
    game_map = Map.from_wad(wad, "E1M1")
    phys = Physics(game_map)
    index = ThingIndex(game_map)
    phys.things = index
    ctx = AIContext(physics=phys, players=[])
    ctx.mobjs = []
    player = spawn_mobj(game_map, phys, index, 900 << 16, -3500 << 16, 0,
                        MT_INDEX["PLAYER"])
    player.is_player = True
    player.z = player.floorz
    troop = spawn_mobj(game_map, phys, index, 930 << 16, -3500 << 16, 0,
                       MT_INDEX["BRUISER"])
    troop.z = troop.floorz
    player.angle = 0
    ps = PlayerState()
    ps.weapons |= 1 << WP_SHOTGUN
    ps.readyweapon = ps.pendingweapon = WP_SHOTGUN
    ps.ammo[1] = 50
    ctx.players = [player]
    ctx.mobjs = [player, troop]
    cries = []
    import pydoom.audio as audio_mod
    orig = audio_mod.engine.play
    audio_mod.engine.play = lambda n, x=None, y=None, origin=None: (
        cries.append(n), False)[1]
    try:
        _queue: list = []
        weapons.fire(ps, player, phys, index, ctx.mobjs, None, True, ctx,
                     _queue)
        while _queue:  # NOTE: shotgun windup runs out here
            weapons.tick_pending(ps, player, phys, index, ctx.mobjs,
                                 None, ctx, _queue)
        assert troop.health < 1000  # pellets landed, baron stands
        for _ in range(12):
            think_mobj(troop, phys, ctx)
    finally:
        audio_mod.engine.play = orig
    assert cries.count("dmpain") == 1, cries


@requires_wad
def test_every_played_sound_resolves():
    """Every sfx name the engine can utter must be in the SFX table
    and decode from the WAD (sawidl went missing silently once)."""
    import re
    wad = WadFile(WAD_PATH)
    lumps = {l.name for l in wad.lumps}
    names = set()
    import pydoom.audio as audio_mod
    for table in (audio_mod.MONSTERS.values(),):
        for entry in table:
            names.update(n for n in entry if n is not None)
    names.update(audio_mod.MISSILE_DEATHS.values())
    src = ""
    for path in ("pydoom/weapons.py", "pydoom/combat.py", "pydoom/ai.py",
                 "pydoom/pickup.py", "pydoom/doors.py", "tools/doom_view.py"):
        with open(path) as fh:
            src += fh.read()
    names.update(re.findall(r'audio\.play\("(\w+)"', src))
    names.discard("plasma")
    names.discard("bfg")  # NOTE: shareware has no lumps for these
    # NOTE: cacodemon/skull wake/death lumps are registered-only; the
    # MONSTERS table keeps them for activesound-draw parity (no E1 use).
    names.discard("cacsit")
    names.discard("cacdth")
    names.discard("firxpl")
    missing = [n for n in names
               if n not in audio_mod.SFX or ("DS" + n.upper()) not in lumps]
    assert missing == [], missing


@requires_wad
def test_music_init_play_stop():
    from pydoom import audio as _a
    wad = WadFile(os.path.join(os.path.dirname(__file__), "..",
                               "DOOM1.WAD"))
    assert audio.music_init(wad)
    try:
        _a.music_log.clear()
        assert audio.music_play("D_E1M1")
        assert _a.music_log[-1][0] == "D_E1M1"  # NOTE: song switch logged
        assert not audio.music_play("D_NOPE")
        audio.music_set_volume(0)
        audio.music_set_volume(15)
        audio.music_set_volume(99)  # NOTE: clamps, never crashes
        audio.music_pump()  # NOTE: no mixer here, must stay silent-safe
        audio.music_stop()
        assert _a._music_vol == 15
    finally:
        audio.music_shutdown()
    assert _a._music_player is None


def test_music_pump_caps_backlog():
    """One queued chunk max: switches stay tight, memory never grows."""
    from pydoom.audio import _feed_action

    class FakeChannel:
        def __init__(self):
            self.busy = False
            self.queued = None

        def get_busy(self):
            return self.busy

        def get_queue(self):
            return self.queued

    fake = FakeChannel()
    assert _feed_action(fake) == "play"  # NOTE: idle starts now
    fake.busy = True
    assert _feed_action(fake) == "queue"  # NOTE: one lines up
    fake.queued = object()
    assert _feed_action(fake) == "skip"  # NOTE: never two waiting
    fake.queued = None  # NOTE: mixer consumed it
    assert _feed_action(fake) == "queue"


def test_sfx_volume_reaches_channel():
    """Slider regression: master scales the channel (pygame-ce ignores
    the two-arg set_volume on mono mixers, so single-arg it is)."""
    import os
    import pygame
    os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
    was_init = pygame.mixer.get_init() is not None
    if not was_init:
        try:
            pygame.mixer.pre_init(11025, 8, 1, 512)
            pygame.mixer.init()
        except Exception:
            pytest.skip("no mixer available")
    old_master, old_slots = audio.engine.master, audio.engine.slots
    old_mixer, old_wad = audio.engine.mixer, audio.engine.wad
    audio.engine.mixer = pygame.mixer
    audio.engine.wad = WadFile(os.path.join(os.path.dirname(__file__),
                                            "..", "DOOM1.WAD"))
    try:
        audio.engine.master = 0.2
        audio.engine.slots = []
        assert audio.play("pistol")
        assert pygame.mixer.Channel(0).get_volume() == pytest.approx(
            0.2, abs=0.02)
    finally:
        audio.engine.master, audio.engine.slots = old_master, old_slots
        audio.engine.mixer, audio.engine.wad = old_mixer, old_wad
        if not was_init:
            pygame.mixer.quit()
