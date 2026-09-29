# tankline

Given a start and a finish inside the contiguous USA, tankline plans the cheapest fuel stops for a truck
(500 mile range, 10 mpg, 50 gallon tank) using the provided OPIS price list. It returns the route, each
fuel stop with the gallons bought and the cost, and the total money spent on fuel. It minimises fuel
cost only; tolls, hours of service, traffic and brand preferences are out of scope.

```
GET /api/v1/plan/?start=Detroit, MI&finish=Chicago, IL
```

The response below is trimmed (route geometry and some per-stop fields left out) and was generated from the
synthetic stations in `tests/fixtures/`, not from the provided price list.

```json
{
  "route": {"distance_miles": 277.5, "duration_hours": 5.3, "provider": "osrm", "profile": "driving"},
  "fuel_stops": [
    {"stop": 1, "name": "SAMPLE STOP 17", "city": "Detroit", "state": "MI", "price_per_gallon": "3.990",
     "mile": 0.0, "gallons": "13.37", "reserve_gallons": "0.00", "cost": "53.35"},
    {"stop": 2, "name": "SAMPLE STOP 29", "city": "Kalamazoo", "state": "MI", "price_per_gallon": "3.930",
     "mile": 133.7, "gallons": "6.17", "reserve_gallons": "0.00", "cost": "24.25"},
    {"stop": 3, "name": "SAMPLE STOP 7", "city": "Bridgman", "state": "MI", "price_per_gallon": "3.890",
     "mile": 195.4, "gallons": "6.11", "reserve_gallons": "0.00", "cost": "23.77"},
    {"stop": 4, "name": "SAMPLE STOP 26", "city": "Hammond", "state": "IN", "price_per_gallon": "3.720",
     "mile": 256.5, "gallons": "2.10", "reserve_gallons": "0.00", "cost": "7.81"}
  ],
  "summary": {"stops": 4, "gallons_purchased": "27.75", "gallons_burned": "27.75", "total_cost": "109.18",
              "start_fuel_miles": 0.0, "range_miles": 500, "mpg": 10},
  "assumptions": ["Assumes the truck starts with an empty tank and fills up at the cheapest station near the start. ..."],
  "meta": {"external_calls": 0, "cache_hit": true, "elapsed_ms": 139},
  "map_url": "http://localhost:8000/map/?start=Detroit%2C+MI&finish=Chicago%2C+IL&start_fuel_miles=0"
}
```

`map_url` opens a Leaflet map of the route and stops.

## Quick start

The provided fuel price CSV is not in this repository. Copy it to `data/fuel-prices.csv` first. It is
git-ignored and never baked into the Docker image.

### Docker (Postgres and Redis included)

```bash
cp .env.example .env        # optional: add an ORS key and set ROUTING_PROVIDER=ors for truck routing
docker compose up --build
```

On start the container migrates the database, loads the stations from the CSV and starts gunicorn.
If `data/fuel-prices.csv` is missing it exits with a message saying so. When it is up:

- API: <http://localhost:8000/api/v1/plan/?start=Chicago, IL&finish=Denver, CO>
- Map: <http://localhost:8000/map/?start=Chicago, IL&finish=Denver, CO>
- Interactive docs: <http://localhost:8000/api/docs/>
- Health: <http://localhost:8000/healthz> (503 until stations are loaded)

### Local (SQLite)

```bash
uv sync
uv run --env-file .env manage.py migrate
uv run --env-file .env manage.py load_stations
uv run --env-file .env manage.py runserver
```

Every `manage.py` command needs `--env-file .env` so Django finds its secret key (or set `DJANGO_DEBUG=1`).

`ROUTING_PROVIDER` defaults to `osrm`: car routing on the public OSRM demo server, no key needed. Set
`ROUTING_PROVIDER=ors` and `ORS_API_KEY` for OpenRouteService truck routing (`driving-hgv`), which falls
back to OSRM on quota, timeout or server errors. The response says which one answered in `route.provider`.

## API

`GET /api/v1/plan/`, all parameters in the query string.

| Parameter | Required | Description |
| --- | --- | --- |
| `start` | yes | Start place (max 120 characters). |
| `finish` | yes | Finish place. |
| `start_fuel_miles` | no | Range already in the tank at the start, 0 to 500. Default 0 (empty tank). |

Accepted place formats: `City, ST` (or full state name, or `City ST`), a 5 digit ZIP code (or ZIP+4),
and `lat,lng`. Other text is sent to the geocoder. A city name that exists in several states, such as
`Springfield`, is rejected with the candidate list; add the state.

| Response field | Meaning |
| --- | --- |
| `start`, `finish` | The place as resolved: name, coordinates, and what resolved it. |
| `route` | Distance in miles, duration in hours, routing `provider`, `profile`, and GeoJSON `geometry`. |
| `fuel_stops[]` | Stops in route order. |
| `fuel_stops[].mile` | Distance from the start along the route. |
| `fuel_stops[].price_per_gallon` | Station price, 3 decimals. |
| `fuel_stops[].gallons`, `cost` | Gallons bought at the stop and their cost (`gallons` times the 3 decimal price, to the cent). |
| `fuel_stops[].reserve_gallons` | Gallons burned before the first station and paid for there (see Assumptions). Zero after the first stop. |
| `fuel_stops[].off_route_miles` | Straight-line offset of the city centre from the route. |
| `fuel_stops[].country`, `lat`, `lng`, `location_precision` | Station country and position; precision is always `city`. |
| `summary.total_cost` | Total fuel cost, USD. |
| `summary.gallons_burned` | Fuel used by the whole route (distance / 10 mpg). |
| `summary.gallons_purchased` | Gallons paid for; equals `gallons_burned` on an empty-tank start. |
| `assumptions[]` | Notes that apply to this plan. |
| `meta` | `external_calls` made for this request, `cache_hit`, `elapsed_ms`. |
| `map_url` | Map page for the same request (recomputed from cache if warm; never 404s). |

