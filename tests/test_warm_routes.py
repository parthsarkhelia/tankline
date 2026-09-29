import gzip
import io
import json
from pathlib import Path

import pytest
import responses
from django.core.management import call_command

from planner import routing
from planner.management.commands import warm_routes

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.django_db
@pytest.mark.usefixtures("load_stations")
@responses.activate
def test_second_warm_run_is_all_cache_hits(monkeypatch):
    monkeypatch.setattr(warm_routes, "DEMO_ROUTES", [("Chicago, IL", "St. Louis, MO")])
    with gzip.open(FIXTURES / "ors_chicago_stl.json.gz", "rt") as fh:
        responses.post(routing.ORS_DIRECTIONS_URL, json=json.load(fh))
    first, second = io.StringIO(), io.StringIO()
    call_command("warm_routes", stdout=first)
    call_command("warm_routes", stdout=second)
    assert "cache_hit=False" in first.getvalue() and "cache_hit=True" in second.getvalue()
    assert len(responses.calls) == 1
