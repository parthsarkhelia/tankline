"""One trip request: resolve places, fetch (or reuse) the route, choose fuel stops."""

import time
import uuid
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from functools import lru_cache

import numpy as np
from django.conf import settings
from django.core.cache import cache

from stations.models import Station
from stations.prices import STATIONS_VERSION_KEY

from .borders import country_codes
from .corridor import haversine_mi, place_stations, simplify_route
from .errors import FuelGap, OutsideServiceArea, SameLocation
from .locations import resolve
from .optimizer import Candidate, RangeGapError, plan_purchases
from .routing import fetch_route

CENT = Decimal("0.01")
PRICE_STEP = Decimal("0.001")  # prices are shown, and costs computed, at 3 dp
SAME_PLACE_MILES = 1.0
# Assumption: roads to a pump run about 1.3x the straight-line offset (road circuity), and even a pump
# right at an exit is a 0.2-mile ramp and lot away. City-centroid stations get no floor.
ROAD_FACTOR = 1.3
RAMP_TENTHS = 2
FALLBACK_ROUTE_SECONDS = 600  # a car route standing in for ORS: retry truck routing soon
EMPTY_TANK_NOTE = (
    "Assumes the truck starts with an empty tank and fills up at the station near the start that makes "
    "the trip cheapest; the drive to that pump is paid there (reserve_gallons). "
    "Pass start_fuel_miles if it already has fuel."
)
RESERVE_NOTE = (
    "No station near the start: the truck runs {miles} miles on reserve to the first stop and repays it "
    "there (reserve_gallons)."
)
START_FUEL_HINT = " Pass start_fuel_miles if it starts with fuel."


@dataclass(frozen=True)
class StationTable:
    version: str
    stations: tuple
    lng: np.ndarray
    lat: np.ndarray
    max_offset: np.ndarray
    country: np.ndarray
    exact: np.ndarray  # placed at an OpenStreetMap exit or pump, not a city centroid

    @classmethod
    def empty(cls):
        return cls("", (), *(np.array([]) for _ in range(5)))


def stations_version():
    return cache.get_or_set(STATIONS_VERSION_KEY, lambda: uuid.uuid4().hex, None)


def station_table():
    """All stations as arrays, loaded once per process and again whenever load_stations runs."""
    return _load_table(stations_version())


@lru_cache(maxsize=1)
def _load_table(version):
    rows = tuple(Station.objects.order_by("opis_id"))
    radius = np.array([s.radius_miles for s in rows], dtype=float)
    exact = np.array([s.location_precision != "city" for s in rows], dtype=bool)
    city_corridor = np.minimum(settings.CORRIDOR_BASE_MILES + radius, settings.CORRIDOR_MAX_MILES)
    return StationTable(
        version=version,
        stations=rows,
        lng=np.array([s.lng for s in rows], dtype=float),
        lat=np.array([s.lat for s in rows], dtype=float),
        max_offset=np.where(exact, settings.CORRIDOR_BASE_MILES, city_corridor),
        country=np.array([s.country for s in rows], dtype="<U2"),
        exact=exact,
    )


def detour_tenths(offset_miles, exact):
    """One-way drive from the route to the pump, tenths of a mile."""
    tenths = round(offset_miles * ROAD_FACTOR * 10)
    return max(tenths, RAMP_TENTHS) if exact else tenths


