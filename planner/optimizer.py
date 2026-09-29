"""Minimum-cost fuel purchases along a fixed route.

Units are integer tenths of a mile: positions along the route and fuel in the
tank (as the range it provides). At 10 mpg one tenth of a mile is 0.01 gal.
"""

from dataclasses import dataclass
from decimal import Decimal

import numpy as np

TANK_TENTHS = 5000  # 500 miles
_PRICE_SCALE = 10**8  # prices carry up to 8 decimals in the source data
_STOP_WEIGHT = 1024  # key = cost * weight + stops: cheapest first, then fewest stops
_INF = np.iinfo(np.int64).max // 4


@dataclass(frozen=True)
class Candidate:
    key: int
    position: int
    price: Decimal


@dataclass(frozen=True)
class Purchase:
    candidate: Candidate
    fuel: int  # pumped into the tank here, never more than a full tank
    reserve: int = 0  # reserve used to reach the first station, repaid here


class RangeGapError(Exception):
    """No station reachable between two points of the route."""

    def __init__(self, from_position, to_position):
        super().__init__(f"gap from {from_position} to {to_position}")
        self.from_position = from_position
        self.to_position = to_position


def plan_purchases(candidates, length, start_fuel=0, tank=TANK_TENTHS, min_fill=0):
    """Return the purchases that minimise total cost, then the number of stops.

    Exact dynamic programme over fuel levels: for each station in route order it
    keeps the best (cost, stops) for every fuel level on arrival. A stop pumps
    nothing, at least `min_fill`, or whatever fills the tank; a smaller purchase is also
    allowed when it is exactly what reaches the destination. A truck that cannot reach
    the first station on its start fuel runs on reserve and repays it there, on top of that stop's fill.
    """
    start_fuel = min(start_fuel, tank)
    if start_fuel >= length:
        return []
    ordered = sorted(
        (c for c in candidates if 0 <= c.position <= length), key=lambda c: (c.position, c.price)
    )
    # Stations share city-centroid positions; a dearer one at the same spot is never the better buy.
    path = [c for n, c in enumerate(ordered) if n == 0 or c.position != ordered[n - 1].position]
    _check_gaps(path, length, tank)

    reserve = max(0, path[0].position - start_fuel)
    levels = np.arange(tank + 1, dtype=np.int64)
    arrive = np.full(tank + 1, _INF, dtype=np.int64)
    arrive[start_fuel + reserve - path[0].position] = 0
    sources = []  # per station: arrival fuel behind each leaving level (-1 = none bought)
    best_finish = (_INF, None, None)  # key, station index, arrival fuel of a final small top-up

    for i, station in enumerate(path):
        price = int(station.price * _PRICE_SCALE)
        forced = i == 0 and reserve > 0
        if forced:  # the reserve stop happens anyway: count it once, charge the reserve here
            arrive = np.where(arrive < _INF, arrive + reserve * price * _STOP_WEIGHT + 1, _INF)
        stop = 0 if forced else 1

        need = length - station.position  # fuel that reaches the destination from here
        if need <= tank:
            small = (arrive < _INF) & (levels < need) & (need - levels < min_fill)
            if small.any():
                keys = np.where(small, arrive + (need - levels) * price * _STOP_WEIGHT + stop, _INF)
                f = int(keys.argmin())
                if keys[f] < best_finish[0]:
                    best_finish = (int(keys[f]), i, f)

        leave, source = arrive.copy(), np.full(tank + 1, -1, dtype=np.int64)
        if min_fill <= tank:
            base = np.where(arrive < _INF, arrive - levels * price * _STOP_WEIGHT, _INF)
            run_min = np.minimum.accumulate(base)
            run_arg = np.maximum.accumulate(np.where(base == run_min, levels, 0))
            g = levels[min_fill:]
            ok = run_min[: len(g)] < _INF
            bought = np.where(ok, run_min[: len(g)] + g * price * _STOP_WEIGHT + stop, _INF)
            better = bought < leave[min_fill:]
            leave[min_fill:] = np.where(better, bought, leave[min_fill:])
            source[min_fill:] = np.where(better, run_arg[: len(g)], -1)
        # A tank too full to take the minimum may still be topped up to full.
        if 0 < min_fill:
            topup = np.where(arrive < _INF, arrive + (tank - levels) * price * _STOP_WEIGHT + stop, _INF)
            topup[tank] = _INF  # arriving full buys nothing
            f = int(topup.argmin())
            if topup[f] < leave[tank]:
                leave[tank] = topup[f]
                source[tank] = f
        sources.append(source)

        nxt = path[i + 1].position if i + 1 < len(path) else length
        gap = nxt - station.position
        arrive = np.full(tank + 1, _INF, dtype=np.int64)
        arrive[: tank + 1 - gap] = leave[gap:]
        if i + 1 == len(path):
            end_key = int(arrive.min())
            end_fuel = int(arrive.argmin())
        elif not (arrive < _INF).any() and best_finish[1] is None:
            raise RangeGapError(station.position, nxt)

    if min(end_key, best_finish[0]) >= _INF:
        raise RangeGapError(path[-1].position, length)
    return _trace(path, sources, reserve, length, end_key, end_fuel, best_finish)


def _check_gaps(path, length, tank):
    """Clear error for the common case: two neighbouring stops further apart than a full tank."""
    points = [0] + [c.position for c in path] + [length]
    if not path or path[0].position > tank:
        raise RangeGapError(0, points[1])
    for a, b in zip(points[1:], points[2:], strict=False):
        if b - a > tank:
            raise RangeGapError(a, b)


def _trace(path, sources, reserve, length, end_key, end_fuel, best_finish):
    """Walk the recorded choices back from the cheapest way to finish."""
    pumped = {}
    if best_finish[0] < end_key:
        _, last, fuel = best_finish
        pumped[last] = length - path[last].position - fuel
        level = fuel  # arrival fuel at `last`
        i = last - 1
    else:
        level = end_fuel + (length - path[-1].position)  # fuel on leaving the last station
        i = len(path) - 1
        level = _undo_purchase(sources, i, level, pumped)
        i -= 1
    while i >= 0:
        level += path[i + 1].position - path[i].position  # fuel on leaving station i
        level = _undo_purchase(sources, i, level, pumped)
        i -= 1
    if reserve:
        pumped.setdefault(0, 0)
    return [Purchase(path[i], pumped[i], reserve if i == 0 else 0) for i in sorted(pumped)]


def _undo_purchase(sources, i, level, pumped):
    """Given fuel on leaving station i, record what was bought there and return fuel on arrival."""
    arrival = int(sources[i][level])
    if arrival < 0:
        return level
    pumped[i] = level - arrival
    return arrival
