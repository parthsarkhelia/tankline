import functools
import random
from decimal import Decimal

import pytest

from planner.optimizer import Candidate, RangeGapError, plan_purchases

INF = (float("inf"), float("inf"))


def reference(candidates, length, start_fuel, tank):
    """Exhaustive (cost, stops) minimum over integer purchases. Small inputs only."""
    path = sorted(candidates, key=lambda c: (c.position, c.price))
    path.append(Candidate(-1, length, Decimal(0)))

    @functools.lru_cache(None)
    def from_node(i, fuel):
        if i == len(path) - 1:
            return (0, 0)
        best = INF
        for buy in range(tank - fuel + 1):
            best = min(best, _add(drive(i, fuel + buy), buy * path[i].price, buy > 0))
        return best

    @functools.lru_cache(None)
    def drive(i, fuel):
        best = INF
        for j in range(i + 1, len(path)):
            distance = path[j].position - path[i].position
            if distance > fuel:
                break
            best = min(best, from_node(j, fuel - distance))
        return best

    if start_fuel >= length:
        return (0, 0)
    first = path[0].position
    if start_fuel >= first:
        return min(
            (
                from_node(j, start_fuel - path[j].position)
                for j in range(len(path))
                if path[j].position <= start_fuel
            ),
            default=INF,
        )
    if first > tank:
        return INF
    shortfall = first - start_fuel
    return min(
        (_add(drive(0, extra), (shortfall + extra) * path[0].price, True) for extra in range(tank + 1)),
        default=INF,
    )


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


def test_matches_exhaustive_search():
    rng = random.Random(7)
    checked = extra_stops = 0
    for _ in range(3000):
        candidates, length, start_fuel, tank = random_case(rng)
        best = reference(candidates, length, start_fuel, tank)
        if best == INF:
            with pytest.raises(RangeGapError):
                plan_purchases(candidates, length, start_fuel, tank)
            continue
        purchases = plan_purchases(candidates, length, start_fuel, tank)
        checked += 1
        assert total(purchases) == best[0]
        assert sum(p.fuel + p.reserve for p in purchases) == max(0, length - start_fuel)
        assert all(p.fuel <= tank for p in purchases)
        assert len(purchases) <= best[1] + 1
        extra_stops += len(purchases) > best[1]
    assert checked > 1000
    assert extra_stops <= checked // 500


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
