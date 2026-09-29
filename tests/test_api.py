import gzip
import json
from pathlib import Path

import pytest
import responses
from rest_framework.throttling import AnonRateThrottle

from planner import routing

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = [pytest.mark.django_db, pytest.mark.usefixtures("load_stations")]
URL = "/api/v1/plan/"


def ors(name):
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as fh:
        return json.load(fh)


@responses.activate
def test_plan_ok_with_map_url(client):
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors("ors_chicago_stl"))
    response = client.get(URL, {"start": "Chicago, IL", "finish": "St. Louis, MO"})
    assert response.status_code == 200
    body = response.json()
    assert body["map_url"].startswith("http://testserver/map/?")
    assert "start=Chicago%2C+IL" in body["map_url"]
    assert set(body) == {
        "start",
        "finish",
        "route",
        "fuel_stops",
        "summary",
        "assumptions",
        "map_url",
        "meta",
    }


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"finish": "St. Louis, MO"}, "start"),
        ({"start": "x" * 121, "finish": "St. Louis, MO"}, "start"),
        ({"start": "Chicago, IL", "finish": "St. Louis, MO", "start_fuel_miles": "501"}, "start_fuel_miles"),
        ({"start": "Chicago, IL", "finish": "St. Louis, MO", "start_fuel_miles": "nan"}, "start_fuel_miles"),
        ({"start": "Chicago, IL", "finish": "St. Louis, MO", "start_fuel_miles": "-1"}, "start_fuel_miles"),
    ],
)
def test_invalid_params_are_400(client, params, field):
    response = client.get(URL, params)
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "invalid_request" and field in error["fields"]


def test_ambiguous_place_lists_candidates(client):
    response = client.get(URL, {"start": "Springfield", "finish": "Chicago, IL"})
    assert response.status_code == 400
    error = response.json()["error"]
    assert error["code"] == "ambiguous_location"
    assert 1 < len(error["candidates"]) <= 10 and all(
        c.startswith("Springfield, ") for c in error["candidates"]
    )


@responses.activate
def test_upstream_failure_is_502_with_generic_message(client):
    responses.post(routing.ORS_DIRECTIONS_URL, status=500, body="Traceback: secret")
    responses.get(
        routing.OSRM_ROUTE_URL.format(lng1=-87.6866, lat1=41.8375, lng2=-90.2446, lat2=38.6359), status=500
    )
    response = client.get(URL, {"start": "41.8375,-87.6866", "finish": "38.6359,-90.2446"})
    assert response.status_code == 502
    assert "secret" not in response.content.decode()


def test_throttled_requests_get_429(client, monkeypatch):
    monkeypatch.setattr(
        AnonRateThrottle, "THROTTLE_RATES", {"anon": "1/min"}
    )  # read at import: patch the class
    client.get(URL, {"start": "Springfield", "finish": "Chicago, IL"})
    response = client.get(
        URL, {"start": "Springfield", "finish": "Chicago, IL"}, HTTP_X_FORWARDED_FOR="10.9.8.7"
    )
    assert response.status_code == 429  # a spoofed X-Forwarded-For does not buy a fresh bucket
    assert response.json()["error"]["code"] == "throttled"


def test_openapi_schema_and_docs(client):
    assert client.get("/api/schema/").status_code == 200
    assert client.get("/api/docs/").status_code == 200
