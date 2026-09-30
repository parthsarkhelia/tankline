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

Station positions. A city centroid can be miles from the pump, so `build_geodata --positions` places
stations at their real exit or pump where OpenStreetMap has it, and writes
`stations/data/station_points.csv.gz` (OPIS ID, lat, lng, precision, OSM source; written to a `.part`
file and renamed), committed. It makes two bulk Overpass queries per state or province (a padded
bounding box around that state's city centroids): every `highway=motorway_junction` node with the
`ref` of the motorway or trunk ways it lies on, and every `amenity=fuel` with its `brand` and `name`.
Responses are cached under `.cache/geodata/` (also `.part` and rename), queries are 5 seconds apart,
and 429/5xx or unreadable answers are retried with backoff on overpass-api.de and then the
maps.mail.ru mirror. No query is made per station, and nothing is queried at request time. A rebuild
from the cache is offline and deterministic.

OSM snapshot: 113 of the 114 cached responses carry an OSM base timestamp of 2026-09-30 (10:22 to
11:55 UTC). One, the Arizona junctions, came from a mirror whose data was as of 2026-05-06: a mirror can
lag the main server by months, so its timestamp is worth checking after a fallback.

- `exit`: the address names an exit ("I-80, EXIT 360", "I-81N, EXIT 2W & I-81S, EXIT 3", "US-30,
  EXIT 186"). Each exit is read with the routes written before it ("I-85, EXIT 39 I-77, EXIT 13" is
  exit 39 on I-85 and exit 13 on I-77; a suffix letter must touch the number or follow a hyphen). A
  candidate is a junction node whose `ref` is that exit number and which lies on a way carrying the
  named route, within 40 km of the city centroid. Exit numbers repeat across highways, so the route
  check is required; when the address names an Interstate the junction must be on that Interstate (a
  shared US route is not enough, or "I-76/US-224, EXIT 1" would land on Akron's I-277/US-224 exit 1).
  The nearest candidate wins; an exact ref ("15E") beats a number-only one ("15" for "EXIT 15E") only
  among candidates within 5 km of the nearest, so a far exact match never beats a near one.
- `station`: otherwise, `amenity=fuel` within 15 km whose brand or name has the same brand as the CSV
  name (Pilot, Flying J, Love's, TA, Petro, Kwik Trip/Kwik Star, QuikTrip, Casey's, Sheetz, Maverik,
  Speedway, Circle K and about 40 more, case and punctuation ignored; "Petro-Card" cardlocks are never
  matched). An unbranded name must equal the OSM name once store numbers and words like "travel
  center" are removed. If exactly one candidate is tagged for trucks (`hgv`, `fuel:HGV_diesel`) it is
  taken. Otherwise the nearest (truck-tagged first) is kept only if every other candidate lies within
  8 km (the 5 mile corridor) of it; if not, the match is ambiguous and the row stays `city`. So
  `station` precision means: a pump of the same brand within 5 miles of the priced one could be the
  one it is; it is not a confirmed identity.
- `city`: the city centroid, as before.

Every point is bounds-checked (lower 48 for US rows, a Canada box for Canadian rows; the same boxes
check Nominatim hits). Result over the 6,738 IDs (14 of which have no city coordinate and are not
loaded):

| | exit | station | city |
| --- | --- | --- | --- |
| US (6,613 rows) | 3,426 (51.8%) | 901 (13.6%) | 2,286 (34.6%) |
| Canada (111 rows) | 28 | 44 | 39 |

65% of US stations sit at an exit or a same-brand pump. Exit matches lie a median 3.9 km (90th
percentile 11.7 km) from their city centroid, which is the error the old placement carried. Known
limits (US and Canada together): 511 rows with same-brand candidates were left at `city` because the candidates were too far
apart to choose; 287 exit addresses matched no junction (exit missing or unnumbered in OSM, or an
address error in the price list, such as "I-42" for I-41). Station data (C) OpenStreetMap
contributors, ODbL.


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
A station at the same position as another that is no dearer and no further off the road is dropped. The work is about 90 microseconds per
station kept; New York to Los Angeles keeps 266 of 385 candidates and takes about 25 ms. The destination acts as a free
station at the end of the line, which makes the last leg buy only what is needed.

Detours. Each station has a one-way detour `d` from its point on the route to the pump (see Corridor).
Passing a station costs nothing. Stopping needs `d` fuel on arrival, buys at the pump (the minimum fill,
the top-up to full and the 50 gallon cap all apply at the pump, and the exact final purchase is what
takes the truck from the pump back to the route and on to the destination), and rejoins the route with
`arrival - 2d + bought`. The vectorised prefix minimum carries over: leaving with level `L` after a
purchase of at least the minimum means the cheapest arrival level `l <= L + 2d - min_fill` with
`l >= d`, and `L` is capped at `tank - d`. Detour fuel is paid through the gallons bought; the
objective also adds `2d x DETOUR_COST_PER_MILE_USD` per stop (below).

Reserve rule. With `start_fuel_miles` at 0 the truck begins empty. Stations within the start city's
corridor count as mile 0, and the plan begins with a fill at the one that makes the trip cheapest. Any
station at the first route position (from an empty start, any start-zone station) whose pump the start
fuel cannot reach may be the first stop on reserve: the truck runs to it, detour to the pump included,
repays that fuel there and must stop there. The programme picks which, and a pump reachable without
reserve does not rule the others out. The first stop then carries `reserve_gallons` on top of its fill.
From an empty start this is just the drive to the first pump; with no station near the start it is the
run down the road as well (the plan then says so). Only first-position stations can be reserve stops: a
pump further down the road is reached on start fuel or after a first stop, never on reserve. Every mile, detours included, is paid for, no fill exceeds 50 gallons, and
`gallons_purchased` equals `gallons_burned`. The alternative, rejecting the trip, would fail for many
short routes that start between stations. A 422 is returned only when a stretch of the route has no
station within 500 miles.

Ties. Because the programme minimises (cost, stops) together, the stop count is exactly minimal among
the cheapest plans. The tests check it against an exhaustive search that models the same rules
(detours, reserve, minimum fill, stop and detour costs) on 4,000 random small cases per combination of
detours (none, or 0 to 2 units at random), stop and detour cost, and minimum fill: 16 combinations,
1,300 or more feasible cases each, compared exactly on (cost with penalties, stops). A simulation also
checks fuel is never negative on the route, at a pump or back at the exit, and never above the tank at
a pump.

Stop cost. Pure fuel cost gives silly plans: New York to Los Angeles took 16 stops, with top-ups of 1.28,
1.90 and 4.50 gallons a few miles after a fill that each saved cents. The objective is therefore fuel cost
plus a per-stop time cost (`STOP_COST_USD`, default $18), which is added once per stop inside the same
exact programme (`stop_cost` in `plan_purchases`), so the result is still checked against the exhaustive
search. The reported `total_cost` stays fuel money only. Derivation:

- ATRI's "Operational Costs of Trucking" 2025 update (2024 data) puts the all-in cost at about $90.89 per
  truck-hour.
- ATRI's 2025 report (2024 data) gives $2.260 per mile in total, of which $1.779 is non-fuel (about
  78.7%). The following year's figures (2025 data, reported by Trucking Info) are $2.336 per mile in
  total and $1.854 non-fuel (about 79.4%). Both years give a non-fuel share of about 79%. A parked truck
  burns no fuel, so the non-fuel share applies: 90.89 x 0.79 is about $72 per hour.
- A fuel stop (exit, queue, pump, pay, re-merge) is assumed to take about 15 minutes. This is an
  assumption, not a published figure; 10 to 20 minutes gives $12 to $24.
- 72 x 0.25 is about $18 per stop.

Sources: [ATRI operational costs](https://truckingresearch.org/about-atri/atri-research/operational-costs-of-trucking/);
[ATRI 2025 report summary (2024 data)](https://truckingresearch.org/2025/07/new-atri-report-shows-trucking-profitability-severly-squeezed-by-high-costs-low-rates/);
[Trucking Info, 2025 data](https://www.truckinginfo.com/news/trucking-fleets-faced-record-operating-costs-during-third-year-of-freight-recession).
Detour cost. A detour is driven, so it costs what a truck mile costs apart from fuel (the fuel itself is
bought and counted): $1.854 per mile, ATRI's non-fuel operating cost per mile in 2025 data (Trucking
Info, above). `DETOUR_COST_PER_MILE_USD` sets it (0 to 20, about 10 times ATRI's figure, which keeps the optimiser's
integer keys in range; 0 counts detour fuel only). Like the stop
cost it only chooses the plan; `total_cost` stays fuel money. The two costs use different ATRI years on purpose, both cited above: the stop
cost's hourly figure comes from ATRI's 2025 report (2024 data), the detour cost's per-mile figure from
2025 data (Trucking Info).

Measured on the real price list with OSRM routing (city-centroid positions, before detours were
counted): New York to Los Angeles goes from 16 stops (fuel
$852.67, smallest fill 1.28 gal) at $0 to 7 stops (fuel $860.10, smallest fill 18.53 gal) at $18;
Chicago to Denver goes from 6 stops ($292.38, 10.0 gal) to 3 stops ($293.48, 23.7 gal). Set
`STOP_COST_USD=0` for pure fuel cost.

Positions and detours, same routes and prices (OSRM, $18 stop cost, in-process, route cached):

| Route | Before: city centroids, detours free | After: exit/station positions, detours counted |
| --- | --- | --- |
| New York to Los Angeles | 7 stops, fuel $860.10, 281.04 gal, all 7 stops `city` | 7 stops, fuel $872.53, 281.68 gal, 6.4 detour miles, 6 `exit` + 1 `city` |
| Chicago to Denver | 3 stops, fuel $293.48, 100.27 gal, all 3 `city` | 3 stops, fuel $297.76, 100.39 gal, 1.2 detour miles, 3 `exit` |

The fuel bill rises because the old plans used stations whose centroid sat near the road while the pump
may not; the new plans pay for the fuel to reach each pump. With `DETOUR_COST_PER_MILE_USD=0` New York
to Los Angeles picks two city-centroid stations 5 miles off the road instead (fuel $869.47, 30.2 detour
miles): the $1.854 per mile is what keeps plans on exit stations. Planning New York to Los Angeles
in-process with the route cached takes about 60 ms (45 ms before; 385 candidates now).

## Corridor

A station placed at an exit or pump is a candidate if it lies within 5 miles of the route. A station
still at its city centroid is uncertain by up to the city's extent, so it is a candidate if the route
passes within `min(5 + city radius, 20)` miles of the centroid: the 5 mile base covers a route through a
small town, the radius covers a route that clips the edge of a large city, and the 20 mile cap keeps a
huge city from pulling in stations far from the road.

The one-way detour to a candidate is its straight-line offset from the route times a road factor of
1.3, rounded to a tenth of a mile. Both numbers are assumptions: 1.3 stands for road circuity (a road is
longer than the straight line), and exit and station positions get a floor of 0.2 mile each way for the
ramp and the lot, since the matched point is a junction node or the pump's centre, not the pump. A city-centroid station gets the same formula on its centroid offset with a floor of 1 mile each way
(`CITY_DETOUR_FLOOR_MILES`), also an assumption: its pump could be anywhere in town, so a centroid that
happens to sit on the route must not make the station look free to reach.

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

A request normally makes one external call; at most three, or four in the rare case where both places
need the fallback geocoder and OpenRouteService also fails. `City, ST`, ZIP and `lat,lng` input resolves
locally, so the typical request makes only the routing call. Geocoder answers are cached for 30 days, routes for 7 days
(10 minutes if OSRM answered because ORS failed, so a real truck route replaces it soon), and the
finished plan is cached under a key that includes the station data version, so a repeated request costs
0 calls and about 2 ms. Redis holds the cache under Docker; without it a file cache in `.cache/` keeps
development routes across restarts.

ORS is limited to 200 requests per day on the free key. `ORS_DAILY_BUDGET` (default 150) counts calls
per UTC day and sends the rest to OSRM. ORS errors 2004 (route too long), 2009 (no route) and 2010 (no
road near a point) are returned as 422; 403, 429, 5xx, timeouts and malformed bodies fall back to OSRM.
