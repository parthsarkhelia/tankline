import pytest

from stations.osm import address_exits, brand, junction_refs, match_exit, match_station, way_routes


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("I-80, EXIT 360", [("360", {("I", 80)})]),
        ("I-44, EXIT 283 & US-69", [("283", {("I", 44)})]),
        ("I-75, EXIT 144-B", [("144B", {("I", 75)})]),
        ("I-90 & I-35, EXIT 11", [("11", {("I", 90), ("I", 35)})]),
        ("I-81N, EXIT 2W & I-81S, EXIT 3", [("2W", {("I", 81)}), ("3", {("I", 81)})]),
        ("I-10/US-90, EXIT 591 & FM-1518", [("591", {("I", 10), ("US", 90)})]),
        ("ROADRUNNER: I-81, EXIT 36 NORTH", [("36", {("I", 81)})]),
        ("HWY-401, EXIT 53B & HWY 1", [("53B", {("S", 401)})]),
        ("US-30, EXIT 186", [("186", {("US", 30)})]),
        ("US-281", []),
        ("I-85, EXIT 39 I-77, EXIT 13", [("39", {("I", 85)}), ("13", {("I", 77)})]),
        ("I-5, EXIT 5A-B", [("5A", {("I", 5)})]),
    ],
)
def test_address_exits(address, expected):
    assert address_exits(address) == expected


def test_way_and_junction_refs():
    assert way_routes("I 80;US 6") == {("I", 80), ("US", 6)}
    assert way_routes("I-94") == {("I", 94)}
    assert way_routes("401") == {("S", 401)}
    assert junction_refs("144A;144 B") == {"144A", "144B"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("LOVES TRAVEL STOP #459", "loves"),
        ("Love's Travel Stop", "loves"),
        ("TA BROOKVILLE TRAVEL CENTER", "ta"),
        ("TravelCenters of America", "ta"),
        ("PETRO STOPPING CENTER #12", "petro"),
        ("PETRO-CANADA", "petro canada"),
        ("PETRO-CARD 24 #910410", ""),
        ("KWIK STAR #1", "kwik trip"),
        ("TABERNACLE FUEL", ""),
    ],
)
def test_brand(text, expected):
    assert brand(text) == expected


JUNCTIONS = [
    (41.00, -96.00, {"360"}, {("I", 76)}, 1, "360"),  # same number, wrong interstate
    (41.10, -96.10, {"360"}, {("I", 80)}, 2, "360"),
    (41.02, -96.02, {"360A"}, {("I", 80)}, 3, "360A"),  # nearer and number-only: too far to prefer 2
    (43.00, -96.00, {"360"}, {("I", 80)}, 4, "360"),  # too far from the city
]


def test_exit_needs_the_route_and_prefers_the_near_fit():
    point = match_exit("I-80, EXIT 360", 41.0, -96.0, JUNCTIONS)
    assert point is not None and point.source == "osm:node/3" and point.precision == "exit"
    assert match_exit("I-29, EXIT 360", 41.0, -96.0, JUNCTIONS) is None


def test_station_prefers_the_single_truck_stop():
    fuels = [
        (41.001, -96.0, {"brand": "Pilot"}, "node/1"),
        (41.12, -96.0, {"brand": "Pilot", "hgv": "yes"}, "way/2"),  # 13 km away, the only truck stop
        (41.0, -96.0, {"brand": "Shell"}, "node/3"),
        (42.0, -96.0, {"brand": "Pilot", "hgv": "yes"}, "node/4"),  # outside 15 km
    ]
    point = match_station("PILOT TRAVEL CENTER #352", 41.0, -96.0, fuels)
    assert point is not None and point.source == "osm:way/2" and point.precision == "station"


def test_same_brand_pumps_far_apart_are_ambiguous():
    near = [(41.0, -96.0, {"brand": "Circle K"}, "node/1"), (41.05, -96.0, {"brand": "Circle K"}, "node/2")]
    point = match_station("CIRCLE K #1", 41.0, -96.0, near)  # 5.6 km apart: either is within the corridor
    assert point is not None and point.source == "osm:node/1"
    far = [*near, (41.1, -96.0, {"brand": "Circle K"}, "node/3")]  # 11 km from the nearest
    assert match_station("CIRCLE K #1", 41.0, -96.0, far) is None
    trucks = [
        *far,
        (41.1, -96.1, {"brand": "Circle K", "hgv": "yes"}, "node/4"),
        (40.9, -96.0, {"brand": "Circle K", "hgv": "yes"}, "node/5"),
    ]
    assert match_station("CIRCLE K #1", 41.0, -96.0, trucks) is None  # two truck stops 22 km apart
    unbranded = [(41.0, -96.0, {"name": "Woodshed of Big Cabin"}, "node/9")]
    assert match_station("WOODSHED OF BIG CABIN", 41.0, -96.0, unbranded) is not None


def _east(lat, lng, km_east):
    return lat, lng + km_east / (111.32 * 0.755)  # about 41 degrees north


def test_a_near_fit_beats_a_far_exact_ref():
    # Duncan Truck Stop, Jersey City NJ, "I-95, EXIT 15": New Jersey's 15E is 5 km away; New York's
    # I-95 exit 15 is 30 km away. The exact ref must not pull the station across the Hudson.
    city = (40.72, -74.05)
    nj = (*_east(*city, 5), {"15E"}, {("I", 95)}, 10, "15E")
    ny = (*_east(*city, 30), {"15"}, {("I", 95)}, 11, "15")
    point = match_exit("I-95, EXIT 15", *city, [ny, nj])
    assert point is not None and point.source == "osm:node/10"


def test_exact_ref_still_wins_between_close_fits():
    city = (40.72, -74.05)
    number_only = (*_east(*city, 1), {"15E"}, {("I", 95)}, 10, "15E")
    exact = (*_east(*city, 4), {"15"}, {("I", 95)}, 11, "15")
    point = match_exit("I-95, EXIT 15", *city, [number_only, exact])
    assert point is not None and point.source == "osm:node/11"


def test_an_interstate_exit_needs_that_interstate():
    # TA Lodi, Seville OH, "I-76/US-224, EXIT 1": Akron's I-277/US-224 exit 1 shares only US-224.
    city = (41.01, -81.86)
    akron = (*_east(*city, 2), {"1"}, {("I", 277), ("US", 224)}, 20, "1")
    lodi = (*_east(*city, 3), {"1"}, {("I", 76)}, 21, "1")
    point = match_exit("I-76/US-224, EXIT 1", *city, [akron, lodi])
    assert point is not None and point.source == "osm:node/21"
    assert match_exit("I-76/US-224, EXIT 1", *city, [akron]) is None


def test_two_exits_in_one_address_keep_their_own_interstates():
    # Pilot #275, Charlotte NC, "I-85, EXIT 39 I-77, EXIT 13": no exit 13 on I-85, no 39I.
    city = (35.23, -80.84)
    wrong = (*_east(*city, 1), {"13"}, {("I", 85)}, 30, "13")
    right = (*_east(*city, 5), {"39"}, {("I", 85)}, 31, "39")
    also = (*_east(*city, 8), {"13"}, {("I", 77)}, 32, "13")
    point = match_exit("I-85, EXIT 39 I-77, EXIT 13", *city, [wrong, right, also])
    assert point is not None and point.source == "osm:node/31"
