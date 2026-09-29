import pytest

from planner import locations
from planner.errors import AmbiguousLocation, LocationNotFound, OutsideServiceArea
from stations.geo import PlaceRecord

ST_LOUIS = PlaceRecord("St. Louis", "MO", 38.6359, -90.2446, 7.9)
ANCHORAGE = PlaceRecord("Anchorage", "AK", 61.1508, -149.1091, 44.0)
SPRINGFIELD_IL = PlaceRecord("Springfield", "IL", 39.7911, -89.6446, 7.8)
SPRINGFIELD_MO = PlaceRecord("Springfield", "MO", 37.1942, -93.2913, 9.1)


@pytest.fixture(autouse=True, name="small_index")
def _small_index(monkeypatch):
    index = {
        "st louis": [ST_LOUIS],
        "springfield": [SPRINGFIELD_IL, SPRINGFIELD_MO],
        "anchorage": [ANCHORAGE],
    }
    monkeypatch.setattr(locations, "places_index", lambda: index)
    monkeypatch.setattr(
        locations, "zcta_index", lambda: {"63101": (38.6313, -90.1922), "00601": (18.18, -66.75)}
    )
    calls = []
    monkeypatch.setattr(locations, "geocode_us", lambda text: (calls.append(text), (None, 0))[1])
    return calls


@pytest.mark.parametrize(
    "text", ["St. Louis, MO", "  st. louis ,  mo ", "Saint Louis, Missouri", "ST LOUIS, MO", "St Louis MO"]
)
def test_city_state_variants_resolve_offline(text, small_index):
    location, calls = locations.resolve(text)
    assert (location.lat, location.lng, location.resolved_by, calls) == (38.6359, -90.2446, "gazetteer", 0)
    assert location.name == "St. Louis, MO" and location.radius_miles == 7.9
    assert small_index == []


def test_zip_and_zip_plus_four():
    assert locations.resolve("63101")[0].resolved_by == "zip"
    assert locations.resolve("63101-1234")[0].lat == 38.6313


def test_coordinates():
    location, calls = locations.resolve(" 41.8781 , -87.6298 ")
    assert (location.lat, location.lng, location.resolved_by, calls) == (41.8781, -87.6298, "coordinates", 0)


def test_missing_state_lists_candidates():
    with pytest.raises(AmbiguousLocation) as err:
        locations.resolve("Springfield")
    assert err.value.extra["candidates"] == ["Springfield, IL", "Springfield, MO"]


def test_unique_name_without_state_resolves():
    assert locations.resolve("st louis")[0].name == "St. Louis, MO"


@pytest.mark.parametrize("text", ["00601", "19.4,-99.1", "61.2,-149.9", "Anchorage, AK", "Anchorage AK"])
def test_outside_lower_48_rejected(text):
    with pytest.raises(OutsideServiceArea):
        locations.resolve(text)


@pytest.mark.parametrize("text", ["Toronto, ON", "Toronto ON"])
def test_canadian_province_rejected(text):
    with pytest.raises(OutsideServiceArea):
        locations.resolve(text)


def test_unicode_digits_are_not_coordinates():
    with pytest.raises(LocationNotFound):
        locations.resolve("٤١,-٨٧")


def test_unknown_place_falls_back_to_geocoder(monkeypatch):
    monkeypatch.setattr(locations, "geocode_us", lambda text: ((40.0, -78.24, "Breezewood, PA"), 1))
    location, calls = locations.resolve("Breezewood, PA")
    assert (location.resolved_by, calls, location.name) == ("geocoder", 1, "Breezewood, PA")


def test_unknown_place_without_geocoder_is_not_found():
    with pytest.raises(LocationNotFound):
        locations.resolve("Nowhereville, KS")


@pytest.mark.parametrize("text", ["", "   ", "91,0", "0,200"])
def test_blank_or_impossible_input(text):
    with pytest.raises((LocationNotFound, OutsideServiceArea)):
        locations.resolve(text)
