import gzip
import json
import re
from decimal import Decimal
from pathlib import Path

import pytest
import responses
from django.core.cache import cache

from planner import routing, services
from planner.errors import FuelGap, OutsideServiceArea, SameLocation
from stations.models import Station
from stations.prices import STATIONS_VERSION_KEY

FIXTURES = Path(__file__).parent / "fixtures"


def ors_fixture(name):
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as fh:
        return json.load(fh)


pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("load_stations")]


@responses.activate
def test_chicago_to_st_louis_end_to_end():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    body = services.plan_trip("Chicago, IL", "St. Louis, MO")
    summary = body["summary"]
    assert body["meta"] == {**body["meta"], "external_calls": 1, "cache_hit": False}
    assert body["fuel_stops"], "a 294-mile trip on reserve must buy fuel"
    assert summary["gallons_purchased"] == summary["gallons_burned"]  # every mile's fuel is paid for
    assert Decimal(summary["total_cost"]) == sum(Decimal(s["cost"]) for s in body["fuel_stops"])
    for stop in body["fuel_stops"]:  # the shown numbers multiply out exactly
        bought = Decimal(stop["gallons"]) + Decimal(stop["reserve_gallons"])
        assert Decimal(stop["cost"]) == (bought * Decimal(stop["price_per_gallon"])).quantize(Decimal("0.01"))
        assert Decimal(stop["gallons"]) <= 50  # a fill never exceeds the tank
    final = body["fuel_stops"][-1]
    for stop in body["fuel_stops"]:  # minimum fill, unless it is the small final top-up or a reserve stop
        assert Decimal(stop["gallons"]) >= 10 or stop is final or Decimal(stop["reserve_gallons"]) > 0
    miles = [s["mile"] for s in body["fuel_stops"]]
    assert miles == sorted(miles)
    assert all(s["country"] == "US" for s in body["fuel_stops"])


@responses.activate
def test_trip_from_empty_starts_at_a_station_near_the_start():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    body = services.plan_trip("Chicago, IL", "St. Louis, MO")
    first = body["fuel_stops"][0]
    assert first["reserve_gallons"] == "0.00" and first["mile"] <= 20
    assert body["assumptions"] == [services.EMPTY_TANK_NOTE]


@responses.activate
def test_reserve_run_when_no_station_is_near_the_start():
    Station.objects.filter(lat__gt=41.0).delete()  # nothing left near Chicago
    cache.set(STATIONS_VERSION_KEY, "no-chicago", None)
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    body = services.plan_trip("Chicago, IL", "St. Louis, MO")
    first = body["fuel_stops"][0]
    assert Decimal(first["reserve_gallons"]) > 0 and Decimal(first["gallons"]) <= 50
    assert any("on reserve" in note for note in body["assumptions"])
    assert body["summary"]["gallons_purchased"] == body["summary"]["gallons_burned"]


@responses.activate
def test_reserve_note_counts_the_start_fuel():
    Station.objects.filter(lat__gt=41.0).delete()
    cache.set(STATIONS_VERSION_KEY, "no-chicago-fuel", None)
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    body = services.plan_trip("Chicago, IL", "St. Louis, MO", start_fuel_miles=50)
    note = next(n for n in body["assumptions"] if "on reserve" in n)
    assert f"runs {body['fuel_stops'][0]['mile'] - 50:.1f} miles" in note
    assert "Pass start_fuel_miles" not in note


@responses.activate
def test_start_fuel_removes_the_empty_tank_note():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    assert services.plan_trip("Chicago, IL", "St. Louis, MO", start_fuel_miles=100)["assumptions"] == []


@responses.activate
def test_second_request_hits_cache_with_zero_calls():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    services.plan_trip("Chicago, IL", "St. Louis, MO")
    again = services.plan_trip("chicago, il", "Saint Louis, Missouri")
    assert again["meta"]["external_calls"] == 0 and again["meta"]["cache_hit"] is True
    assert len(responses.calls) == 1


@responses.activate
def test_full_start_tank_on_a_short_trip_buys_nothing():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    body = services.plan_trip("Chicago, IL", "St. Louis, MO", start_fuel_miles=500)
    assert body["fuel_stops"] == [] and body["summary"]["total_cost"] == "0.00"
    assert Decimal(body["summary"]["gallons_burned"]) > 29


