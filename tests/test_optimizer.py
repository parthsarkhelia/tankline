import functools
import random
from decimal import Decimal

import pytest

from planner.optimizer import Candidate, RangeGapError, plan_purchases

INF = (float("inf"), float("inf"))


def reference(candidates, length, start_fuel, tank, min_fill=0, stop_cost=0, detour_cost=0):
    """Exhaustive minimum of (fuel cost + stop penalties, stops) over integer purchases, same rules.

    Small inputs only. A stop at station i leaves the route and drives `detour` units to the pump
    and back: it needs `detour` fuel on arrival, buys at the pump (tank cap applies there) and
    rejoins with fuel - 2 x detour + bought. Its penalty is stop_cost + 2 x detour x detour_cost.
    A stop pumps at least min_fill, or tops the tank up to full, or buys exactly what takes the
    truck from the pump back to the route and on to the destination (then nothing more is bought).
    A truck that reaches no pump on start fuel runs on reserve to a station at the first route
    position, repays the reserve (detour included) there and must stop there; that stop counts once.
    """
    path = sorted(candidates, key=order)
    path.append(Candidate(-1, length, Decimal(0)))
    last = len(path) - 1

    def penalty(c):
        return stop_cost + 2 * c.detour * detour_cost

    def best_of(*options: tuple) -> tuple:
        return min(options)

    def buys(i, pump):
        """(amount, finishes_here) options at station i's pump holding `pump` fuel."""
        c = path[i]
        to_end = c.detour + length - c.position
        for buy in range(tank - pump + 1):
            if buy >= min_fill or (buy > 0 and pump + buy == tank):
                yield buy, False
            elif pump + buy == to_end:
                yield buy, True

    @functools.lru_cache(None)
    def at(i, fuel, forced=False) -> tuple:
        """Best continuation arriving on the route at station i with `fuel`."""
        if i == last:
            return (0, 0)
        c = path[i]
        best = INF if forced else drive(i, fuel)  # pass by
        if fuel < c.detour:
            return best
        pump = fuel - c.detour
        options = list(buys(i, pump)) + ([(0, False)] if forced else [])
        for buy, finishes in options:
            leave = pump + buy - c.detour
            if leave < 0:
                continue
            rest = (0, 0) if finishes else drive(i, leave)
            here = buy * c.price + (0 if forced else penalty(c))
            best = best_of(best, _add(rest, here, not forced))
        return best

    @functools.lru_cache(None)
    def drive(i, fuel):
        best = INF
        for j in range(i + 1, len(path)):
            distance = path[j].position - path[i].position
            if distance > fuel:
                break
            best = best_of(best, at(j, fuel - distance))
        return best

    if start_fuel >= length:
        return (0, 0)
    stations = path[:last]
    if not stations:  # no station at all: the tank must already cover the trip
        return INF
    if start_fuel >= min(c.position + c.detour for c in stations):
        return best_of(
            INF, *(at(j, start_fuel - c.position) for j, c in enumerate(stations) if c.position <= start_fuel)
        )
    first = stations[0].position
    return best_of(
        INF,
        *(
            _add(at(j, c.detour, True), (first + c.detour - start_fuel) * c.price + penalty(c), True)
            for j, c in enumerate(stations)
            if c.position == first and first + c.detour <= tank
        ),
    )


def order(c):
    return (c.position, c.price, c.detour)


def _add(result, cost, stopped):
    if result == INF:
        return INF
    return (result[0] + cost, result[1] + int(stopped))


def objective(purchases, stop_cost=0, detour_cost=0):
    return sum(
        (p.fuel + p.reserve) * p.candidate.price + stop_cost + 2 * p.candidate.detour * detour_cost
        for p in purchases
    )


def random_case(rng, detours):
    tank = rng.randint(4, 10)
    length = rng.randint(3, 30)
    start_fuel = rng.choice([0, 0, rng.randint(0, tank)])
    positions = sorted(rng.choices(range(length), k=rng.randint(0, 7)))
    candidates = [
        Candidate(k, p, Decimal(rng.choice([1, 2, 2, 3, 3])), rng.randint(0, 2) if detours else 0)
        for k, p in enumerate(positions)
    ]
    return candidates, length, start_fuel, tank


