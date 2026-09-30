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
    detour: int = 0  # one way, route to pump; a stop drives it twice


@dataclass(frozen=True)
class Purchase:
    candidate: Candidate
    fuel: int  # pumped into the tank here, never more than a full tank
    reserve: int = 0  # reserve used to reach the first pump, repaid here


class RangeGapError(Exception):
    """No station reachable between two points of the route."""

    def __init__(self, from_position, to_position):
        super().__init__(f"gap from {from_position} to {to_position}")
        self.from_position = from_position
        self.to_position = to_position


@dataclass(frozen=True)
class _Stop:
    """One station's numbers for the programme; `penalty` is its stop and detour cost as a key step."""

    price: int
    detour: int
    need: int  # route fuel from here to the destination
    penalty: int
    reserve: int  # > 0: the truck may reach this pump only on reserve, and then must stop here


def plan_purchases(
    candidates,
    length,
    start_fuel=0,
    tank=TANK_TENTHS,
    min_fill=0,
    stop_cost=Decimal(0),
    detour_cost=Decimal(0),
):
    """Return the purchases that minimise total cost plus penalties, then the number of stops.

    Each stop costs `stop_cost` plus `detour_cost` per unit of detour driven (to the pump and back),
    both in units of price x fuel; 0 means pure purchase cost.

    Exact dynamic programme over fuel levels: for each station in route order it keeps the best
    (cost, stops) for every fuel level on arrival at its exit. A stop drives `detour` to the pump
    (arrival fuel must cover it), buys there (the tank cap applies at the pump) and drives `detour`
    back. It buys at least `min_fill`, or whatever fills the tank; a smaller purchase is also allowed
    when it is exactly what gets from the pump to the destination. A truck that reaches no pump on
    its start fuel runs on reserve to one at the first route position and repays it there (detour
    included), on top of that stop's fill.
    """
    start_fuel = min(start_fuel, tank)
    if start_fuel >= length:
        return []
    path = _path(candidates, length)
    _check_gaps(path, length, tank)

    first = path[0].position
    on_reserve = start_fuel < min(c.position + c.detour for c in path)
    levels = np.arange(tank + 1, dtype=np.int64)
    arrive = np.full(tank + 1, _INF, dtype=np.int64)
    if not on_reserve:
        arrive[start_fuel - first] = 0
    stops = [
        _Stop(
            price=int(c.price * _PRICE_SCALE),
            detour=c.detour,
            need=length - c.position,
            # One stop adds `penalty` to a key. Keys stay far below _INF (~2.3e18): the largest
            # realistic cost key is ~6e16 and a stop adds ~1e15 at most ($18 plus a 50-mile detour).
            penalty=int((stop_cost + 2 * c.detour * detour_cost) * _PRICE_SCALE) * _STOP_WEIGHT + 1,
            reserve=first + c.detour - start_fuel
            if on_reserve and c.position == first and first + c.detour <= tank
            else 0,
        )
        for c in path
    ]
    sources = []  # per station: arrival fuel behind each leaving level (-1 none bought, -2 reserve stop)
    end_key, end_fuel = _INF, 0  # set by the last station; path is never empty
    best_finish = (_INF, None, None)  # key, station index, arrival fuel (-2 = reserve stop) of a final top-up

    for i, stop in enumerate(stops):
        leave, source, finish = _station(arrive, levels, stop, tank, min_fill)
        if finish[0] < best_finish[0]:
            best_finish = (finish[0], i, finish[1])
        sources.append(source)
        nxt = path[i + 1].position if i + 1 < len(path) else length
        gap = nxt - path[i].position
        arrive = np.full(tank + 1, _INF, dtype=np.int64)
        arrive[: tank + 1 - gap] = leave[gap:]
        if i + 1 == len(path):
            end_key = int(arrive.min())
            end_fuel = int(arrive.argmin())
        elif gap > 0 and not (arrive < _INF).any() and best_finish[1] is None:
            raise RangeGapError(path[i].position, nxt)

    if min(end_key, best_finish[0]) >= _INF:
        raise RangeGapError(path[-1].position, length)
    return _trace(path, stops, sources, length, (end_key, end_fuel), best_finish)


def _path(candidates, length):
    """Stations on the route in order, minus any that another at the same spot beats on price and detour."""
    ordered = sorted(
        (c for c in candidates if 0 <= c.position <= length), key=lambda c: (c.position, c.price, c.detour)
    )
    path, nearest = [], {}  # position -> smallest detour kept there
    for c in ordered:
        if c.detour < nearest.get(c.position, _INF):
            path.append(c)
            nearest[c.position] = c.detour
    return path


