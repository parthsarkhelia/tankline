"""One route per request: OpenRouteService truck profile, OSRM public server as fallback."""

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
import requests
from django.conf import settings
from django.core.cache import cache

from stations.geo import normalize

from .errors import RouteRejected, UpstreamUnavailable

log = logging.getLogger(__name__)
GEOCODE_CACHE_SECONDS = 30 * 24 * 3600
GEOCODE_MIN_CONFIDENCE = 0.8  # the ORS geocoder returns a fuzzy best guess even for gibberish
OSRM_NO_ROUTE_CODES = {"NoRoute", "NoSegment"}  # NoSegment: no road near one of the points

ORS_DIRECTIONS_URL = "https://api.openrouteservice.org/v2/directions/driving-hgv/geojson"
ORS_GEOCODE_URL = "https://api.openrouteservice.org/geocode/search"
OSRM_ROUTE_URL = (
    "https://router.project-osrm.org/route/v1/driving/{lng1:.6f},{lat1:.6f};{lng2:.6f},{lat2:.6f}"
)
METERS_PER_MILE = 1609.344
USER_AGENT = "tankline/1.0 (+https://github.com/parthsarkhelia/tankline)"
# ORS error codes that mean "no such route", not "service trouble".
ORS_REJECTIONS = {
    2004: "The route is longer than the routing service allows (about 3,700 miles).",
    2009: "No drivable route connects these places.",
    2010: "No road found near one of the places.",
}

_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


@dataclass(frozen=True)
class Route:
    coordinates: np.ndarray  # (N, 2) lng, lat
    distance_miles: float
    duration_hours: float
    provider: str
    profile: str


class _TryFallback(Exception):
    pass


def ors_budget_left():
    """Count ORS calls per UTC day across all workers; past the budget, OSRM answers instead."""
    key = f"ors-calls:{datetime.now(UTC):%Y-%m-%d}"
    cache.add(key, 0, 2 * 24 * 3600)
    try:
        return cache.incr(key) <= settings.ORS_DAILY_BUDGET
    except ValueError:  # evicted between add and incr
        return True


def fetch_route(start_lat, start_lng, finish_lat, finish_lng):
    """Return (Route, external_calls)."""
    points = (start_lat, start_lng, finish_lat, finish_lng)
    use_ors = settings.ROUTING_PROVIDER == "ors" and bool(
        settings.ORS_API_KEY
    )  # no key: OSRM, no budget spent
    if use_ors and not ors_budget_left():
        log.warning("daily openrouteservice budget spent, using OSRM")
    elif use_ors:
        try:
            return _ors_route(*points), 1
        except _TryFallback as exc:
            log.warning("openrouteservice unavailable, using OSRM: %s", exc)
            return _osrm_route(*points), 2
    return _osrm_route(*points), 1


def _ors_route(start_lat, start_lng, finish_lat, finish_lng):
    body = {
        "coordinates": [[start_lng, start_lat], [finish_lng, finish_lat]],
        "radiuses": [settings.ORS_SNAP_RADIUS_M, settings.ORS_SNAP_RADIUS_M],
        "instructions": False,
    }
    try:
        response = _session.post(
            ORS_DIRECTIONS_URL,
            json=body,
            headers={"Authorization": settings.ORS_API_KEY},
            timeout=settings.HTTP_TIMEOUT,
        )
    except requests.RequestException as exc:
        raise _TryFallback(type(exc).__name__) from exc
    if response.status_code == 200:
        try:
            feature = response.json()["features"][0]
            summary = feature["properties"]["summary"]
            return Route(
                coordinates=np.asarray(feature["geometry"]["coordinates"], dtype=float)[:, :2],
                distance_miles=summary["distance"] / METERS_PER_MILE,
                duration_hours=summary["duration"] / 3600,
                provider="openrouteservice",
                profile="driving-hgv",
            )
        except (ValueError, KeyError, IndexError, TypeError) as exc:
            raise _TryFallback("malformed response") from exc
    code = _ors_error_code(response)
    if code in ORS_REJECTIONS:
        raise RouteRejected(ORS_REJECTIONS[code])
    log.warning("openrouteservice HTTP %s code %s: %.300s", response.status_code, code, response.text)
    raise _TryFallback(f"HTTP {response.status_code}")


def _ors_error_code(response):
    try:
        error = response.json().get("error")
        return error.get("code") if isinstance(error, dict) else None
    except ValueError:
        return None


def _osrm_route(start_lat, start_lng, finish_lat, finish_lng):
    url = OSRM_ROUTE_URL.format(lng1=start_lng, lat1=start_lat, lng2=finish_lng, lat2=finish_lat)
    try:
        response = _session.get(
            url, params={"overview": "full", "geometries": "geojson"}, timeout=settings.HTTP_TIMEOUT
        )
        data = response.json()
    except (requests.RequestException, ValueError) as exc:
        log.warning("OSRM request failed: %s", type(exc).__name__)
        raise UpstreamUnavailable("The routing service is unavailable. Please try again shortly.") from exc
    if not isinstance(data, dict):
        raise UpstreamUnavailable("The routing service is unavailable. Please try again shortly.")
    if data.get("code") in OSRM_NO_ROUTE_CODES:
        raise RouteRejected(ORS_REJECTIONS[2009] if data["code"] == "NoRoute" else ORS_REJECTIONS[2010])
    try:
        route = data["routes"][0]
        return Route(
            coordinates=np.asarray(route["geometry"]["coordinates"], dtype=float)[:, :2],
            distance_miles=route["distance"] / METERS_PER_MILE,
            duration_hours=route["duration"] / 3600,
            provider="osrm",
            profile="driving",
        )
    except (KeyError, IndexError, TypeError) as exc:
        log.warning("OSRM HTTP %s: %.300s", response.status_code, response.text)
        raise UpstreamUnavailable("The routing service is unavailable. Please try again shortly.") from exc


def geocode_us(text):
    """Free-text fallback geocoder, US only. Returns ((lat, lng, label) or None, external_calls).

    Definite answers (a match or "no confident match") are cached, so a repeated request costs nothing.
    """
    if not settings.ORS_API_KEY:
        return None, 0
    key = "geocode:v1:" + hashlib.sha256(normalize(text).encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        return (tuple(cached) if cached else None), 0
    if not ors_budget_left():
        return None, 0
    try:
        response = _session.get(
            ORS_GEOCODE_URL,
            params={"text": text, "boundary.country": "US", "size": 1},
            headers={"Authorization": settings.ORS_API_KEY},
            timeout=settings.HTTP_TIMEOUT,
        )
        response.raise_for_status()
        features = response.json()["features"]
    except requests.RequestException, ValueError, KeyError, TypeError:
        return None, 1  # transient: not cached
    found = None
    try:
        feature = features[0]
        if float(feature["properties"].get("confidence", 0)) >= GEOCODE_MIN_CONFIDENCE:
            lng, lat = feature["geometry"]["coordinates"][:2]
            found = (float(lat), float(lng), str(feature["properties"].get("label", text))[:120])
    except IndexError, KeyError, TypeError, ValueError:
        found = None
    cache.set(key, list(found) if found else [], GEOCODE_CACHE_SECONDS)
    return found, 1
