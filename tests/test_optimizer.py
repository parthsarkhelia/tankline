import functools
import random
from decimal import Decimal

import pytest

from planner.optimizer import Candidate, RangeGapError, plan_purchases

INF = (float("inf"), float("inf"))


def reference(candidates, length, start_fuel, tank, min_fill=0):
    """Exhaustive (cost, stops) minimum over integer purchases, same rules. Small inputs only.

    A stop pumps nothing or at least min_fill, except a smaller amount that is exactly
    what reaches the destination (then nothing more is bought). A truck that cannot reach
    the first station on start fuel repays the reserve there; that stop counts once.
    """
    path = sorted(candidates, key=lambda c: (c.position, c.price))
    path.append(Candidate(-1, length, Decimal(0)))
    last = len(path) - 1

    def buys(i, fuel):
        """(amount, finishes_here) options at station i with `fuel` on arrival."""
        to_end = length - path[i].position
        for buy in range(tank - fuel + 1):
            if buy == 0 or buy >= min_fill or fuel + buy == tank:
                yield buy, False
            elif fuel + buy == to_end:
                yield buy, True

    @functools.lru_cache(None)
    def at(i, fuel, forced=False):
        if i == last:
            return (0, 0)
        best = INF
        for buy, finishes in buys(i, fuel):
            stopped = forced or buy > 0
            here = buy * path[i].price
            rest = (0, 0) if finishes else drive(i, fuel + buy)
            best = min(best, _add(rest, here, stopped))
        return best

    @functools.lru_cache(None)
    def drive(i, fuel):
        best = INF
        for j in range(i + 1, len(path)):
            distance = path[j].position - path[i].position
            if distance > fuel:
                break
            best = min(best, at(j, fuel - distance))
        return best

    if start_fuel >= length:
        return (0, 0)
    if last == 0:  # no station at all: the tank must already cover the trip
        return INF
    first = path[0].position
    if start_fuel >= first:
        return min(
            (
                at(j, start_fuel - path[j].position)
                for j in range(len(path))
                if path[j].position <= start_fuel
            ),
            default=INF,
        )
    if first > tank:
        return INF
    shortfall = first - start_fuel
    return _add(at(0, 0, True), shortfall * path[0].price, False)


def _add(result, cost, stopped):
    if result == INF:
        return INF
    return (result[0] + cost, result[1] + int(stopped))


def total(purchases):
    return sum((p.fuel + p.reserve) * p.candidate.price for p in purchases)


def random_case(rng):
    tank = rng.randint(4, 10)
    length = rng.randint(3, 30)
    start_fuel = rng.choice([0, 0, rng.randint(0, tank)])
    positions = sorted(rng.sample(range(1, length), min(length - 1, rng.randint(0, 7))))
    candidates = [Candidate(k, p, Decimal(rng.choice([1, 2, 2, 3, 3]))) for k, p in enumerate(positions)]
    return candidates, length, start_fuel, tank


def simulate(purchases, candidates, length, start_fuel, tank, min_fill):
    """Drive the plan: fuel never negative or above the tank, and the fill rule holds."""
    bought = {p.candidate.key: p for p in purchases}
    fuel, previous = start_fuel + sum(p.reserve for p in purchases), 0
    for c in sorted(candidates, key=lambda c: (c.position, c.price)) + [Candidate(-1, length, Decimal(0))]:
        fuel -= c.position - previous
        previous = c.position
        assert fuel >= 0
        if c.key in bought:
            p = bought.pop(c.key)
            fuel += p.fuel
            assert fuel <= tank
            small_ok = fuel == tank or fuel == length - c.position
            assert p.fuel == 0 and p.reserve > 0 or p.fuel >= min_fill or small_ok
    assert not bought


@pytest.mark.parametrize("min_fill", [0, 2, 3, "tank-1"])
def test_matches_exhaustive_search(min_fill):
    rng = random.Random(7 + (99 if min_fill == "tank-1" else min_fill))
    checked = 0
    for _ in range(3000):
        candidates, length, start_fuel, tank = random_case(rng)
        fill = tank - 1 if min_fill == "tank-1" else min_fill
        best = reference(candidates, length, start_fuel, tank, fill)
        if best == INF:
            with pytest.raises(RangeGapError):
                plan_purchases(candidates, length, start_fuel, tank, fill)
            continue
        purchases = plan_purchases(candidates, length, start_fuel, tank, fill)
        checked += 1
        assert (total(purchases), len(purchases)) == best  # cheapest, then fewest stops: exact
        assert sum(p.fuel + p.reserve for p in purchases) == max(0, length - start_fuel)
        simulate(purchases, candidates, length, start_fuel, tank, fill)
    assert checked > 1000


