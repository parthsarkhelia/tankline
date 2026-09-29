"""Which country a point on the route is in (US, CA, or unknown)."""

import json
from functools import lru_cache

import numpy as np
import shapely

from stations.geo import BORDERS_FILE


def load_borders(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    borders = []
    for feature in data["features"]:
        geometry = shapely.from_geojson(json.dumps(feature["geometry"]))
        shapely.prepare(geometry)
        borders.append((feature["properties"]["country"], geometry))
    return tuple(borders)


@lru_cache(maxsize=1)
def default_borders():
    return load_borders(BORDERS_FILE)


def country_codes(lng, lat, borders=None):
    codes = np.full(len(lng), "", dtype="<U2")
    for code, geometry in borders if borders is not None else default_borders():
        codes[shapely.contains_xy(geometry, lng, lat)] = code
    return codes
