# API reference

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
requests and status assertions is in [`postman/tankline.postman_collection.json`](../postman/tankline.postman_collection.json).