def plan_trip(start, finish, start_fuel_miles=0.0):
    started = time.perf_counter()
    origin, calls_a = resolve(start)
    destination, calls_b = resolve(finish)
    _check_endpoints(origin, destination)

    coords = f"{origin.lat:.4f},{origin.lng:.4f}:{destination.lat:.4f},{destination.lng:.4f}"
    route_key = f"{settings.ROUTING_PROVIDER}:{coords}"
    fuel = round(start_fuel_miles * 10)
    # Stations this close to the start count as mile 0: the trip begins at the cheapest of them.
    start_zone = min(settings.CORRIDOR_BASE_MILES + origin.radius_miles, settings.CORRIDOR_MAX_MILES)
    costs = f"{settings.STOP_COST_USD}:{settings.DETOUR_COST_PER_MILE_USD}"
    plan_key = f"plan:v2:{stations_version()}:{route_key}:{fuel}:{start_zone:.1f}:{costs}"
    plan, route_calls = cache.get(plan_key), 0
    if plan is None:
        route, route_calls = _cached_route(route_key, origin, destination)
        plan = _plan(route, fuel, start_zone)
        cache.set(plan_key, plan, _ttl(route))
    return {
        "start": _place(start, origin),
        "finish": _place(finish, destination),
        **plan,
        "meta": {
            "external_calls": calls_a + calls_b + route_calls,
            "cache_hit": route_calls == 0,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        },
    }


def _check_endpoints(origin, destination):
    codes = country_codes(np.array([origin.lng, destination.lng]), np.array([origin.lat, destination.lat]))
    for location, code in zip((origin, destination), codes, strict=True):
        if code not in ("US", ""):  # "" = water or coast: let routing decide
            raise OutsideServiceArea(f"'{location.name}' is outside the United States.")
    if haversine_mi(origin.lng, origin.lat, destination.lng, destination.lat) < SAME_PLACE_MILES:
        raise SameLocation("Start and finish are the same place.")


def _cached_route(route_key, origin, destination):
    key = f"route:v1:{route_key}"
    route = cache.get(key)
    if route is not None:
        return route, 0
    route, calls = fetch_route(origin.lat, origin.lng, destination.lat, destination.lng)
    cache.set(key, route, _ttl(route))
    return route, calls


def _ttl(route):
    stand_in = settings.ROUTING_PROVIDER == "ors" and route.provider != "openrouteservice"
    return FALLBACK_ROUTE_SECONDS if stand_in else settings.ROUTE_CACHE_SECONDS


def _plan(route, fuel, start_zone):
    length = round(route.distance_miles * 10)
    stops = _choose_stops(route, length, fuel, start_zone)
    return {
        "route": _route(route),
        "fuel_stops": stops,
        "summary": _summary(length, stops, fuel),
        "assumptions": _assumptions(stops, fuel),
    }


def _choose_stops(route, length, fuel, start_zone):
    table = station_table()
    placement = place_stations(
        route.coordinates, route.distance_miles, table.lng, table.lat, table.max_offset
    )
    # A station counts only while the route is in the station's country (unknown = allowed).
    route_country = country_codes(placement.lng, placement.lat)
    allowed = (route_country == "") | (route_country == table.country[placement.index])
    candidates = [
        Candidate(
            key=int(row),
            position=0 if mile <= start_zone else round(float(mile) * 10),
            price=table.stations[row].price,
            detour=detour_tenths(float(offset), bool(table.exact[row])),
        )
        for row, mile, offset, ok in zip(
            placement.index, placement.mile, placement.offset, allowed, strict=True
        )
        if ok
    ]
    offsets = dict(zip(placement.index.tolist(), placement.offset.tolist(), strict=True))
    miles = dict(zip(placement.index.tolist(), placement.mile.tolist(), strict=True))
    try:
        purchases = plan_purchases(
            candidates,
            length,
            start_fuel=fuel,
            tank=settings.VEHICLE_RANGE_MILES * 10,
            min_fill=settings.VEHICLE_MIN_FILL_GALLONS
            * settings.VEHICLE_MPG
            * 10,  # gallons -> tenths of a mile
            stop_cost=settings.STOP_COST_USD * 10 * settings.VEHICLE_MPG,  # dollars -> price x tenths
            # dollars per mile of detour -> price x tenths, per tenth of a mile
            detour_cost=settings.DETOUR_COST_PER_MILE_USD * settings.VEHICLE_MPG,
        )
    except RangeGapError as gap:
        if not candidates:
            raise FuelGap(
                "No fuel stations along this route. If the tank covers the trip, set start_fuel_miles."
            ) from None
        raise FuelGap(
            f"No fuel station within {settings.VEHICLE_RANGE_MILES} miles between mile "
            f"{gap.from_position / 10:.0f} and mile {gap.to_position / 10:.0f} of the route.",
            from_mile=round(gap.from_position / 10, 1),
            to_mile=round(gap.to_position / 10, 1),
        ) from None
    return [
        _stop(n, p, table.stations[p.candidate.key], offsets[p.candidate.key], miles[p.candidate.key])
        for n, p in enumerate(purchases, 1)
    ]


