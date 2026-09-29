import gzip
import json
from pathlib import Path

import pytest
import requests
import responses

from planner import routing
from planner.errors import RouteRejected, UpstreamUnavailable

FIXTURES = Path(__file__).parent / "fixtures"
CHI, STL = (41.8375, -87.6866), (38.6359, -90.2446)
OSRM_CHI_STL = routing.OSRM_ROUTE_URL.format(lng1=CHI[1], lat1=CHI[0], lng2=STL[1], lat2=STL[0])


def fixture(name):
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as fh:
        return json.load(fh)


@responses.activate
def test_ors_route_parsed_in_miles():
    responses.post(routing.ORS_DIRECTIONS_URL, json=fixture("ors_chicago_stl"))
    route, calls = routing.fetch_route(*CHI, *STL)
    assert calls == 1 and route.provider == "openrouteservice" and route.profile == "driving-hgv"
    assert 280 < route.distance_miles < 310
    assert route.coordinates.shape[1] == 2
    assert responses.calls[0].request.headers["Authorization"] == "test-key"
    body = json.loads(responses.calls[0].request.body)
    assert body["coordinates"] == [[CHI[1], CHI[0]], [STL[1], STL[0]]]  # lng, lat
    assert body["radiuses"] == [5000, 5000]


@pytest.mark.parametrize("status", [403, 429, 500, 503])
@responses.activate
def test_falls_back_to_osrm(status):
    responses.post(routing.ORS_DIRECTIONS_URL, status=status, json={"error": "quota"})
    responses.get(OSRM_CHI_STL, json=fixture("osrm_chicago_stl"))
    route, calls = routing.fetch_route(*CHI, *STL)
    assert (route.provider, route.profile, calls) == ("osrm", "driving", 2)


@responses.activate
def test_falls_back_on_timeout():
    responses.post(routing.ORS_DIRECTIONS_URL, body=requests.ConnectTimeout())
    responses.get(OSRM_CHI_STL, json=fixture("osrm_chicago_stl"))
    assert routing.fetch_route(*CHI, *STL)[0].provider == "osrm"


@pytest.mark.parametrize("body", [{"features": []}, {"type": "FeatureCollection"}, "not json"])
@responses.activate
def test_malformed_ors_body_falls_back(body):
    kwargs = {"json": body} if isinstance(body, dict) else {"body": body}
    responses.post(routing.ORS_DIRECTIONS_URL, **kwargs)
    responses.get(OSRM_CHI_STL, json=fixture("osrm_chicago_stl"))
    assert routing.fetch_route(*CHI, *STL)[0].provider == "osrm"


@pytest.mark.parametrize(("status", "code"), [(400, 2004), (404, 2010), (404, 2009)])
@responses.activate
def test_ors_rejections_are_422(status, code):
    responses.post(routing.ORS_DIRECTIONS_URL, status=status, json={"error": {"code": code, "message": "x"}})
    with pytest.raises(RouteRejected):
        routing.fetch_route(*CHI, *STL)
    assert len(responses.calls) == 1  # no fallback for a real "no route"


@responses.activate
def test_both_providers_down_is_502_without_upstream_text():
    responses.post(routing.ORS_DIRECTIONS_URL, status=500, body="secret internal trace")
    responses.get(OSRM_CHI_STL, status=500, body="osrm trace")
    with pytest.raises(UpstreamUnavailable) as err:
        routing.fetch_route(*CHI, *STL)
    assert "trace" not in err.value.message


@responses.activate
def test_osrm_only_mode(settings):
    settings.ROUTING_PROVIDER = "osrm"
    responses.get(OSRM_CHI_STL, json=fixture("osrm_chicago_stl"))
    route, calls = routing.fetch_route(*CHI, *STL)
    assert (route.provider, calls) == ("osrm", 1)


@responses.activate
def test_geocode_us_restricts_country_and_sends_text_as_param():
    responses.get(routing.ORS_GEOCODE_URL, json=fixture("ors_geocode_breezewood"))
    found, calls = routing.geocode_us("Breezewood, PA/../../v2?x=1")
    assert calls == 1 and found is not None
    request = responses.calls[0].request
    assert request.url.startswith(routing.ORS_GEOCODE_URL + "?")
    assert "boundary.country=US" in request.url


def test_geocode_us_without_key_makes_no_call(settings):
    settings.ORS_API_KEY = ""
    assert routing.geocode_us("Breezewood, PA") == (None, 0)


@responses.activate
def test_geocode_us_caches_answers():
    responses.get(routing.ORS_GEOCODE_URL, json=fixture("ors_geocode_breezewood"))
    first, calls_1 = routing.geocode_us("Breezewood, PA")
    again, calls_2 = routing.geocode_us("  BREEZEWOOD, pa ")
    assert (calls_1, calls_2, first == again) == (1, 0, True)
    assert len(responses.calls) == 1


@responses.activate
def test_geocode_us_rejects_low_confidence():
    body = fixture("ors_geocode_breezewood")
    body["features"][0]["properties"]["confidence"] = 0.3
    responses.get(routing.ORS_GEOCODE_URL, json=body)
    assert routing.geocode_us("xqzv, PA") == (None, 1)


@responses.activate
def test_spent_budget_goes_straight_to_osrm(settings):
    settings.ORS_DAILY_BUDGET = 0
    responses.get(OSRM_CHI_STL, json=fixture("osrm_chicago_stl"))
    route, calls = routing.fetch_route(*CHI, *STL)
    assert (route.provider, calls) == ("osrm", 1)


@pytest.mark.parametrize(("code", "text"), [("NoRoute", "No drivable"), ("NoSegment", "No road")])
@responses.activate
def test_osrm_no_route_codes_are_422(settings, code, text):
    settings.ROUTING_PROVIDER = "osrm"
    responses.get(OSRM_CHI_STL, json={"code": code})
    with pytest.raises(RouteRejected, match=text):
        routing.fetch_route(*CHI, *STL)


@responses.activate
def test_osrm_non_object_body_is_502(settings):
    settings.ROUTING_PROVIDER = "osrm"
    responses.get(OSRM_CHI_STL, json=["unexpected"])
    with pytest.raises(UpstreamUnavailable):
        routing.fetch_route(*CHI, *STL)


@responses.activate
def test_ors_without_key_uses_osrm_only(settings):
    settings.ROUTING_PROVIDER = "ors"
    settings.ORS_API_KEY = ""
    responses.get(OSRM_CHI_STL, json=fixture("osrm_chicago_stl"))
    route, calls = routing.fetch_route(*CHI, *STL)
    assert (route.provider, calls) == ("osrm", 1)
    assert [c.request.url.split("?")[0] for c in responses.calls] == [OSRM_CHI_STL]
