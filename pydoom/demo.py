"""Vanilla demo streams (g_game.c record/playback): 13-byte header,
4-byte ticcmd packets, DEMOMARKER footer.

Single-player only (deathmatch/multiplayer headers are refused): the
stream is 35 Hz Ticcmds from level start to the footer, spanning level
transitions without reseeding (M_ClearRandom runs once, in G_InitNew).
"""
from __future__ import annotations

from dataclasses import dataclass

from pydoom.ticcmd import Ticcmd

# NOTE: linuxdoom-1.10 gameversion (G_DoPlayDemo refuses the rest).
VERSION = 109
# NOTE: skill_t order (sk_baby..sk_nightmare); G_InitNew clamps >4.
SKILL_NAMES = ("baby", "easy", "normal", "hard", "nightmare")
DEMOMARKER = 0x80
HEADER_LEN = 13


@dataclass
class DemoHeader:
    """Raw header bytes (G_BeginRecording layout)."""

    skill: int = 2
    episode: int = 1
    map: int = 1
    deathmatch: int = 0
    respawn: int = 0
    fast: int = 0
    nomonsters: int = 0
    consoleplayer: int = 0
    players: tuple = (1, 0, 0, 0)

    def to_bytes(self) -> bytes:
        return bytes((VERSION, self.skill & 0xFF, self.episode & 0xFF,
                      self.map & 0xFF, self.deathmatch & 0xFF,
                      self.respawn & 0xFF, self.fast & 0xFF,
                      self.nomonsters & 0xFF, self.consoleplayer & 0xFF,
                      *[p & 0xFF for p in self.players[:4]]))

    @staticmethod
    def from_bytes(data: bytes) -> "DemoHeader":
        if len(data) < HEADER_LEN or data[0] != VERSION:
            raise ValueError("not a version-109 demo")
        return DemoHeader(skill=data[1], episode=data[2], map=data[3],
                          deathmatch=data[4], respawn=data[5],
                          fast=data[6], nomonsters=data[7],
                          consoleplayer=data[8],
                          players=tuple(data[9:13]))

    def skill_name(self) -> str:
        """Byte to skill (vanilla clamps >nightmare down to it)."""
        return SKILL_NAMES[min(self.skill, 4)]

    def marker(self, mission: str = "shareware") -> str:
        """Start map (vanilla forces episode 1 on shareware, clamps)."""
        from pydoom.mission import episode_count
        max_ep = max(1, episode_count(mission))
        ep = min(max(self.episode, 1), max_ep)
        return f"E{ep}M{min(max(self.map, 1), 9)}"

    def single_player(self) -> bool:
        """Refuse deathmatch/multiplayer streams (engine is 1P)."""
        return (self.deathmatch == 0 and self.consoleplayer == 0
                and tuple(self.players[:4]) == (1, 0, 0, 0))


class DemoReader:
    """Forward stream reader (G_ReadDemoTiccmd)."""

    def __init__(self, data: bytes) -> None:
        self.header = DemoHeader.from_bytes(data)
        self.pos = HEADER_LEN
        self.data = data
        self.tics = 0  # packets consumed (desync reports)

    def read_cmd(self) -> Ticcmd | None:
        """Next packet, or None at DEMOMARKER/end of buffer."""
        if self.pos >= len(self.data) or self.data[self.pos] == DEMOMARKER:
            return None
        if self.pos + 4 > len(self.data):
            return None  # NOTE: truncated tail ends the demo, no crash
        cmd = Ticcmd.unpack(self.data[self.pos:self.pos + 4])
        self.pos += 4
        self.tics += 1
        return cmd


class DemoWriter:
    """Stream recorder (G_BeginRecording/G_WriteDemoTiccmd)."""

    def __init__(self, header: DemoHeader) -> None:
        self.header = header
        self.body = bytearray()
        self.tics = 0

    def append(self, cmd: Ticcmd) -> None:
        self.body += cmd.pack()
        self.tics += 1

    def finish(self) -> bytes:
        """Header + stream + DEMOMARKER (G_CheckDemoStatus tail)."""
        return self.header.to_bytes() + bytes(self.body) + bytes((DEMOMARKER,))
