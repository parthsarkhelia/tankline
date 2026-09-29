"""Place-name normalisation and the offline lookup tables built by build_geodata."""

import csv
import gzip
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent / "data"
PLACES_FILE = DATA_DIR / "places.tsv.gz"
ZCTA_FILE = DATA_DIR / "zcta.tsv.gz"
BORDERS_FILE = DATA_DIR / "borders.geojson"

US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "DC": "District of Columbia",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois",
    "IN": "Indiana", "IA": "Iowa", "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana",
    "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota",
    "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma", "OR": "Oregon",
    "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota",
    "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia",
    "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
}  # fmt: skip
CANADIAN_PROVINCES = frozenset({"AB", "BC", "MB", "NB", "NL", "NS", "NT", "NU", "ON", "PE", "QC", "SK", "YT"})

_ABBREVIATIONS = (
    (" saint ", " st "), (" st. ", " st "), (" ft. ", " fort "), (" ft ", " fort "),
    (" mt. ", " mount "), (" mt ", " mount "),
)  # fmt: skip
# Census appends a legal/statistical area type ("city", "CDP", ...) to every NAME.
AREA_SUFFIX = re.compile(
    r"\s+(city and borough|charter township|city|town|village|cdp|borough|township|municipality"
    r"|ccd|plantation|urban county|(consolidated|metropolitan|unified) government.*)$",
    re.IGNORECASE,
)
_CONSOLIDATED = re.compile(r"-.*? (metropolitan|consolidated|unified) government.*$", re.IGNORECASE)
# "Lexington-Fayette urban county", "Macon-Bibb County", "Butte-Silver Bow", "Louisville/Jefferson County ..."
_CITY_COUNTY = re.compile(r"^([^-/]+)[-/].*(\bcounty\b|\bgovernment\b|\(balance\))", re.IGNORECASE)


def normalize(text):
    """Lower-case, expand common abbreviations, drop punctuation, collapse spaces."""
    s = f" {text.strip().lower()} "
    for old, new in _ABBREVIATIONS:
        s = s.replace(old, new)
    s = s.replace(".", "").replace("'", "").replace("-", " ")
    s = re.sub(r"\bmc ", "mc", s)
    return re.sub(r"\s+", " ", s).strip()


STATE_BY_NAME = {normalize(name): code for code, name in US_STATES.items()}


def place_keys(name):
    """Lookup keys for a Gazetteer NAME: as written, without area type, and consolidated-city short form."""
    raw = name.strip()
    name = re.sub(r"\s*\(balance\)$", "", raw)
    variants = {name, AREA_SUFFIX.sub("", name), AREA_SUFFIX.sub("", _CONSOLIDATED.sub("", name))}
    if match := _CITY_COUNTY.match(raw):  # city-county names only, so "Winston-Salem" gains no "Winston"
        variants.add(match[1])
    return {normalize(v) for v in variants if v}


@dataclass(frozen=True)
class PlaceRecord:
    name: str
    state: str
    lat: float
    lng: float
    radius_miles: float


@lru_cache(maxsize=1)
def places_index():
    index = {}
    with gzip.open(PLACES_FILE, "rt", encoding="utf-8") as fh:
        for key, state, name, lat, lng, radius in csv.reader(fh, delimiter="\t"):
            index.setdefault(key, []).append(PlaceRecord(name, state, float(lat), float(lng), float(radius)))
    return index


@lru_cache(maxsize=1)
def zcta_index():
    with gzip.open(ZCTA_FILE, "rt", encoding="utf-8") as fh:
        return {zip_code: (float(lat), float(lng)) for zip_code, lat, lng in csv.reader(fh, delimiter="\t")}
