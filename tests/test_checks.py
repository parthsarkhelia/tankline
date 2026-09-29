from planner.checks import routing_settings


def ids():
    return [m.id for m in routing_settings(None)]


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