def test_equal_prices_use_one_stop():
    candidates = [Candidate(1, 10, Decimal("3.5")), Candidate(2, 20, Decimal("3.5"))]
    purchases = plan_purchases(candidates, length=400, start_fuel=0, tank=500)
    assert [(p.candidate.key, p.fuel, p.reserve) for p in purchases] == [(1, 390, 10)]


def test_reserve_is_repaid_at_first_station():
    purchases = plan_purchases([Candidate(1, 30, Decimal("3"))], length=100, start_fuel=0, tank=500)
    assert [(p.candidate.key, p.fuel, p.reserve) for p in purchases] == [(1, 70, 30)]


def test_fill_never_exceeds_the_tank_after_a_long_reserve_run():
    candidates = [Candidate(1, 499, Decimal("3")), Candidate(2, 900, Decimal("3.5"))]
    purchases = plan_purchases(candidates, length=1300, start_fuel=0, tank=500)
    assert [(p.candidate.key, p.fuel, p.reserve) for p in purchases] == [(1, 500, 499), (2, 301, 0)]


def test_first_station_beyond_a_full_tank_is_a_gap():
    with pytest.raises(RangeGapError):
        plan_purchases([Candidate(1, 501, Decimal("3"))], length=900, start_fuel=0, tank=500)


def test_start_fuel_covers_route():
    assert plan_purchases([Candidate(1, 30, Decimal("3"))], length=100, start_fuel=100) == []


def test_gap_names_the_stretch():
    with pytest.raises(RangeGapError) as err:
        plan_purchases([Candidate(1, 10, Decimal("3"))], length=1000, start_fuel=0, tank=500)
    assert (err.value.from_position, err.value.to_position) == (10, 1000)


def test_no_stations_on_a_long_route():
    with pytest.raises(RangeGapError):
        plan_purchases([], length=6000)


def test_cheaper_station_ahead_buys_only_enough_to_reach_it():
    candidates = [Candidate(1, 0, Decimal("4")), Candidate(2, 1000, Decimal("3"))]
    purchases = plan_purchases(candidates, length=4000)
    assert [(p.candidate.key, p.fuel, p.reserve) for p in purchases] == [(1, 1000, 0), (2, 3000, 0)]


def test_minimum_fill_removes_the_tiny_stop():
    # Without a minimum the cheapest plan pumps 1 unit at 3.00 just to reach 2.99 one unit on.
    candidates = [Candidate(1, 0, Decimal("3.00")), Candidate(2, 1, Decimal("2.99"))]
    assert [p.fuel for p in plan_purchases(candidates, length=400, tank=500)] == [1, 399]
    # With a minimum of 100, every fill is at least 100 (still the cheapest plan under that rule).
    purchases = plan_purchases(candidates, length=400, tank=500, min_fill=100)
    assert [p.fuel for p in purchases] == [100, 300]


def test_small_final_top_up_is_allowed():
    candidates = [Candidate(1, 0, Decimal("3")), Candidate(2, 480, Decimal("2"))]
    purchases = plan_purchases(candidates, length=500, tank=500, min_fill=100)
    assert [(p.candidate.key, p.fuel) for p in purchases] == [(1, 480), (2, 20)]


def test_cheapest_of_several_stations_at_one_spot_is_used():
    candidates = [
        Candidate(1, 50, Decimal("3.20")),
        Candidate(2, 50, Decimal("3.10")),
        Candidate(3, 50, Decimal("3.30")),
    ]
    purchases = plan_purchases(candidates, length=300, tank=500)
    assert [(p.candidate.key, p.fuel, p.reserve) for p in purchases] == [(2, 250, 50)]


def test_full_top_up_below_the_minimum_keeps_the_trip_drivable():
    candidates = [Candidate(1, 0, Decimal("3")), Candidate(2, 4800, Decimal("3"))]
    purchases = plan_purchases(candidates, length=9700, start_fuel=4500, tank=5000, min_fill=1000)
    assert sum(p.fuel + p.reserve for p in purchases) == 9700 - 4500
    simulate(purchases, candidates, 9700, 4500, 5000, 1000)
    assert purchases[0].fuel == 500  # tops the tank up to full, below the minimum
