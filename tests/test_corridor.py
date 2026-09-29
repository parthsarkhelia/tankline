import math
import time

import numpy as np

from planner.corridor import place_stations

MI_PER_DEG_LNG_40 = 69.0 * math.cos(math.radians(40))
EAST_WEST = np.array([[-100.0, 40.0], [-99.0, 40.0]])
LENGTH = MI_PER_DEG_LNG_40


def place(lngs, lats, offsets, route=EAST_WEST, length=LENGTH):
    return place_stations(
        route, length, np.array(lngs, float), np.array(lats, float), np.array(offsets, float)
    )


def test_on_route_and_off_route_stations():
    p = place([-99.5, -99.5, -99.5], [40.0, 40 + 3 / 69, 40 + 8 / 69], [5, 5, 5])
    assert list(p.index) == [0, 1]
    assert np.allclose(p.mile, LENGTH / 2, atol=0.1)
    assert np.allclose(p.offset, [0, 3], atol=0.05)


def test_each_station_uses_its_own_corridor_width():
    p = place([-99.5, -99.5], [40 + 8 / 69, 40 + 8 / 69], [5, 12])
    assert list(p.index) == [1]


def test_stations_beyond_the_ends_clamp_into_the_route():
    p = place([-100.05, -98.95], [40.0, 40.0], [5, 5])
    assert list(p.index) == [0, 1]
    assert p.mile.min() == 0.0 and p.mile.max() == LENGTH


def test_mile_markers_follow_the_reported_distance():
    p = place([-99.5], [40.0], [5], length=2 * LENGTH)  # road longer than the drawn line
    assert math.isclose(p.mile[0], LENGTH, rel_tol=1e-3)


def test_route_that_doubles_back_projects_to_nearest_leg():
    loop = np.array([[-100.0, 40.0], [-99.0, 40.0], [-99.0, 40.2], [-100.0, 40.2]])
    length = 2 * MI_PER_DEG_LNG_40 + 0.2 * 69
    p = place([-99.5, -99.5], [40.01, 40.19], [5, 5], route=loop, length=length)
    assert p.mile[0] < p.mile[1]
    assert abs(p.mile[1] - (MI_PER_DEG_LNG_40 + 0.2 * 69 + MI_PER_DEG_LNG_40 / 2)) < 1


def test_no_stations_or_degenerate_route():
    empty = place([], [], [])
    assert empty.index.size == 0
    point = place([-100.0], [40.0], [5], route=np.array([[-100.0, 40.0], [-100.0, 40.0]]), length=0.0)
    assert point.index.size in (0, 1)


def test_coast_to_coast_is_fast():
    rng = np.random.default_rng(0)
    n = 40_000
    lng = np.linspace(-118.2, -74.0, n)
    lat = 34 + 6.7 * np.linspace(0, 1, n) + 0.3 * np.sin(np.linspace(0, 60, n))
    route = np.column_stack([lng, lat])
    st = rng.uniform([-124, 25], [-67, 49], size=(6738, 2))
    offsets = np.minimum(5 + rng.uniform(0, 6, 6738), 20)
    place_stations(route, 2800.0, st[:, 0], st[:, 1], offsets)  # warm-up
    started = time.perf_counter()
    place_stations(route, 2800.0, st[:, 0], st[:, 1], offsets)
    assert time.perf_counter() - started < 0.25  # ~30 ms measured; generous for CI