Errors are `{"error": {"code": ..., "message": ...}}`.

| Status | `error.code` | When |
| --- | --- | --- |
| 400 | `invalid_request` | Missing or malformed parameter. |
| 400 | `location_not_found` | Place, ZIP or text could not be resolved. |
| 400 | `ambiguous_location` | Several places match; `candidates` lists them. |
| 400 | `outside_service_area` | Alaska, Hawaii, Canada, Mexico or open water. |
| 400 | `same_location` | Start and finish are the same place. |
| 422 | `route_not_possible` | The routing service found no route, or the route is too long. |
| 422 | `fuel_gap` | A stretch of route has no station within 500 miles. |
| 429 | `throttled` | Rate limit (30 requests per minute per address by default). |
| 502 | `routing_unavailable` | Neither routing provider answered. |

Interactive docs are at `/api/docs/` (OpenAPI schema at `/api/schema/`). A Postman collection with eight
requests and status assertions is in [`postman/tankline.postman_collection.json`](postman/tankline.postman_collection.json).

## How it works

Detail and measurements are in [docs/design.md](docs/design.md).

**Data.** `load_stations` reads the CSV, keeps the lowest price for each OPIS ID, drops unreadable or
implausible prices and places each station at its city's coordinates. The city coordinates are committed
(`stations/data/`), so no geocoding happens at request time and no price data is committed.

**Routing.** One routing call per new start/finish pair. OpenRouteService with the `driving-hgv` profile
is the primary provider, the public OSRM server the fallback. Routes and plans are cached for a week
(fallback routes for 10 minutes), so a repeated request makes no external call.

**Corridor.** Stations are placed on the route by projecting their city coordinates onto it. A station
is a candidate if it lies within `min(5 + city radius, 20)` miles of the route, so large cities count
from further out than small towns.

**Borders.** Canadian stations are kept in the data but are candidates only where the route itself is in
Canada. A point of unknown country (water, border slivers) does not exclude a station.

**Optimiser.** Fuel price along a route is linear in gallons, so a greedy rule is optimal: at each
stop, if a cheaper station is within one tank, buy just enough to reach it, otherwise fill up and go to
the cheapest station in range. Ties are resolved towards fewer stops.

## Assumptions

- Stations sit at their city's coordinates, not their street address. 6,724 of 6,738 distinct stations
  are placed (14 skipped, no coordinates). A few hundred city coordinates come from OpenStreetMap
  Nominatim, (c) OpenStreetMap contributors, ODbL; the rest from the US Census Gazetteer.
- One row per OPIS ID, lowest price kept. Prices are treated as USD per gallon.
- The truck starts empty unless `start_fuel_miles` says otherwise. Stations within the start city's
  corridor count as mile 0, so the trip begins with a fill at the cheapest of them.
- With no station there, the truck runs on reserve to the first station and repays that fuel there
  (`reserve_gallons`), so every mile is paid for and no fill exceeds 50 gallons.
- 422 `fuel_gap` only when a stretch has no station within 500 miles.
- Cost only: no detour cost, tolls, hours of service or brand preferences.
- Canadian stations count only while the route is in Canada. In the data, Sarnia diesel is $3.31 against
  a US median of $3.40, worth at most about $4.50 per tank, which does not justify a border crossing.
- The OSRM fallback uses a car profile, so its durations are shorter than a truck's. ORS `driving-hgv`
  durations are longer.

## Performance

Manual run on the provided data (OSRM routing): Chicago, IL to Denver, CO is 1,002.7 miles with 7 stops,
100.27 gallons and $292.01. Cold it makes 1 external call and takes about 2.1 s, nearly all of it the
routing call. Repeated, it takes 1.9 ms with 0 external calls. Lookup tables are loaded once before the
gunicorn workers fork (`--preload`), so the first request in each worker is not slower.

Figures measured under Docker Compose are filled in by the final check before submission.

## Development

```bash
uv run pytest                 # synthetic stations, recorded routes, network blocked
uv run ruff check . && uv run ruff format --check .
uv run --env-file .env manage.py build_geodata --fetch-missing   # rebuild coordinate files
uv run --env-file .env manage.py warm_routes                     # before a demo
```

Tests use `tests/fixtures/fuel-prices-sample.csv`: real city coordinates, invented names and prices.

The free OpenRouteService key allows 200 truck routes per day (measured). `ORS_DAILY_BUDGET` (default
150) caps the calls tankline makes; past it, OSRM answers. `warm_routes` plans the four demo routes used
by the Postman collection once, so the demo is served from the cache. It spends ORS quota when
`ROUTING_PROVIDER=ors`, so run it once, not repeatedly.
