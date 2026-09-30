"""Match fuel CSV rows to OpenStreetMap exits and fuel stations (build time only).

Pure functions over already-downloaded Overpass data; build_geodata does the fetching.
"""

import math
import re
from dataclasses import dataclass
from functools import lru_cache

EXIT_KM = 40.0  # an exit this far from the city centroid is still "in" that city's listing
STATION_KM = 15.0
EXACT_REF_KM = 5.0  # an exact ref ("5A") beats a number-only one only this close to the nearest fit
SAME_PUMP_KM = 8.0  # the 5-mile corridor: same-brand pumps further apart than this make a match ambiguous
BOUNDS = {"US": (24.3, 49.5, -125.0, -66.8), "CA": (41.6, 70.0, -141.0, -52.0)}  # lower 48; Canada

_ROUTE = re.compile(r"\b(IH|I|US|SR|SH|ST|HWY|HIGHWAY|TCH|RTE|RT|ROUTE|[A-Z]{1,2})\s*-?\s*(\d{1,4})[A-Z]?\b")
# The suffix letter must touch the number or follow a hyphen: in "EXIT 39 I-77" the I is a route.
_EXIT = re.compile(r"\b(?:EXIT|EXT|EX)\.?\s*#?\s*(\d{1,4})(?:\s*-\s*|)([A-Z])?(?![A-Z0-9])")
_OSM_REF = re.compile(r"^([A-Z]+)?[\s-]*(\d{1,4})")
# Longest phrase first; the same table reads CSV names and OSM brand/name tags. None = never matched.
_BRANDS = sorted(
    {
        "pilot": "pilot",
        "flying j": "flying j",
        "loves": "loves",
        "ta": "ta",
        "travelcenters of america": "ta",
        "travel centers of america": "ta",
        "petro": "petro",
        "petro stopping center": "petro",
        "petro canada": "petro canada",
        "petro card": None,
        "sapp bros": "sapp bros",
        "kwik trip": "kwik trip",
        "kwik star": "kwik trip",
        "quiktrip": "quiktrip",
        "qt": "quiktrip",
        "caseys": "caseys",
        "sheetz": "sheetz",
        "maverik": "maverik",
        "speedway": "speedway",
        "circle k": "circle k",
        "7 eleven": "7 eleven",
        "allsups": "allsups",
        "stripes": "stripes",
        "racetrac": "racetrac",
        "raceway": "raceway",
        "shell": "shell",
        "chevron": "chevron",
        "marathon": "marathon",
        "sunoco": "sunoco",
        "bp": "bp",
        "exxon": "exxon",
        "mobil": "mobil",
        "thorntons": "thorntons",
        "rutters": "rutters",
        "kum go": "kum go",
        "cefco": "cefco",
        "flyers": "flyers",
        "cenex": "cenex",
        "cubbys": "cubbys",
        "road ranger": "road ranger",
        "husky": "husky",
        "esso": "esso",
        "phillips 66": "phillips 66",
        "conoco": "conoco",
        "valero": "valero",
        "sinclair": "sinclair",
        "holiday": "holiday",
        "murphy usa": "murphy usa",
        "wawa": "wawa",
        "getgo": "getgo",
        "buc ees": "buc ees",
        "ambest": "ambest",
        "town pump": "town pump",
        "stuckeys": "stuckeys",
        "citgo": "citgo",
    }.items(),
    key=lambda item: -len(item[0]),
)
_GENERIC = re.compile(
    r"\b(travel (center|centers|plaza|stop)|truck (stop|plaza)|truckstop|fuel stop|store)\b"
)


@dataclass(frozen=True)
class Point:
    lat: float
    lng: float
    precision: str  # exit | station
    source: str  # e.g. "osm:node/123"
    detail: str  # matched tags, for spot checks


def norm(text):
    """'Love's #123' -> 'loves 123'."""
    text = text.lower().replace("'", "").replace("’", "").replace(".", "")
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _kind(prefix):
    return {"I": "I", "IH": "I", "US": "US"}.get(prefix or "", "S")


def address_exits(address):
    """[(exit ref, {(kind, number)})] for each 'EXIT n' in a CSV address; routes named before it."""
    text = address.upper()
    everything = {(_kind(m[1]), int(m[2])) for m in _ROUTE.finditer(text)}
    found, previous = [], 0
    for m in _EXIT.finditer(text):
        before = text[previous : m.start()]
        routes = {(_kind(r[1]), int(r[2])) for r in _ROUTE.finditer(before)}
        found.append((m[1] + (m[2] or ""), routes or everything))
        previous = m.end()
    return found


