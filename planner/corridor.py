"""Place stations along a route: distance off the route and mile marker along it."""

from dataclasses import dataclass

import numpy as np
import shapely

EARTH_RADIUS_MI = 3958.8
MILES_PER_DEG_LAT = 69.0
MILES_PER_DEG_LNG_MIN = 45.0  # at 49°N, the northern edge of the service area
SIMPLIFY_DEG = 0.002  # ~0.14 mi; far below the city-centroid error of station positions
CHUNK = 512


@dataclass(frozen=True)
class Placement:
    index: np.ndarray  # rows of the station arrays inside the corridor
    mile: np.ndarray  # along-route position, miles from origin
    offset: np.ndarray  # distance off the route, miles
    lng: np.ndarray  # nearest point on the route
    lat: np.ndarray


def haversine_mi(lng1, lat1, lng2, lat2):
    lng1, lat1, lng2, lat2 = map(np.radians, (lng1, lat1, lng2, lat2))
    a = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * EARTH_RADIUS_MI * np.arcsin(np.sqrt(a))


def simplify_route(route_lnglat, tolerance_deg=0.001):
    return shapely.get_coordinates(shapely.simplify(shapely.linestrings(route_lnglat), tolerance_deg))


def place_stations(  # pylint: disable=too-many-locals  # vectorised projection keeps its arrays together
    route_lnglat, route_miles, st_lng, st_lat, max_offset
):
    """Project stations onto the route; keep those within their own max_offset (miles)."""
    empty = Placement(*(np.array([], dtype=t) for t in (int, float, float, float, float)))
    if len(st_lng) == 0 or len(route_lnglat) < 2:
        return empty
    pts = simplify_route(route_lnglat, SIMPLIFY_DEG)
    if len(pts) < 2:
        return empty
    seg_len = haversine_mi(pts[:-1, 0], pts[:-1, 1], pts[1:, 0], pts[1:, 1])
    cum = np.concatenate([[0.0], np.cumsum(seg_len)])
    scale = route_miles / cum[-1] if cum[-1] > 0 else 0.0

    # Coarse GEOS prefilter in degrees, generous in longitude; exact miles below.
    line = shapely.linestrings(pts)
    shapely.prepare(line)
    rows = np.flatnonzero(
        shapely.dwithin(line, shapely.points(st_lng, st_lat), float(max_offset.max()) / MILES_PER_DEG_LNG_MIN)
    )
    if rows.size == 0:
        return empty

    # Local planar frame per station: longitude scaled by cos(latitude).
    ax, ay = pts[:-1, 0], pts[:-1, 1]
    dx, dy = pts[1:, 0] - ax, pts[1:, 1] - ay
    best_d = np.empty(rows.size)
    best_seg = np.empty(rows.size, dtype=int)
    best_t = np.empty(rows.size)
    for start in range(0, rows.size, CHUNK):
        r = rows[start : start + CHUNK]
        px, py = st_lng[r][:, None], st_lat[r][:, None]
        k = np.cos(np.radians(py))
        sdx, sdy = dx * k, dy
        vx, vy = (px - ax) * k, py - ay
        denom = sdx**2 + sdy**2
        t = np.clip(np.divide(vx * sdx + vy * sdy, denom, out=np.zeros_like(vx), where=denom > 0), 0.0, 1.0)
        d2 = (vx - t * sdx) ** 2 + (vy - t * sdy) ** 2
        seg = d2.argmin(axis=1)
        pick = np.arange(r.size)
        best_seg[start : start + r.size] = seg
        best_t[start : start + r.size] = t[pick, seg]
        best_d[start : start + r.size] = np.sqrt(d2[pick, seg]) * MILES_PER_DEG_LAT

    keep = best_d <= max_offset[rows]
    seg, t = best_seg[keep], best_t[keep]
    return Placement(
        index=rows[keep],
        mile=np.clip((cum[seg] + t * seg_len[seg]) * scale, 0.0, route_miles),
        offset=best_d[keep],
        lng=ax[seg] + t * dx[seg],
        lat=ay[seg] + t * dy[seg],
    )
