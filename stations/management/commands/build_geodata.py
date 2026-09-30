"""Build city coordinates and lookup tables from the fuel CSV's cities and public gazetteers.

Run once; the outputs are committed so the API never geocodes at request time. Only
coordinates are written: station names and prices stay in the (uncommitted) CSV.
"""

import csv
import gzip
import io
import json
import math
import re
import time
import zipfile

import requests
import shapely
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from stations.geo import (
    AREA_SUFFIX,
    BORDERS_FILE,
    CANADIAN_PROVINCES,
    DATA_DIR,
    PLACES_FILE,
    US_STATES,
    ZCTA_FILE,
    normalize,
    place_keys,
)
from stations.osm import in_bounds, junction_refs, match_exit, match_station, way_routes
from stations.prices import (
    DEFAULT_CSV,
    LOCATIONS_FILE,
    POINTS_FILE,
    dedupe_lowest_price,
    read_locations,
    read_price_rows,
)

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_{}_national.zip"
)
BORDERS_URL = (
    "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2"
    "/geojson/ne_10m_admin_0_countries.geojson"
)
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "tankline-geodata/1.0 (+https://github.com/parthsarkhelia/tankline)"
OVERRIDES_FILE = DATA_DIR / "geocode_overrides.csv"
CACHE_DIR = settings.BASE_DIR / ".cache" / "geodata"
BORDER_BBOX = (-130.0, 24.0, -60.0, 53.0)
DEFAULT_RADIUS_MILES = 2.0  # Nominatim gives a point, not an area
# Sanity boxes for Nominatim hits (min lat, max lat, min lng, max lng): a wrong hit must not be committed.
NOMINATIM_BOXES = {"us": (24.3, 49.5, -125.0, -66.8), "ca": (41.6, 70.0, -141.0, -52.0)}
# Overpass (OpenStreetMap, ODbL). The mirror is tried when the main server refuses or times out.
OVERPASS_URLS = (
    "https://overpass-api.de/api/interpreter",
    "https://maps.mail.ru/osm/tools/overpass/api/interpreter",
)
OVERPASS_PAUSE = 5.0  # seconds between live queries: be polite to a shared public service
OVERPASS_RETRIES = 4
PAD_LAT, PAD_LNG = 0.4, 0.6  # bbox padding (deg) around a state's city centroids: covers 40 km at 49N
JUNCTIONS_QUERY = """[out:json][timeout:600][maxsize:536870912][bbox:{bbox}];
way[highway~"^(motorway|trunk)$"][ref]->.w;
.w out body;
node(w.w)[highway=motorway_junction][ref];
out;"""
FUEL_QUERY = """[out:json][timeout:600][maxsize:536870912][bbox:{bbox}];
nwr[amenity=fuel];
out center tags;"""
# Census names a few consolidated cities in a way no general rule recovers.
ALIASES = {("boise", "ID"): ("boise city", "ID")}


def radius_from_area(square_miles):
    return math.sqrt(square_miles / math.pi) if square_miles > 0 else 0.0


def place_display_name(name):
    """'Oklahoma City city' -> 'Oklahoma City'."""
    return AREA_SUFFIX.sub("", re.sub(r"\s*\(balance\)$", "", name.strip()))


def _download(url):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / url.rsplit("/", 1)[-1]
    if not target.exists():
        partial = target.with_name(target.name + ".part")  # an interrupted download is never reused
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=120)
        response.raise_for_status()
        partial.write_bytes(response.content)
        partial.replace(target)
    return target


def _gazetteer(kind):
    with zipfile.ZipFile(_download(GAZETTEER_URL.format(kind))) as archive:
        name = next((n for n in archive.namelist() if n.endswith(".txt")), None)
        if name is None:
            raise CommandError("no .txt file in gazetteer archive")
        with archive.open(name) as raw, io.TextIOWrapper(raw, encoding="utf-8") as text:
            for row in csv.DictReader(text, delimiter="|"):
                yield {k.strip(): v.strip() for k, v in row.items()}


