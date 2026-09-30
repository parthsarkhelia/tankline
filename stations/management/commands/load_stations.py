"""Load stations and prices from data/fuel-prices.csv.

A station sits at its OpenStreetMap exit or pump when one was matched (station_points.csv.gz),
else at its city's centroid.
"""

import uuid
from decimal import Decimal
from pathlib import Path

from django.core.cache import cache
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from stations.geo import CANADIAN_PROVINCES, normalize
from stations.models import Station
from stations.prices import (
    DEFAULT_CSV,
    STATIONS_VERSION_KEY,
    dedupe_lowest_price,
    read_locations,
    read_points,
    read_price_rows,
)

MAX_ID = 2**31 - 1  # PositiveIntegerField range on every database backend


class Command(BaseCommand):
    help = "Load fuel stations and prices from the fuel price CSV, data/fuel-prices.csv (replaces rows)."

    def add_arguments(self, parser):
        parser.add_argument("--csv", default=str(DEFAULT_CSV))

    def handle(self, *args, **options):
        path = Path(options["csv"])
        if not path.exists():
            raise CommandError(f"{path} not found; it ships with the repository (see README).")
        try:
            rows = dedupe_lowest_price(read_price_rows(path))
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        locations, points = read_locations(), read_points()
        stations, skipped = [], 0
        for row in rows:
            place = locations.get((normalize(row["City"]), row["State"]))
            try:
                station_id, rack_id = int(row["OPIS Truckstop ID"]), int(row["Rack ID"] or 0)
            except ValueError:
                station_id = rack_id = -1
            if not (0 < station_id <= MAX_ID and 0 <= rack_id <= MAX_ID):
                place = None
            if place is None:
                skipped += 1
                continue
            point = points.get(station_id)
            stations.append(
                Station(
                    opis_id=station_id,
                    name=row["Truckstop Name"][:120],
                    address=row["Address"][:200],
                    city=row["City"][:80],
                    state=row["State"][:2],
                    country="CA" if row["State"] in CANADIAN_PROVINCES else "US",
                    rack_id=rack_id,
                    price=Decimal(row["Retail Price"]),
                    lat=point.lat if point else place.lat,
                    lng=point.lng if point else place.lng,
                    radius_miles=place.radius_miles,
                    location_precision=point.precision if point else "city",
                    geocode_source="osm" if point else place.source,
                )
            )
        with transaction.atomic():
            Station.objects.all().delete()
            Station.objects.bulk_create(stations, batch_size=1000)
        cache.set(STATIONS_VERSION_KEY, uuid.uuid4().hex, None)  # running servers reload; old plans expire
        self.stdout.write(f"stations: {len(stations)} loaded, {skipped} skipped (no coordinates or bad row)")