@responses.activate
def test_port_huron_route_never_uses_canadian_stations():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_port_huron_chicago"))
    body = services.plan_trip("Port Huron, MI", "Chicago, IL")
    assert Station.objects.filter(
        city__iexact="Sarnia", country="CA"
    ).exists()  # cheap, 3 mi away, still excluded
    assert all(s["country"] == "US" for s in body["fuel_stops"])


@responses.activate
def test_detroit_buffalo_can_use_ontario_if_route_crosses_it():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_detroit_buffalo"))
    body = services.plan_trip("Detroit, MI", "Buffalo, NY")
    assert body["fuel_stops"]
    # If the recorded route runs through Ontario, CA stations are allowed only while it is there.
    for stop in body["fuel_stops"]:
        if stop["country"] == "CA":
            assert stop["state"] == "ON"


@responses.activate
def test_coast_to_coast_plans_multiple_stops_quickly():
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_miami_seattle"))
    body = services.plan_trip("Miami, FL", "Seattle, WA")
    assert len(body["fuel_stops"]) >= 6
    assert body["meta"]["elapsed_ms"] < 1000  # HTTP mocked, so this is our own work


def test_same_start_and_finish_is_rejected_without_routing():
    with pytest.raises(SameLocation):
        services.plan_trip("Chicago, IL", "Chicago, Illinois")


@responses.activate
def test_gap_longer_than_range_is_422(monkeypatch):
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    empty = services.StationTable.empty()
    monkeypatch.setattr(services, "station_table", lambda: empty)
    with pytest.raises(FuelGap) as err:
        services.plan_trip("Chicago, IL", "St. Louis, MO")
    assert "start_fuel_miles" in err.value.message


@pytest.mark.parametrize(
    ("start", "finish"), [("43.6532,-79.3832", "Chicago, IL"), ("Chicago, IL", "25.6866,-100.3161")]
)
def test_start_or_finish_in_canada_or_mexico_is_rejected(start, finish):
    with pytest.raises(OutsideServiceArea):
        services.plan_trip(start, finish)


@responses.activate
def test_repeat_request_reuses_the_whole_plan(monkeypatch):
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_miami_seattle"))
    first = services.plan_trip("Miami, FL", "Seattle, WA")
    monkeypatch.setattr(services, "place_stations", lambda *a, **k: pytest.fail("plan was recomputed"))
    again = services.plan_trip("miami, fl", "Seattle, Washington")
    assert again["fuel_stops"] == first["fuel_stops"]
    assert again["meta"] == {**again["meta"], "cache_hit": True, "external_calls": 0}
    assert again["start"]["query"] == "miami, fl"  # echoes this request, not the cached one


@responses.activate
def test_fallback_route_is_cached_briefly(monkeypatch):
    responses.post(routing.ORS_DIRECTIONS_URL, status=503)
    responses.get(
        re.compile(r"https://router\.project-osrm\.org/route/v1/driving/.*"),
        json=ors_fixture("osrm_chicago_stl"),
    )
    timeouts = {}
    real_set = cache.set
    monkeypatch.setattr(
        cache,
        "set",
        lambda key, value, timeout=None: (
            timeouts.setdefault(key.split(":")[0], timeout),
            real_set(key, value, timeout),
        ),
    )
    body = services.plan_trip("Chicago, IL", "St. Louis, MO")
    assert body["route"]["provider"] == "osrm"
    assert timeouts["route"] == timeouts["plan"] == services.FALLBACK_ROUTE_SECONDS


@responses.activate
def test_evicted_stations_version_never_serves_an_old_plan(monkeypatch):
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors_fixture("ors_chicago_stl"))
    runs = []
    real = services.place_stations
    monkeypatch.setattr(services, "place_stations", lambda *a, **k: runs.append(1) or real(*a, **k))
    services.plan_trip("Chicago, IL", "St. Louis, MO")
    for _ in range(2):  # a fixed fallback version would serve the second eviction's plan again
        cache.delete(STATIONS_VERSION_KEY)
        services.plan_trip("Chicago, IL", "St. Louis, MO")
    assert len(runs) == 3