class Command(BaseCommand):
    help = "Geocode the CSV's cities and build the offline lookup files."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(DEFAULT_CSV))
        parser.add_argument(
            "--positions",
            action="store_true",
            help="Only build station_points.csv.gz from OpenStreetMap exits and stations (Overpass).",
        )
        parser.add_argument(
            "--fetch-missing", action="store_true", help="Look up unmatched cities on Nominatim (1 req/s)."
        )

    def handle(self, *args, **options):
        try:
            rows = dedupe_lowest_price(read_price_rows(options["csv"]))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read the fuel CSV: {exc}") from exc
        if options["positions"]:
            self._write_points(rows)
            return
        places = self._build_places()
        self._build_zcta()
        self._build_borders()
        overrides = self._read_overrides()
        if options["fetch_missing"]:
            overrides |= self._fetch_missing(rows, places, overrides)
            self._write_overrides(overrides)
        self._write_locations(rows, places, overrides)

    def _build_places(self):
        index = {}
        for kind in ("place", "cousubs"):  # places win over county subdivisions
            for row in _gazetteer(kind):
                if row["USPS"] not in US_STATES:
                    continue
                record = (
                    place_display_name(row["NAME"]),
                    float(row["INTPTLAT"]),
                    float(row["INTPTLONG"]),
                    radius_from_area(float(row["ALAND_SQMI"])),
                )
                for key in place_keys(row["NAME"]):
                    index.setdefault((key, row["USPS"]), record)
        for alias, target in ALIASES.items():
            if target in index:
                index.setdefault(alias, index[target])
        with gzip.open(PLACES_FILE, "wt", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter="\t")
            for (key, state), (name, lat, lng, radius) in sorted(index.items()):
                writer.writerow([key, state, name, f"{lat:.6f}", f"{lng:.6f}", f"{radius:.2f}"])
        self.stdout.write(f"places: {len(index)} keys")
        return index

    def _build_zcta(self):
        with gzip.open(ZCTA_FILE, "wt", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh, delimiter="\t")
            for row in _gazetteer("zcta"):
                writer.writerow([row["GEOID"], row["INTPTLAT"], row["INTPTLONG"]])

    def _build_borders(self):
        data = json.loads(_download(BORDERS_URL).read_text(encoding="utf-8"))
        box = shapely.box(*BORDER_BBOX)
        features = []
        for feature in data["features"]:
            code = {"USA": "US", "CAN": "CA", "MEX": "MX"}.get(feature["properties"]["ADM0_A3"])
            if not code:
                continue
            geometry = shapely.from_geojson(json.dumps(feature["geometry"]))
            geometry = shapely.simplify(shapely.intersection(geometry, box), 0.001)
            features.append(
                {
                    "type": "Feature",
                    "properties": {"country": code},
                    "geometry": json.loads(shapely.to_geojson(shapely.set_precision(geometry, 0.0001))),
                }
            )
        if {f["properties"]["country"] for f in features} != {"US", "CA", "MX"}:
            raise CommandError("Border file is missing the US, Canada or Mexico.")
        BORDERS_FILE.write_text(
            json.dumps({"type": "FeatureCollection", "features": features}), encoding="utf-8"
        )

    def _read_overrides(self):
        if not OVERRIDES_FILE.exists():
            return {}
        with OVERRIDES_FILE.open(encoding="utf-8") as fh:
            return {
                (normalize(r["city"]), r["state"]): (float(r["lat"]), float(r["lng"]), r["source"])
                for r in csv.DictReader(fh)
            }

    def _write_overrides(self, overrides):
        with OVERRIDES_FILE.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["city", "state", "lat", "lng", "source"])
            for (city, state), (lat, lng, source) in sorted(overrides.items()):
                writer.writerow([city, state, f"{lat:.6f}", f"{lng:.6f}", source])

    def _fetch_missing(self, rows, places, overrides):
        missing = sorted(
            {(r["City"], r["State"]) for r in rows if self._locate(r, places, overrides) is None}
        )
        found = {}
        for city, state in missing:
            country = "ca" if state in CANADIAN_PROVINCES else "us"
            params = {"city": city, "state": state, "countrycodes": country, "format": "json", "limit": 1}
            response = requests.get(
                NOMINATIM_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=30
            )
            response.raise_for_status()
            hits = response.json()
            min_lat, max_lat, min_lng, max_lng = NOMINATIM_BOXES[country]
            lat, lng = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else (0.0, 0.0)
            if hits and min_lat <= lat <= max_lat and min_lng <= lng <= max_lng:
                found[(normalize(city), state)] = (lat, lng, "nominatim")
            else:
                self.stderr.write(f"not found or outside {country.upper()}: {city}, {state}")
            time.sleep(1.1)  # Nominatim usage policy: at most 1 request per second
        self.stdout.write(f"nominatim: {len(found)} of {len(missing)} resolved")
        return found

    @staticmethod
    def _locate(row, places, overrides):
        key = (normalize(row["City"]), row["State"])
        if key in places:
            _, lat, lng, radius = places[key]
            return lat, lng, radius, "gazetteer"
        if key in overrides:
            lat, lng, source = overrides[key]
            return lat, lng, DEFAULT_RADIUS_MILES, source
        return None

    def _write_locations(self, rows, places, overrides):
        located, unmatched = {}, set()
        for row in rows:
            key = (normalize(row["City"]), row["State"])
            found = self._locate(row, places, overrides)
            if found is None:
                unmatched.add(key)
            else:
                located[key] = found
        with gzip.open(LOCATIONS_FILE, "wt", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["city_key", "state", "lat", "lng", "radius_miles", "source"])
            for (city, state), (lat, lng, radius, source) in sorted(located.items()):
                writer.writerow([city, state, f"{lat:.6f}", f"{lng:.6f}", f"{radius:.2f}", source])
        placed = sum((normalize(r["City"]), r["State"]) in located for r in rows)
        self.stdout.write(
            f"locations: {len(located)} cities; {placed} of {len(rows)} stations placed; "
            f"{len(unmatched)} cities unmatched"
        )
        for city, state in sorted(unmatched)[:20]:
            self.stdout.write(f"  unmatched: {city}, {state}")

    def _write_points(self, rows):
        locations = read_locations()
        by_state = {}
        for row in rows:
            place = locations.get((normalize(row["City"]), row["State"]))
            if place is not None and row["OPIS Truckstop ID"].isdigit():
                by_state.setdefault(row["State"], []).append((row, place))
        points, counts = {}, {}
        for state, located in sorted(by_state.items()):
            country = "CA" if state in CANADIAN_PROVINCES else "US"
            bbox = _bbox([p for _, p in located])
            self.stdout.write(f"{state}: querying {bbox}")
            junctions = _junctions(_overpass(JUNCTIONS_QUERY, bbox, f"junctions-{state}", self.stderr.write))
            fuels = _fuels(_overpass(FUEL_QUERY, bbox, f"fuel-{state}", self.stderr.write))
            for row, place in located:
                point = match_exit(row["Address"], place.lat, place.lng, junctions)
                if point is None or not in_bounds(point.lat, point.lng, country):
                    point = match_station(row["Truckstop Name"], place.lat, place.lng, fuels)
                if point is not None and not in_bounds(point.lat, point.lng, country):
                    point = None
                if point is not None:
                    points[int(row["OPIS Truckstop ID"])] = point
                precision = point.precision if point is not None else "city"
                counts[(country, precision)] = counts.get((country, precision), 0) + 1
            self.stdout.write(f"{state}: {len(located)} stations, {len(junctions)} exits, {len(fuels)} fuel")
        with gzip.open(POINTS_FILE, "wt", encoding="utf-8", newline="") as fh:
            writer = csv.writer(fh)
            writer.writerow(["opis_id", "lat", "lng", "precision", "source"])
            for opis_id, p in sorted(points.items()):
                writer.writerow([opis_id, f"{p.lat:.6f}", f"{p.lng:.6f}", p.precision, p.source])
        for (country, precision), n in sorted(counts.items()):
            self.stdout.write(f"points: {country} {precision} {n}")


