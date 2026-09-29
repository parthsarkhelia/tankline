"""The fuel price CSV (data/fuel-prices.csv) and the committed city coordinates."""

import csv
import gzip
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from django.conf import settings

from .geo import DATA_DIR

DEFAULT_CSV = settings.BASE_DIR / "data" / "fuel-prices.csv"
LOCATIONS_FILE = DATA_DIR / "station_locations.csv.gz"
COLUMNS = ("OPIS Truckstop ID", "Truckstop Name", "Address", "City", "State", "Rack ID", "Retail Price")
MIN_PRICE = Decimal("0.5")  # USD/gal; outside [MIN, MAX) is a data error, not a bargain
MAX_PRICE = Decimal(20)
STATIONS_VERSION_KEY = "stations:version"


@dataclass(frozen=True)
class CsvLocation:
    lat: float
    lng: float
    radius_miles: float
    source: str


def read_price_rows(path):
    with Path(path).open(encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh, skipinitialspace=True)  # quoted fields may follow a padding space
        header = [h.strip() for h in next(reader, [])]  # spreadsheets re-save with padded names
        missing = [c for c in COLUMNS if c not in header]
        if missing:
            raise ValueError(f"CSV is missing columns: {', '.join(missing)}")
        index = {c: header.index(c) for c in COLUMNS}
        return [{c: (row[i] if i < len(row) else "").strip() for c, i in index.items()} for row in reader]


def dedupe_lowest_price(rows):
    """One row per OPIS ID, keeping the lowest price; unreadable or implausible prices are dropped."""
    best = {}
    for row in rows:
        try:
            price = Decimal(row["Retail Price"])
        except ArithmeticError:
            continue
        if not (price.is_finite() and MIN_PRICE <= price < MAX_PRICE):
            continue
        raw = row["OPIS Truckstop ID"]
        key = int(raw) if raw.isascii() and raw.isdigit() else raw  # "0910001" and "910001" are one station
        if key not in best or price < best[key][0]:
            best[key] = (price, row)
    return [row for _, row in best.values()]


def read_locations(path=LOCATIONS_FILE):
    with gzip.open(path, "rt", encoding="utf-8", newline="") as fh:
        return {
            (r["city_key"], r["state"]): CsvLocation(
                float(r["lat"]), float(r["lng"]), float(r["radius_miles"]), r["source"]
            )
            for r in csv.DictReader(fh)
        }
