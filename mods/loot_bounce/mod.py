"""loot_bounce: drops spawn mid-air and bounce on the floor.

on_kill places the drop at the engine-suggested mid-victim height and
gives it an upward momz kick. From there the ENGINE flies the arc
(_z_movement runs in the thinkers walk, before post_tic): the mod only
re-kicks on landing for a couple of damped bounces, then lets go.
Purely cosmetic: the pickup sweep is XY-only, so z never changes
what/when you grab. Grabbed/invalid drops leave the list on their own.
"""

from pydoom.ext import Mod

KICK = 5 * 65536  # upward launch (fixed/tic, engine units)
BOUNCE = 0.45  # bounce damping
REST = 65536  # re-kicks below this are done


class LootBounce(Mod):
    id = "loot_bounce"
    version = "0.2.0"
    description = "Dropped loot launches up and bounces down"

    def __init__(self):
        self.flying: list = []  # [(mo, kick)]

    def on_enable(self, api):
        self.flying = []
        api.on("on_kill", self._pop, priority=0)
        api.on("post_tic", self._step, priority=0)

    def on_disable(self):
        self.flying = []

    def _pop(self, ev):
        drop = ev.drop
        if drop is None or getattr(drop, "dead", False):
            return
        # NOTE: mid-air start (older engines omit fall_from: launch from
        # the ground instead of guessing a height).
        start = getattr(ev, "fall_from", None)
        drop.z = max(drop.floorz, start) if start is not None \
            else drop.floorz
        drop.momz = KICK
        self.flying.append([drop, KICK])

    def _step(self, ev):
        alive = []
        for drop, kick in self.flying:
            if getattr(drop, "dead", False):
                continue  # grabbed mid-air: stop
            if drop.z == drop.floorz and drop.momz == 0:
                # NOTE: the engine just landed it: one damped re-kick,
                # or let go once the kick decays past REST.
                kick = int(kick * BOUNCE)
                if kick < REST:
                    continue
                drop.momz = kick
            alive.append([drop, kick])
        self.flying = alive


MOD = LootBounce()
