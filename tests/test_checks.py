from decimal import Decimal

import pytest

from planner.apps import routing_settings, stop_cost_setting


def ids():
    return [m.id for m in routing_settings()]


def test_ors_without_key_warns(settings):
    settings.ROUTING_PROVIDER, settings.ORS_API_KEY = "ors", ""
    assert ids() == ["planner.W001"]


def test_osrm_and_keyed_ors_are_clean(settings):
    settings.ROUTING_PROVIDER, settings.ORS_API_KEY = "osrm", ""
    assert ids() == []
    settings.ROUTING_PROVIDER, settings.ORS_API_KEY = "ors", "k"
    assert ids() == []


def test_bad_provider_is_an_error(settings):
    settings.ROUTING_PROVIDER = "bogus"
    assert ids() == ["planner.E002"]


@pytest.mark.parametrize(
    ("value", "expected"), [("18", []), ("0", []), ("-1", ["planner.E003"]), ("NaN", ["planner.E003"])]
)
def test_stop_cost_must_be_zero_or_more(settings, value, expected):
    settings.STOP_COST_USD = Decimal(value)
    assert [m.id for m in stop_cost_setting()] == expected
