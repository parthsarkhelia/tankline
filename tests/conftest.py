from pathlib import Path

import pytest
from django.core.management import call_command

SAMPLE_CSV = Path(__file__).parent / "fixtures" / "fuel-prices-sample.csv"


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Tests never open sockets; `responses` mocks HTTP above the socket layer."""

    def blocked(*args, **kwargs):
        raise RuntimeError("network access in tests")

    monkeypatch.setattr("socket.socket.connect", blocked)


@pytest.fixture(autouse=True)
def empty_cache():
    """Routes, plans, throttle history and the ORS day counter start empty in every test."""
    from django.core.cache import cache

    cache.clear()


@pytest.fixture
def load_stations(django_db_blocker):
    """Synthetic stations: real city coordinates, invented names and prices (created in the services task)."""
    with django_db_blocker.unblock():
        call_command("load_stations", csv=str(SAMPLE_CSV), verbosity=0)
