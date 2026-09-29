import gzip
import json
from pathlib import Path

import pytest
import responses
from django.core.cache import cache
from rest_framework.throttling import AnonRateThrottle

from planner import routing
from stations.models import Station
from stations.prices import STATIONS_VERSION_KEY

FIXTURES = Path(__file__).parent / "fixtures"
pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def stations(load_stations):  # pylint: disable=unused-argument  # fixture requested for its side effect
    """Synthetic sample stations for every test in this module."""


def ors(name):
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt") as fh:
        return json.load(fh)


@responses.activate
def test_map_renders_route_and_escapes_station_names(client):
    Station.objects.filter(pk__in=Station.objects.filter(state="IL").values("pk")[:50]).update(
        name='<script>alert("x")</script>'
    )
    cache.set(STATIONS_VERSION_KEY, "renamed", None)  # as load_stations does after a reload
    responses.post(routing.ORS_DIRECTIONS_URL, json=ors("ors_chicago_stl"))
    response = client.get("/map/", {"start": "Chicago, IL", "finish": "St. Louis, MO"})
    html = response.content.decode()
    assert response.status_code == 200
    assert "leaflet" in html and "openstreetmap.org/copyright" in html
    assert '<script>alert("x")</script>' not in html


def test_map_shows_errors_instead_of_404(client):
    response = client.get("/map/", {"start": "Springfield", "finish": "Chicago, IL"})
    assert response.status_code == 400
    assert "matches several places" in response.content.decode()


def test_map_shares_the_api_throttle(client, monkeypatch):
    monkeypatch.setattr(AnonRateThrottle, "THROTTLE_RATES", {"anon": "1/min"})
    client.get("/api/v1/plan/", {"start": "Springfield", "finish": "Chicago, IL"})
    assert client.get("/map/", {"start": "Springfield", "finish": "Chicago, IL"}).status_code == 429
