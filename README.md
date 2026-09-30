# tankline

Given a start and a finish inside the contiguous USA, tankline plans the cheapest fuel stops for a truck
(500 mile range, 10 mpg, 50 gallon tank) using the OPIS price list in `data/fuel-prices.csv`. It returns the route, each
fuel stop with the gallons bought and the cost, and the total money spent on fuel. It minimises fuel
cost plus a per-stop time cost; tolls, hours of service, traffic and brand preferences are out of scope.

```
GET /api/v1/plan/?start=Detroit, MI&finish=Chicago, IL
```

The response below is trimmed (route geometry and some per-stop fields left out). It is a first, uncached
request on the real price list with OSRM routing.

```json
{
  "route": {
    "distance_miles": 277.5,
    "duration_hours": 5.3,
    "provider": "osrm",
    "profile": "driving"
  },
  "fuel_stops": [
    { "stop": 1, "name": "EXXON", "city": "Redford", "state": "MI", "price_per_gallon": "3.699", "mile": 10.7, "off_route_miles": 0.7, "detour_miles": 2.0, "gallons": "27.85", "reserve_gallons": "0.10", "cost": "103.39", "location_precision": "city" }
  ],
  "summary": {
    "stops": 1,
    "gallons_purchased": "27.95",
    "gallons_burned": "27.95",
    "total_cost": "103.39",
    "start_fuel_miles": 0.0,
    "range_miles": 500,
    "mpg": 10,
    "stop_cost_usd": "18.00",
    "detour_cost_per_mile_usd": "1.854",
    "detour_miles": 2.0,
    "route_miles_driven": 279.5
  },
  "assumptions": [
    "Assumes the truck starts with an empty tank and fills up at the station near the start that makes the trip cheapest; ..."
  ],
  "meta": {
    "external_calls": 1,
    "cache_hit": false,
    "elapsed_ms": 2049
  },
  "map_url": "http://localhost:8000/map/?start=Detroit%2C+MI&finish=Chicago%2C+IL&start_fuel_miles=0"
}
```

`map_url` opens a Leaflet map of the route and stops.

## Quick start

Prices come from `data/fuel-prices.csv`, a retail diesel price list of US and Canadian truck stops that is
included in the repository.

### Docker (Postgres and Redis included)

```bash
docker compose up --build
```

No `.env` is needed. Optionally `cp .env.example .env`, add an ORS key and set `ROUTING_PROVIDER=ors`
for truck routing; compose picks it up. Without a key the app uses the public OSRM server (car profile)
automatically. Port 8000 busy? `TANKLINE_PORT=8080 docker compose up --build`.

On start the container migrates the database, loads the stations from the CSV and starts gunicorn.
When it is up:

- API: <http://localhost:8000/api/v1/plan/?start=Chicago%2C+IL&finish=Denver%2C+CO>
- Map: <http://localhost:8000/map/?start=Chicago%2C+IL&finish=Denver%2C+CO>
- Interactive docs: <http://localhost:8000/api/docs/>
- Health: <http://localhost:8000/healthz> (503 until stations are loaded)

### Local (SQLite)

