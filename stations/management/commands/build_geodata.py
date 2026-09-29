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
import urllib.parse
import urllib.request
import zipfile

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
from stations.prices import DEFAULT_CSV, LOCATIONS_FILE, dedupe_lowest_price, read_price_rows

GAZETTEER_URL = (
    "https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_{}_national.zip"
)
BORDERS_URL = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/v5.1.2/geojson/ne_10m_admin_0_countries.geojson"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "tankline-geodata/1.0 (+https://github.com/parthsarkhelia/tankline)"
OVERRIDES_FILE = DATA_DIR / "geocode_overrides.csv"
CACHE_DIR = settings.BASE_DIR / ".cache" / "geodata"
BORDER_BBOX = (-130.0, 24.0, -60.0, 53.0)
DEFAULT_RADIUS_MILES = 2.0  # Nominatim gives a point, not an area
# Sanity boxes for Nominatim hits (min lat, max lat, min lng, max lng): a wrong hit must not be committed.
NOMINATIM_BOXES = {"us": (24.3, 49.5, -125.0, -66.8), "ca": (41.6, 70.0, -141.0, -52.0)}
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
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})  # noqa: S310  fixed https URL
        with urllib.request.urlopen(request, timeout=120) as response:  # noqa: S310  fixed https URLs
            partial.write_bytes(response.read())
        partial.replace(target)
    return target


def _gazetteer(kind):
    with zipfile.ZipFile(_download(GAZETTEER_URL.format(kind))) as archive:
        name = next(n for n in archive.namelist() if n.endswith(".txt"))
        text = io.TextIOWrapper(archive.open(name), encoding="utf-8")
        for row in csv.DictReader(text, delimiter="|"):
            yield {k.strip(): v.strip() for k, v in row.items()}


class Command(BaseCommand):
    help = "Geocode the CSV's cities and build the offline lookup files."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(DEFAULT_CSV))
        parser.add_argument(
            "--fetch-missing", action="store_true", help="Look up unmatched cities on Nominatim (1 req/s)."
        )

    def handle(self, *args, **options):
        try:
            rows = dedupe_lowest_price(read_price_rows(options["csv"]))
        except (OSError, ValueError) as exc:
            raise CommandError(f"Cannot read the fuel CSV: {exc}") from exc
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
            query = urllib.parse.urlencode(
                {"city": city, "state": state, "countrycodes": country, "format": "json", "limit": 1}
            )
            request = urllib.request.Request(f"{NOMINATIM_URL}?{query}", headers={"User-Agent": USER_AGENT})  # noqa: S310  fixed https URL
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310  fixed https URL
                hits = json.load(response)
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