def _gallons(tenths):
    return Decimal(tenths) / (10 * settings.VEHICLE_MPG)


def _stop(number, purchase, station, offset, mile):
    gallons, reserve = _gallons(purchase.fuel), _gallons(purchase.reserve)
    price = station.price.quantize(PRICE_STEP, ROUND_HALF_UP)
    return {
        "stop": number,
        "station_id": station.opis_id,
        "name": station.name,
        "address": station.address,
        "city": station.city,
        "state": station.state,
        "country": station.country,
        "price_per_gallon": str(price),
        "mile": round(mile, 1),  # true position, even when the start zone counts it as mile 0
        "off_route_miles": round(offset, 1),  # straight line from the route to the station
        "detour_miles": 2 * purchase.candidate.detour / 10,  # there and back, by road (estimated)
        "gallons": str(gallons.quantize(CENT)),  # pumped at this stop, never more than a full tank
        "reserve_gallons": str(reserve.quantize(CENT)),  # reserve repaid here (first stop only)
        "cost": str(((gallons + reserve) * price).quantize(CENT, ROUND_HALF_UP)),  # at the shown price
        "lat": station.lat,
        "lng": station.lng,
        "location_precision": station.location_precision,
    }


def _summary(length, stops, fuel):
    detour = round(sum(s["detour_miles"] for s in stops) * 10)  # tenths of a mile
    return {
        "stops": len(stops),
        "gallons_purchased": str(
            sum((Decimal(s["gallons"]) + Decimal(s["reserve_gallons"]) for s in stops), Decimal(0)).quantize(
                CENT
            )
        ),
        "gallons_burned": str(_gallons(length + detour).quantize(CENT)),  # route plus detours
        "total_cost": str(sum((Decimal(s["cost"]) for s in stops), Decimal(0)).quantize(CENT)),
        "start_fuel_miles": fuel / 10,
        "range_miles": settings.VEHICLE_RANGE_MILES,
        "mpg": settings.VEHICLE_MPG,
        "stop_cost_usd": str(settings.STOP_COST_USD.quantize(CENT)),
        "detour_cost_per_mile_usd": str(settings.DETOUR_COST_PER_MILE_USD.quantize(PRICE_STEP)),
        "detour_miles": detour / 10,
        "route_miles_driven": (length + detour) / 10,
    }


def _assumptions(stops, fuel):
    notes = [EMPTY_TANK_NOTE] if fuel == 0 else []
    first = stops[0] if stops else None
    # Reserve beyond the drive to the pump means no station was near the start.
    if first and Decimal(first["reserve_gallons"]) > _gallons(round(first["detour_miles"] * 5)):
        miles = round(first["mile"] + first["detour_miles"] / 2 - fuel / 10, 1)
        notes.append(RESERVE_NOTE.format(miles=miles) + (START_FUEL_HINT if fuel == 0 else ""))
    return notes


def _place(query, location):
    return {
        "query": query,
        "name": location.name,
        "lat": location.lat,
        "lng": location.lng,
        "resolved_by": location.resolved_by,
    }


def _route(route):
    coordinates = np.round(simplify_route(route.coordinates), 5).tolist()
    return {
        "distance_miles": round(route.distance_miles, 1),
        "duration_hours": round(route.duration_hours, 1),
        "provider": route.provider,
        "profile": route.profile,
        "geometry": {"type": "LineString", "coordinates": coordinates},
    }