def way_routes(ref):
    """OSM way ref 'I 80;US 6' -> {('I', 80), ('US', 6)}."""
    routes = set()
    for token in re.split(r"[;,]", ref.upper()):
        m = _OSM_REF.match(token.strip())
        if m:
            routes.add((_kind(m[1]), int(m[2])))
    return routes


def _number(ref):
    m = re.match(r"\d+", ref)
    return m[0] if m else ""


def junction_refs(ref):
    return {re.sub(r"[\s-]", "", r).upper() for r in re.split(r"[;/]", ref) if r.strip()}


@lru_cache(maxsize=65536)
def brand(text):
    """Canonical brand of a CSV name or OSM tag, else ''. None-mapped phrases (cardlocks) give ''."""
    s = norm(text)
    for phrase, canonical in _BRANDS:
        if s == phrase or s.startswith(phrase + " "):
            return canonical or ""
    return ""


@lru_cache(maxsize=65536)
def plain_name(text):
    """Name without store numbers or generic suffixes, for unbranded independents."""
    s = _GENERIC.sub(" ", " ".join(w for w in norm(text).split() if not w.isdigit()))
    return re.sub(r"\s+", " ", s).strip()


def km(lat1, lng1, lat2, lng2):
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (
        math.sin((p2 - p1) / 2) ** 2
        + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
    )
    return 2 * 6371.0 * math.asin(math.sqrt(a))


def in_bounds(lat, lng, country):
    min_lat, max_lat, min_lng, max_lng = BOUNDS[country]
    return min_lat <= lat <= max_lat and min_lng <= lng <= max_lng


def match_exit(address, lat, lng, junctions):
    """Nearest junction whose ref is the exit and which sits on a way carrying the named route.

    `junctions`: [(lat, lng, refs, routes, osm id, raw ref)]. An address naming an Interstate needs
    a junction on that Interstate (a shared US route is not enough). Among fits within EXACT_REF_KM
    of the nearest one, an exact ref beats a number-only one ('5A' vs '5').
    """
    found = []
    for exit_ref, routes in address_exits(address):
        number = _number(exit_ref)
        wanted = {r for r in routes if r[0] == "I"} or routes
        for j_lat, j_lng, refs, j_routes, osm_id, raw in junctions:
            if not wanted & j_routes or (exit_ref not in refs and number not in {_number(r) for r in refs}):
                continue
            d = km(lat, lng, j_lat, j_lng)
            if d <= EXIT_KM:
                point = Point(j_lat, j_lng, "exit", f"osm:node/{osm_id}", f"ref={raw} d={d:.1f}km")
                found.append((exit_ref not in refs, d, point))
    if not found:
        return None
    nearest = min(d for _, d, _ in found)
    return min((f for f in found if f[1] <= nearest + EXACT_REF_KM), key=lambda f: f[:2])[2]


def match_station(name, lat, lng, fuels):
    """Same-brand amenity=fuel within STATION_KM of the city, or None when the match is ambiguous.

    `fuels`: [(lat, lng, tags, osm type/id)]. A single truck-tagged candidate wins outright. Otherwise
    the nearest (truck-tagged first) is kept only if every other candidate is within SAME_PUMP_KM of it.
    """
    want, plain = brand(name), plain_name(name)
    found = []
    for f_lat, f_lng, tags, osm_id in fuels:
        labels = [label for label in (tags.get("brand", ""), tags.get("name", "")) if label]
        if want:
            ok = any(brand(label) == want for label in labels)
        else:
            ok = len(plain) >= 4 and any(plain_name(label) == plain for label in labels)
        d = km(lat, lng, f_lat, f_lng) if ok else math.inf
        if d <= STATION_KM:
            truck = tags.get("hgv") in ("yes", "designated") or tags.get("fuel:HGV_diesel") == "yes"
            detail = f"brand={tags.get('brand', '')} name={tags.get('name', '')} hgv={int(truck)} d={d:.1f}km"
            found.append(((not truck, d), Point(f_lat, f_lng, "station", f"osm:{osm_id}", detail)))
    if not found:
        return None
    trucks = [p for (not_truck, _), p in found if not not_truck]
    if len(trucks) == 1:
        return trucks[0]
    best = min(found, key=lambda f: f[0])[1]
    if all(km(best.lat, best.lng, p.lat, p.lng) <= SAME_PUMP_KM for _, p in found):
        return best
    return None
