"""Travel speed as a function of slope.

These are HAND-SET PRIORS, a first guess to be replaced by models fitted on
your own GPS traces. Everything a model needs to change is in the profiles.

Conventions: `grade` is rise/run (0.05 = 5 % uphill, -0.05 = 5 % downhill).
Flat speeds are *average urban* speeds: they already include traffic lights,
crossings and the time to get going, which is why they look low.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class BikeProfile:
    name: str
    flat_kmh: float     # average speed on the flat, in town
    uphill_k: float     # relative slowdown per % of climb
    downhill_k: float   # relative speed-up per % of descent
    max_kmh: float      # cap (braking, safety, e-assist cut-off at 25 km/h)

    def speed_kmh(self, grade: float) -> float:
        pct = grade * 100
        if pct >= 0:
            return self.flat_kmh / (1 + self.uphill_k * pct)
        return min(self.flat_kmh * (1 + self.downhill_k * -pct), self.max_kmh)


# A mechanical Vélib weighs ~20 kg and has 3 gears: climbs hurt a lot.
MECHANICAL = BikeProfile("velib_mecanique", flat_kmh=14.0, uphill_k=0.20,
                         downhill_k=0.06, max_kmh=27.0)
# Electric assistance flattens climbs; the motor stops helping at 25 km/h.
EBIKE = BikeProfile("velib_electrique", flat_kmh=17.0, uphill_k=0.04,
                    downhill_k=0.03, max_kmh=26.0)


@dataclass(frozen=True)
class WalkProfile:
    """Tobler's hiking function, rescaled so that the flat speed is `flat_kmh`."""
    name: str = "marche"
    flat_kmh: float = 4.8

    def speed_kmh(self, grade: float) -> float:
        tobler = math.exp(-3.5 * abs(grade + 0.05))
        return self.flat_kmh * tobler / math.exp(-3.5 * 0.05)

    @property
    def max_kmh(self) -> float:
        return self.speed_kmh(-0.05)


WALK = WalkProfile()


def edge_seconds(length_m: float, grade: float, profile) -> float:
    return length_m / (profile.speed_kmh(grade) / 3.6)
