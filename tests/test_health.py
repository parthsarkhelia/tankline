import pytest


@pytest.mark.django_db
def test_healthz_reports_empty_station_table(client):
    response = client.get("/healthz")
    assert response.status_code == 503
    assert response.json() == {"status": "no stations loaded"}
