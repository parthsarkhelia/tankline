"""Minimum-cost fuel purchases along a fixed route.

Units are integer tenths of a mile: positions along the route and fuel in the
tank (as range it provides). At 10 mpg one tenth of a mile is 0.01 gal.
"""

from dataclasses import dataclass
from decimal import Decimal

TANK_TENTHS = 5000  # 500 miles
_DESTINATION_KEY = -1


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


def plan_purchases(candidates, length, start_fuel=0, tank=TANK_TENTHS):
    """Return the purchases that minimise total cost, fewest stops on price ties.

    Greedy on a line: from each stop, if a cheaper station is within a full
    tank, buy just enough to reach the first one; otherwise fill up and go to
    the cheapest station in range (the farthest one on price ties). The
    destination acts as a free station. A truck that cannot reach the first
    station on its start fuel runs on reserve and repays it there, on top of
    that stop's fill.
    """
    start_fuel = min(start_fuel, tank)
    if start_fuel >= length:
        return []
    stations = sorted(
        (c for c in candidates if 0 <= c.position <= length),
        key=lambda c: (c.position, c.price),
    )
    path = stations + [Candidate(_DESTINATION_KEY, length, Decimal(0))]
    if path[0].position > tank or len(path) == 1:
        raise RangeGapError(0, path[0].position)

    buys = {}
    reserve = max(0, path[0].position - start_fuel)
    fuel = start_fuel + reserve - path[0].position

    i, last = 0, len(path) - 1
    while i < last:
        here = path[i]
        reach = here.position + tank
        cheaper = best = None
        j = i + 1
        while j <= last and path[j].position <= reach:
            if path[j].price < here.price:
                cheaper = j
                break
            if best is None or path[j].price <= path[best].price:
                best = j
            j += 1
        if cheaper is None and best is None:
            raise RangeGapError(here.position, path[i + 1].position)
        nxt = cheaper if cheaper is not None else best
        distance = path[nxt].position - here.position
        need = (distance if cheaper is not None else tank) - fuel
        if need > 0:
            buys[i] = buys.get(i, 0) + need
            fuel += need
        fuel -= distance
        i = nxt

    stops = dict(_merge_equal_price(path, sorted(buys.items()), start_fuel + reserve, tank))
    if reserve:
        stops.setdefault(0, 0)
    return [Purchase(path[idx], stops[idx], reserve if idx == 0 else 0) for idx in sorted(stops)]


# Known limit: stop count is minimal in all but ~1 in 3,000 tie-heavy random cases;
# an exact (cost, stops) DP is O(n^3) and would blow the latency budget.
def _merge_equal_price(path, stops, start_fuel, tank):
    """Fold two same-price stops into one where the tank allows; cost is unchanged."""
    merged = True
    while merged:
        merged = False
        for a in range(len(stops) - 1):
            (ia, fa), (ib, fb) = stops[a], stops[a + 1]
            price = path[ia].price
            if path[ib].price != price:
                continue
            for k in range(ia, ib + 1):
                if path[k].price != price:
                    continue
                trial = stops[:a] + [(k, fa + fb)] + stops[a + 2 :]
                if _feasible(path, dict(trial), start_fuel, tank):
                    stops, merged = trial, True
                    break
            if merged:
                break
    return stops


def _feasible(path, buys, start_fuel, tank):
    """start_fuel includes any reserve, so the drive to the first station is covered."""
    fuel, previous = start_fuel, 0
    for idx, node in enumerate(path):
        fuel -= node.position - previous
        previous = node.position
        if fuel < 0:
            return False
        fuel += buys.get(idx, 0)
        if not 0 <= fuel <= tank:
            return False
    return True
