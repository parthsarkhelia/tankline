from decimal import Decimal

import pytest
from django.core.management import CommandError, call_command

from stations.management.commands.build_geodata import radius_from_area
from stations.models import Station
from stations.prices import dedupe_lowest_price, read_price_rows

HEADER = "OPIS Truckstop ID,Truckstop Name,Address,City,State,Rack ID,Retail Price\n"


def test_dedupe_keeps_lowest_price_per_id():
    rows = [
        {"OPIS Truckstop ID": "910001", "Truckstop Name": "SAMPLE STOP A", "Retail Price": "3.459"},
        {"OPIS Truckstop ID": "910001", "Truckstop Name": "SAMPLE STOP A DUP", "Retail Price": "3.359"},
        {"OPIS Truckstop ID": "910002", "Truckstop Name": "SAMPLE STOP B", "Retail Price": "3.12345678"},
        {"OPIS Truckstop ID": "910003", "Truckstop Name": "SAMPLE BAD PRICE", "Retail Price": "abc"},
        {"OPIS Truckstop ID": "910004", "Truckstop Name": "SAMPLE NAN", "Retail Price": "NaN"},
        {"OPIS Truckstop ID": "910005", "Truckstop Name": "SAMPLE ZERO", "Retail Price": "0"},
        {
            "OPIS Truckstop ID": "910006",
            "Truckstop Name": "SAMPLE TINY",
            "Retail Price": "1E-400",
        },  # would store as 0
    ]
    result = dedupe_lowest_price(rows)
    assert {r["OPIS Truckstop ID"]: Decimal(r["Retail Price"]) for r in result} == {
        "910001": Decimal("3.359"),
        "910002": Decimal("3.12345678"),
    }


def test_missing_columns_are_named(tmp_path):
    path = tmp_path / "bad.csv"
    path.write_text("id,price\n1,3.0\n")
    with pytest.raises(ValueError, match="Truckstop Name"):
        read_price_rows(path)


def test_radius_from_area_is_equivalent_circle():
    assert round(radius_from_area(3.14159), 2) == 1.0
    assert radius_from_area(0) == 0.0


@pytest.mark.django_db
def test_load_stations_joins_prices_to_committed_locations(tmp_path, client):
    path = tmp_path / "prices.csv"
    path.write_text(
        HEADER
        + '910001,SAMPLE STOP A,"I-00, EXIT 1",Chicago ,IL,1,3.459\n'
        + '910001,SAMPLE STOP A DUP,"I-00, EXIT 1",Chicago,IL,1,3.359\n'
        + "910002,SAMPLE BORDER STOP,HWY 00,Sarnia,ON,1,3.21\n"
        + "910003,SAMPLE NOWHERE STOP,I-00,Nowhereville,KS,1,3.05\n"
        + "910004,SAMPLE BAD PRICE,I-00,Chicago,IL,1,abc\n"
        + "x,SAMPLE BAD ID,I-00,Chicago,IL,1,3.10\n"
    )
    call_command("load_stations", csv=str(path), verbosity=0)
    rows = {s.opis_id: s for s in Station.objects.all()}
    assert set(rows) == {910001, 910002}
    assert rows[910001].price == Decimal("3.359") and rows[910001].country == "US"
    assert rows[910001].location_precision == "city" and rows[910001].city == "Chicago"
    assert rows[910002].country == "CA"
    call_command("load_stations", csv=str(path), verbosity=0)  # safe to re-run on every boot
    assert Station.objects.count() == 2
    assert client.get("/healthz").json() == {"status": "ok"}


@pytest.mark.parametrize(
    ("text", "state"),
    [("lexington", "KY"), ("macon", "GA"), ("boise", "ID"), ("butte", "MT"), ("st louis", "MO")],
)
def test_consolidated_cities_resolve_offline(text, state):
    from stations.geo import places_index

    assert any(p.state == state for p in places_index().get(text, []))


@pytest.mark.django_db
def test_load_stations_explains_a_missing_csv(tmp_path):
    with pytest.raises(CommandError, match="README"):
        call_command("load_stations", csv=str(tmp_path / "missing.csv"))


def test_dedupe_treats_zero_padded_ids_as_one_station():
    rows = [
        {"OPIS Truckstop ID": "0910009", "Truckstop Name": "SAMPLE PADDED", "Retail Price": "3.50"},
        {"OPIS Truckstop ID": "910009", "Truckstop Name": "SAMPLE PLAIN", "Retail Price": "3.40"},
    ]
    result = dedupe_lowest_price(rows)
    assert [Decimal(r["Retail Price"]) for r in result] == [Decimal("3.40")]


@pytest.mark.django_db
def test_load_stations_accepts_zero_padded_duplicate_ids(tmp_path):
    path = tmp_path / "prices.csv"
    path.write_text(
        HEADER
        + "0910009,SAMPLE PADDED,I-00,Chicago,IL,1,3.50\n"
        + "910009,SAMPLE PLAIN,I-00,Chicago,IL,1,3.40\n"
    )
    call_command("load_stations", csv=str(path), verbosity=0)
    assert [(s.opis_id, s.price) for s in Station.objects.all()] == [(910009, Decimal("3.40"))]