def simulate(purchases, candidates, length, start_fuel, tank, min_fill):
    """Drive the plan: fuel never negative (route, pump, exit) nor above the tank, fill rule holds."""
    bought = {p.candidate.key: p for p in purchases}
    fuel, previous = start_fuel + sum(p.reserve for p in purchases), 0
    for c in sorted(candidates, key=order) + [Candidate(-1, length, Decimal(0))]:
        fuel -= c.position - previous
        previous = c.position
        assert fuel >= 0
        if c.key in bought:
            p = bought.pop(c.key)
            fuel -= c.detour
            assert fuel >= 0  # reached the pump
            fuel += p.fuel
            assert fuel <= tank
            small_ok = fuel in (tank, c.detour + length - c.position)
            assert p.fuel == 0 and p.reserve > 0 or p.fuel >= min_fill or small_ok
            fuel -= c.detour
            assert fuel >= 0  # back on the route
    assert not bought


@pytest.mark.parametrize("detours", [False, True])
@pytest.mark.parametrize(("stop_cost", "detour_cost"), [(0, 0), (2, 1)])
@pytest.mark.parametrize("min_fill", [0, 2, 3, "tank-1"])
def test_matches_exhaustive_search(min_fill, stop_cost, detour_cost, detours):
    rng = random.Random(7 + (99 if min_fill == "tank-1" else min_fill) + 1000 * stop_cost + 10**5 * detours)
    checked = 0
    for _ in range(4000):
        candidates, length, start_fuel, tank = random_case(rng, detours)
        fill = tank - 1 if min_fill == "tank-1" else min_fill
        args = (candidates, length, start_fuel, tank, fill, Decimal(stop_cost), Decimal(detour_cost))
        best = reference(candidates, length, start_fuel, tank, fill, stop_cost, detour_cost)
        if best == INF:
            with pytest.raises(RangeGapError):
                plan_purchases(*args)
            continue
        purchases = plan_purchases(*args)
        checked += 1
        # cheapest incl. stop and detour penalties, then fewest stops: exact
        assert (objective(purchases, stop_cost, detour_cost), len(purchases)) == best
        detour = sum(2 * p.candidate.detour for p in purchases)
        assert sum(p.fuel + p.reserve for p in purchases) == max(0, length - start_fuel) + detour
        simulate(purchases, candidates, length, start_fuel, tank, fill)
    assert checked > 1300


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


def test_stop_cost_skips_a_top_up_that_saves_less_than_the_stop():
    # The cheaper station 450 units on saves 0.10 on a 50 unit top-up: 5 in all.
    candidates = [Candidate(1, 0, Decimal("3.00")), Candidate(2, 450, Decimal("2.90"))]

    def plan(stop_cost):
        return [
            p.fuel for p in plan_purchases(candidates, length=500, tank=500, stop_cost=Decimal(stop_cost))
        ]

    assert plan(0) == [450, 50]  # free stops: top up at the cheaper station
    assert plan(20) == [500]  # a stop costs more than the saving: one fill


def test_detour_is_burned_and_bought():
    # One pump 10 units off the route: the truck runs on reserve to it and back, and pays for both legs.
    purchases = plan_purchases([Candidate(1, 100, Decimal("3"), 10)], length=300, start_fuel=0, tank=500)
    assert [(p.candidate.key, p.fuel, p.reserve) for p in purchases] == [(1, 210, 110)]


def test_detour_cost_prefers_the_nearer_pump():
    far = Candidate(1, 0, Decimal("2.50"), 30)  # 460 units (reserve 30 + 430) at 2.50 = 1150
    near = Candidate(2, 0, Decimal("3.00"), 1)  # 402 units at 3.00 = 1206

    def first_stop(detour_cost):
        purchases = plan_purchases([far, near], length=400, tank=500, detour_cost=Decimal(detour_cost))
        return purchases[0].candidate.key

    assert first_stop(0) == 1
    assert first_stop(1) == 2  # 1150 + 60 of detour > 1206 + 2


def test_tank_cap_applies_at_the_pump():
    # The pump is 50 units off the route: a full tank there rejoins the route with 450.
    candidates = [Candidate(1, 0, Decimal("2"), 50), Candidate(2, 440, Decimal("3"), 0)]
    purchases = plan_purchases(candidates, length=900, tank=500)
    simulate(purchases, candidates, 900, 0, 500, 0)
    assert [(p.candidate.key, p.fuel, p.reserve) for p in purchases] == [(1, 500, 50), (2, 450, 0)]


def test_a_pump_out_of_reach_is_skipped():
    # A full tank from mile 0 arrives at the cheap station with 5 units, short of its 10-unit detour.
    candidates = [
        Candidate(1, 0, Decimal("2")),
        Candidate(2, 95, Decimal("1"), 10),
        Candidate(3, 100, Decimal("3")),
    ]
    purchases = plan_purchases(candidates, length=200, tank=100)
    assert [(p.candidate.key, p.fuel) for p in purchases] == [(1, 100), (3, 100)]