def _station(arrive, levels, stop, tank, min_fill):
    """Leave-level keys and their sources after passing or stopping, and the best final top-up here."""
    price, d = stop.price * _STOP_WEIGHT, stop.detour
    leave, source = arrive.copy(), np.full(tank + 1, -1, dtype=np.int64)
    finish = (_INF, None)
    if d > tank:
        return leave, source, finish
    out = levels[: tank + 1 - d]  # leaving levels a stop can produce: the tank cap holds at the pump
    reach = (arrive < _INF) & (levels >= d)  # arrival fuel covers the drive to the pump

    # Buy g >= min_fill and leave with l - 2d + g: for each leaving level, the cheapest arrival
    # level l <= out + 2d - min_fill.
    if min_fill <= tank:
        base = np.where(reach, arrive - levels * price, _INF)
        run_min = np.minimum.accumulate(base)
        run_arg = np.maximum.accumulate(np.where(base == run_min, levels, 0))
        k = np.minimum(out + 2 * d - min_fill, tank)
        ok = k >= d
        k = np.clip(k, 0, tank)
        bought = np.where(ok & (run_min[k] < _INF), run_min[k] + (out + 2 * d) * price + stop.penalty, _INF)
        _improve(leave, source, out, bought, run_arg[k])
    # A tank too full to take the minimum may still be topped up to full.
    if min_fill > 0:
        topup = np.where(
            reach & (levels - d < tank), arrive + (tank + d - levels) * price + stop.penalty, _INF
        )
        f = int(topup.argmin())
        _improve(leave, source, out[-1:], topup[f : f + 1], np.array([f]))
    # A final purchase below the minimum that exactly reaches the destination from the pump.
    if stop.need + d <= tank:
        g = stop.need + 2 * d - levels
        small = reach & (g > 0) & (g < min_fill)
        keys = np.where(small, arrive + g * price + stop.penalty, _INF)
        f = int(keys.argmin())
        finish = (int(keys[f]), f)
    if stop.reserve:
        finish = min(finish, _reserve_stop(leave, source, out, stop, tank, min_fill), key=lambda x: x[0])
    return leave, source, finish


def _reserve_stop(leave, source, out, stop, tank, min_fill):
    """The truck reaches this pump empty on reserve, repays it and buys g, leaving with g - detour."""
    price, d = stop.price * _STOP_WEIGHT, stop.detour
    base = stop.reserve * price + stop.penalty
    g = out + d
    allowed = (g >= min_fill) | (g == tank) | (g == 0)
    _improve(leave, source, out, np.where(allowed, base + g * price, _INF), np.full(len(out), -2))
    g = stop.need + d
    if 0 < g < min_fill and g <= tank:
        return (base + g * price, -2)
    return (_INF, None)


def _improve(leave, source, out, keys, arrival):
    better = keys < leave[out]
    leave[out] = np.where(better, keys, leave[out])
    source[out] = np.where(better, arrival, source[out])


def _check_gaps(path, length, tank):
    """Clear error for the common case: two neighbouring stops further apart than a full tank."""
    points = [0] + [c.position for c in path] + [length]
    if not path or path[0].position > tank:
        raise RangeGapError(0, points[1])
    for a, b in zip(points[1:], points[2:], strict=False):
        if b - a > tank:
            raise RangeGapError(a, b)


def _trace(path, stops, sources, length, end, best_finish):
    """Walk the recorded choices back from the cheapest way to finish."""
    pumped, reserve_at = {}, None
    if best_finish[0] < end[0]:
        _, i, fuel = best_finish
        if fuel == -2:
            pumped[i], reserve_at = stops[i].need + stops[i].detour, i
        else:
            pumped[i] = stops[i].need + 2 * stops[i].detour - fuel
        level = fuel  # arrival fuel at station i
    else:
        i = len(path) - 1
        level = end[1] + (length - path[-1].position)  # fuel on leaving the last station
        level = _undo_purchase(stops[i], sources[i], level, pumped, i)
    while level != -2 and i > 0:
        i -= 1
        level += path[i + 1].position - path[i].position  # fuel on leaving station i
        level = _undo_purchase(stops[i], sources[i], level, pumped, i)
    if level == -2 and reserve_at is None:
        reserve_at = i
    if reserve_at is not None:
        pumped.setdefault(reserve_at, 0)  # a reserve stop with no detour may buy nothing more
    return [Purchase(path[i], pumped[i], stops[i].reserve if i == reserve_at else 0) for i in sorted(pumped)]


def _undo_purchase(stop, source, level, pumped, i):
    """Given fuel on leaving station i, record what was bought there; return arrival fuel (-2 = reserve)."""
    arrival = int(source[level])
    if arrival == -1:
        return level
    if arrival == -2:
        pumped[i] = level + stop.detour
        return -2
    pumped[i] = level - arrival + 2 * stop.detour
    return arrival