def _bbox(places):
    lats, lngs = [p.lat for p in places], [p.lng for p in places]
    south, north = min(lats) - PAD_LAT, max(lats) + PAD_LAT
    west, east = min(lngs) - PAD_LNG, max(lngs) + PAD_LNG
    return f"{south:.3f},{west:.3f},{north:.3f},{east:.3f}"


def _overpass(query, bbox, name, log):
    """One bulk Overpass query, cached; retries with backoff and falls back to the mirror."""
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    target = CACHE_DIR / f"overpass-{name}.json"
    if target.exists():
        return json.loads(target.read_text(encoding="utf-8"))
    for attempt in range(OVERPASS_RETRIES):
        for url in OVERPASS_URLS:
            try:
                response = requests.post(
                    url,
                    data={"data": query.format(bbox=bbox)},
                    headers={"User-Agent": USER_AGENT},
                    timeout=(15, 700),
                )
                response.raise_for_status()
                data = json.loads(response.content)  # an HTML "too busy" page or a cut-off body fails here
            except (requests.RequestException, ValueError) as exc:
                log(f"{name}: {url} failed ({exc.__class__.__name__}: {str(exc)[:80]})")
                continue
            if "remark" in data:  # Overpass reports a timeout or memory limit here, after partial output
                raise CommandError(f"Overpass {name}: {data['remark']}; split the region or retry.")
            partial = target.with_name(target.name + ".part")
            partial.write_bytes(response.content)
            partial.replace(target)
            time.sleep(OVERPASS_PAUSE)
            return data
        time.sleep(30 * 2**attempt)
    raise CommandError(f"Overpass unavailable for {name}; try again later.")


def _junctions(data):
    """[(lat, lng, refs, routes, id, ref)]: motorway exits with the routes of the ways they lie on."""
    routes = {}
    for el in data["elements"]:
        if el["type"] == "way":
            carried = way_routes(el.get("tags", {}).get("ref", ""))
            for node in el.get("nodes", ()):
                routes.setdefault(node, set()).update(carried)
    return [
        (
            el["lat"],
            el["lon"],
            junction_refs(el["tags"]["ref"]),
            routes.get(el["id"], set()),
            el["id"],
            el["tags"]["ref"],
        )
        for el in data["elements"]
        if el["type"] == "node"
    ]


def _fuels(data):
    """[(lat, lng, tags, 'type/id')] for every amenity=fuel node, way or relation."""
    found = []
    for el in data["elements"]:
        centre = el if el["type"] == "node" else el.get("center")
        if centre:
            found.append((centre["lat"], centre["lon"], el.get("tags", {}), f"{el['type']}/{el['id']}"))
    return found
