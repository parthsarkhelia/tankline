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
    (41.02, -96.02, {"360A"}, {("I", 80)}, 3, "360A"),  # nearer, but number-only
    (43.00, -96.00, {"360"}, {("I", 80)}, 4, "360"),  # too far from the city
]


def test_exit_needs_the_route_and_prefers_the_exact_ref():
    point = match_exit("I-80, EXIT 360", 41.0, -96.0, JUNCTIONS)
    assert point is not None and point.source == "osm:node/2" and point.precision == "exit"
    assert match_exit("I-29, EXIT 360", 41.0, -96.0, JUNCTIONS) is None


def test_station_prefers_truck_tags_then_distance():
    fuels = [
        (41.001, -96.0, {"brand": "Pilot"}, "node/1"),
        (41.05, -96.0, {"brand": "Pilot", "hgv": "yes"}, "way/2"),
        (41.0, -96.0, {"brand": "Shell"}, "node/3"),
        (42.0, -96.0, {"brand": "Pilot", "hgv": "yes"}, "node/4"),  # outside 15 km
    ]
    point = match_station("PILOT TRAVEL CENTER #352", 41.0, -96.0, fuels)
    assert point is not None and point.source == "osm:way/2" and point.precision == "station"
    unbranded = [(41.0, -96.0, {"name": "Woodshed of Big Cabin"}, "node/9")]
    assert match_station("WOODSHED OF BIG CABIN", 41.0, -96.0, unbranded) is not None