```bash
cp .env.example .env
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
| `fuel_stops[].reserve_gallons` | Gallons burned before the first pump (at least the drive to it) and paid for there (see Assumptions). Zero after the first stop. |
| `fuel_stops[].off_route_miles` | Straight-line offset of the station position from the route. |
| `fuel_stops[].detour_miles` | Estimated road miles to the pump and back (1.3 x offset each way, at least 0.2 mile for exit and station positions, 1 mile for city positions). |
| `fuel_stops[].country`, `lat`, `lng`, `location_precision` | Station country and position; precision is `exit` (OpenStreetMap exit), `station` (a same-brand OpenStreetMap pump, possibly one of several within 5 miles) or `city` (city centre). |
| `summary.total_cost` | Total fuel cost, USD (stop and detour costs are not included). |
| `summary.stop_cost_usd`, `detour_cost_per_mile_usd` | Per-stop time cost and per-detour-mile cost the plan was optimised with, USD. |
| `summary.detour_miles`, `route_miles_driven` | All detours, and route distance plus detours. |
| `summary.gallons_burned` | Fuel used by the route and the detours (`route_miles_driven` / 10 mpg). |
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

Diagrams (standalone HTML; open in a browser): [architecture](docs/diagrams/architecture.html), [request sequence](docs/diagrams/request-sequence.html), [station data pipeline](docs/diagrams/station-data.html), [optimiser](docs/diagrams/optimiser.html).

**Data.** `load_stations` reads the CSV, keeps the lowest price for each OPIS ID, drops unreadable or
implausible prices and places each station at its OpenStreetMap exit or fuel station when one was
matched, else at its city's coordinates. Both are committed (`stations/data/`), so no geocoding happens
at request time.

**Routing.** One routing call per new start/finish pair. OpenRouteService with the `driving-hgv` profile
is the primary provider, the public OSRM server the fallback. Routes and plans are cached for a week
(fallback routes for 10 minutes), so a repeated request makes no external call.

**Corridor.** Stations are placed on the route by projecting their coordinates onto it. A station at an
exit or pump is a candidate within 5 miles of the route; one at a city centre within
`min(5 + city radius, 20)` miles, so large cities count from further out than small towns.

**Borders.** Canadian stations are kept in the data but are candidates only where the route itself is in
Canada. A point of unknown country (water, border slivers) does not exclude a station.

**Optimiser.** An exact dynamic programme over fuel levels finds the cheapest purchases under the
minimum-fill rule, counting a per-stop time cost and each detour to the pump (its fuel, its range and a
per-mile cost). Ties go to the plan with fewer stops.

## Assumptions

- Stations sit at their OpenStreetMap exit (52% of US stations) or at a matched same-brand fuel station
  (14%), else at their city's coordinates (35%). 6,724 of 6,738 distinct stations are placed (14
  skipped, no coordinates). An exit match needs the exit number on the named Interstate. A station
  match is kept only when every same-brand candidate lies within 5 miles of the chosen one (or it is
  the only truck stop), so `station` means a same-brand pump within 5 miles of the priced one could be
  the one; otherwise the station stays at `city`. Exit and station positions and a few hundred city
  coordinates come from OpenStreetMap (Overpass and Nominatim, data as of 2026-09-30), (c)
  OpenStreetMap contributors, ODbL; the other cities from the US Census Gazetteer.
- One row per OPIS ID, lowest price kept. Prices are treated as USD per gallon.
- The truck starts empty unless `start_fuel_miles` says otherwise. Stations within the start city's
  corridor count as mile 0, so the trip begins with a fill at one of them; the drive to its pump is
  paid there as `reserve_gallons`.
- With no station there, the truck runs on reserve to the first station and repays that fuel there
  (`reserve_gallons`), so every mile is paid for and no fill exceeds 50 gallons.
- Detours count: a stop drives to the pump and back (1.3 x the straight-line offset each way, an
  assumed road factor; at least 0.2 mile each way at an exit or station, 1 mile for a station known only by its city), needs that fuel on arrival,
  buys within the tank at the pump, and costs $1.854 per detour mile on top of the fuel (ATRI non-fuel
  cost per mile; `DETOUR_COST_PER_MILE_USD`).
- Minimum fill 10 gal per stop, unless the stop fills the tank to full or is the final purchase needed to reach the destination.
- 422 `fuel_gap` only when a stretch has no station within 500 miles.
- Optimal = cheapest fuel plus an $18 per-stop time cost (from ATRI operating-cost data; see docs/design.md), so plans skip trivial top-ups. Set STOP_COST_USD=0 for pure fuel cost.
- No tolls, hours of service or brand preferences. Detours are straight-line estimates, not routed.
- Canadian stations count only while the route is in Canada. In the data, Sarnia diesel is $3.31 against
  a US median of $3.40, worth at most about $4.50 per tank, which does not justify a border crossing.
- The OSRM fallback uses a car profile, so its durations are shorter than a truck's. ORS `driving-hgv`
  durations are longer.

## Performance

Docker Compose on an Apple Silicon laptop, real price list, routing by the public OSRM demo server. Figures are from
a warm server (`--preload` loads the lookup tables once before the workers fork) and vary run to run.

| Request | Time |
| --- | --- |
| New York, NY to Los Angeles, CA, cold (1 external call) | 1.03 to 1.53 s end to end |
| of which the routing provider (`fetch_route` timed directly, 5 calls) | 0.41 to 1.45 s |
| of which our planning (route cached, new `start_fuel_miles`) | 58 to 105 ms |
| Same request, cached (0 external calls) | 1 to 6 ms |
| Miami, FL to Seattle, WA, warm server, route cached, new plan | 64 to 69 ms |

"New plan" means a new `start_fuel_miles` value: the route comes from the cache and the fuel plan is recomputed over
the full station set. The routing provider dominates a cold request; planning stays around 0.1 s or less.

## Development

```bash
uv run pytest                 # synthetic stations, recorded routes, network blocked
uv run ruff check . && uv run ruff format --check . && uv run pyright
uv run pylint config planner stations tests
uv run --env-file .env manage.py build_geodata --fetch-missing   # rebuild coordinate files
uv run --env-file .env manage.py build_geodata --positions       # rebuild exit/station positions (Overpass)
uv run --env-file .env manage.py warm_routes                     # before a demo
```

In VS Code select `.venv` as the Python interpreter so the Pylint and Pylance extensions load the project's plugins and stubs.

Tests use `tests/fixtures/fuel-prices-sample.csv`: real city coordinates, invented names and prices.

The free OpenRouteService key allows 200 truck routes per day (measured). `ORS_DAILY_BUDGET` (default
150) caps the calls tankline makes; past it, OSRM answers. `warm_routes` plans the four demo routes used
by the Postman collection once, so the demo is served from the cache. It spends ORS quota when
`ROUTING_PROVIDER=ors`, so run it once, not repeatedly.
