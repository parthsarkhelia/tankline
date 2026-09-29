# Design notes

## Data pipeline

The price list has 8,151 rows and 6,738 distinct OPIS truckstop IDs, so many stations appear
more than once (different rack IDs or products). `load_stations` keeps one row per ID, the lowest price.
Rows whose price cannot be read or is implausible (zero or negative, NaN, $20 or more) are skipped. The
CSV header may carry padding spaces, as it does when a spreadsheet re-saves the file; column names and
values are stripped on read.

The CSV has no coordinates, only city and state. `build_geodata` matches each (city, state) to a
coordinate and writes the result to `stations/data/station_locations.csv.gz`, which is committed. Of
the 6,738 stations, 6,724 are placed and 14 are skipped at load time because their city has no
coordinate. 6,428 come from the Census Gazetteer 2025 (places and county subdivisions) and 296 from
OpenStreetMap Nominatim, looked up once with `build_geodata --fetch-missing` and committed as data. A
Gazetteer match also gives the city's land area, converted to an equivalent radius. That radius sizes
the corridor described below. The same script builds the place index used to resolve `City, ST` input,
the ZIP code index and the country borders (Natural Earth).

The 620 Canadian rows are kept. Whether one is used depends on where the route is, not on the row.

The price file is committed as `data/fuel-prices.csv` and copied into the Docker image; the tests use an
invented sample instead.

## Optimiser

The route is a line. Positions are integer tenths of a mile and fuel is measured as miles of range in
the tank, so at 10 mpg one unit is 0.01 gallon and no float rounding enters the plan. Prices are linear
in gallons (no volume discounts), and fuel bought earlier can be carried forward at no cost until the
tank is full.

The optimiser is an exact dynamic programme over fuel levels. The state at each station is the fuel in
the tank on arrival, in tenths of a mile. For every station a vectorised prefix minimum finds the
cheapest way to leave with each fuel level, so a purchase of any size is considered without looping over
sizes. A stop pumps nothing, at least the minimum fill (10 gallons), or whatever fills the tank to full
(a tank too full to take 10 gallons can still be topped up). The one other exception is the final
purchase that is exactly what reaches the destination. Ties on cost go to the plan with fewer stops.
Stations sharing a position keep only the cheapest. The work is about 55 microseconds per distinct
position; Miami to Seattle has 154 positions and takes about 9 ms. The destination acts as a free
station at the end of the line, which makes the last leg buy only what is needed.

Reserve rule. With `start_fuel_miles` at 0 the truck begins empty. Stations within the start city's
corridor count as mile 0, and the plan begins with a fill at the cheapest of them. If none is in the
zone, the first station is some distance down the road. The truck is assumed to reach it on reserve and
to repay that fuel there: the first stop carries `reserve_gallons` on top of its fill. Every mile is
paid for, no fill exceeds 50 gallons, and `gallons_purchased` equals `gallons_burned`. The alternative,
rejecting the trip, would fail for many short routes that start between stations. A 422 is returned
only when a stretch of the route has no station within 500 miles.

Ties. Because the programme minimises (cost, stops) together, the stop count is exactly minimal among
the cheapest plans, and it is checked against an exhaustive search in the tests.

## Corridor

Stations come with city coordinates, not street addresses, so their true position is uncertain by up
to the city's extent. A station is a candidate if the route passes within `min(5 + city radius, 20)`
miles of its city coordinate: the 5 mile base covers a route through a small town, the radius covers a
route that clips the edge of a large city, and the 20 mile cap keeps a huge city from pulling in
stations far from the road.

Distance and projection are computed per station in a local planar frame: longitude is scaled by the
cosine of the station's latitude and degrees are converted to miles. Over an offset of at most 20 miles
this is accurate to well under the centroid uncertainty. The route geometry is first simplified with a
tolerance of 0.002 degrees (about 0.14 mile), far below the same uncertainty, which keeps the projection cheap. A coarse GEOS `dwithin` prefilter removes
distant stations before the exact computation, and the projected position gives each station's `mile`.
Mile positions are scaled so the last point equals the routing service's own distance.

## Borders

Each candidate's country must equal the country of the route at its projected point (Natural Earth
polygons, tested with `shapely.contains_xy`). A US station is therefore never used where the route is in
Canada and the other way round; a route from Detroit to Buffalo through Ontario may use Canadian
stations there. A point that falls in no polygon (a lake, a border sliver) has unknown country and the
station is kept. Start and finish must be in the contiguous US: Alaska, Hawaii, Canada and Mexico are
rejected by state, bounding box and border polygons with `outside_service_area`.

## Caching and call budget

A request normally makes one external call and at most three: one geocoder call per free-text place
(start and finish) and one routing call. In the rare case both places need the fallback geocoder and
ORS also fails, it is four. `City, ST`, ZIP and `lat,lng` input resolves locally, so the typical
request makes one call, the routing call. Geocoder answers are cached for 30 days, routes for 7 days
(10 minutes if OSRM answered because ORS failed, so a real truck route replaces it soon), and the
finished plan is cached under a key that includes the station data version, so a repeated request costs
0 calls and about 2 ms. Redis holds the cache under Docker; without it a file cache in `.cache/` keeps
development routes across restarts.

ORS is limited to 200 requests per day on the free key. `ORS_DAILY_BUDGET` (default 150) counts calls
per UTC day and sends the rest to OSRM. ORS errors 2004 (route too long), 2009 (no route) and 2010 (no
road near a point) are returned as 422; 403, 429, 5xx, timeouts and malformed bodies fall back to OSRM.
