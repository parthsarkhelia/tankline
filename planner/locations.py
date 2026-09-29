"""Turn a user's start/finish text into a point, offline whenever possible."""

import re
from dataclasses import dataclass

from stations.geo import CANADIAN_PROVINCES, STATE_BY_NAME, US_STATES, normalize, places_index, zcta_index

from .errors import AmbiguousLocation, LocationNotFound, OutsideServiceArea
from .routing import geocode_us

LOWER_48 = (24.3, 49.5, -125.0, -66.8)  # min lat, max lat, min lng, max lng
MAX_CANDIDATES = 10
_COORDS = re.compile(r"^(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)$", re.ASCII)
_ZIP = re.compile(r"^(\d{5})(?:-\d{4})?$", re.ASCII)


@dataclass(frozen=True)
class Location:
    name: str
    lat: float
    lng: float
    resolved_by: str
    radius_miles: float = 0.0  # city radius when resolved from the Gazetteer; sizes the start zone


def resolve(text):
    """Return (Location, external_calls). Accepts 'City, ST', 'City, State', ZIP, 'lat,lng' or free text."""
    text = " ".join(text.split())
    if not text:
        raise LocationNotFound("Location is empty.")
    if match := _COORDS.match(text):
        lat, lng = float(match[1]), float(match[2])
        return _in_service_area(Location(f"{lat},{lng}", lat, lng, "coordinates")), 0
    if match := _ZIP.match(text):
        point = zcta_index().get(match[1])
        if point is None:
            raise LocationNotFound(f"Unknown ZIP code '{match[1]}'.")
        return _in_service_area(Location(match[1], *point, "zip")), 0

    city, state = _split_state(text)
    matches = [p for p in places_index().get(normalize(city), []) if p.state == state or not state]
    if len(matches) == 1:
        place = matches[0]
        location = Location(
            f"{place.name}, {place.state}", place.lat, place.lng, "gazetteer", place.radius_miles
        )
        return _in_service_area(location), 0
    if len(matches) > 1:
        candidates = sorted(f"{p.name}, {p.state}" for p in matches)
        raise AmbiguousLocation(
            f"'{text}' matches several places; add the state.", candidates=candidates[:MAX_CANDIDATES]
        )

    found, calls = geocode_us(text)
    if found is None:
        raise LocationNotFound(f"Could not find '{text}'. Try 'City, ST', a ZIP code or 'lat,lng'.")
    lat, lng, label = found
    return _in_service_area(Location(label, lat, lng, "geocoder")), calls


def _split_state(text):
    """'Chicago, IL' or 'Chicago IL' -> ('Chicago', 'IL'); text without a recognised state -> (text, None)."""
    if "," in text:
        city, tail = (part.strip() for part in text.rsplit(",", 1))
    elif " " in text and len(tail := text.rsplit(" ", 1)[1]) == 2:
        city = text.rsplit(" ", 1)[0]
    else:
        return text, None
    code = tail.upper()
    if code in US_STATES:
        return city, code
    if code in CANADIAN_PROVINCES:
        raise OutsideServiceArea("Start and finish must be in the contiguous United States.")
    if (by_name := STATE_BY_NAME.get(normalize(tail))) is not None:
        return city, by_name
    return text, None


def _in_service_area(location):
    min_lat, max_lat, min_lng, max_lng = LOWER_48
    if not (min_lat <= location.lat <= max_lat and min_lng <= location.lng <= max_lng):
        raise OutsideServiceArea(f"'{location.name}' is outside the contiguous United States.")
    return location
