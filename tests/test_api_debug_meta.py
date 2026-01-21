from datetime import datetime
from zoneinfo import ZoneInfo

from starlette.testclient import TestClient

from tripscore.api.app import app
from tripscore.config.settings import get_settings
from tripscore.ingestion.weather_client import WeatherSummary


class _StubTdxClient:
    def get_bus_stops_bulk(self, *, city: str | None = None):
        return []

    def get_bike_stations_bulk(self, *, city: str | None = None):
        return []

    def get_parking_lots_bulk(self, *, city: str | None = None):
        return []

    def get_metro_stations_bulk(self, *, operators: list[str] | None = None):
        return []


class _StubWeatherClient:
    def get_summary(self, *, lat: float, lon: float, start: datetime, end: datetime):
        # Return a stable, non-error value (avoid network).
        return WeatherSummary(max_precipitation_probability=20, mean_temperature_c=26.0)


def test_api_recommendations_includes_debug_meta(monkeypatch):
    # Patch the cached clients factory so API tests stay offline.
    import tripscore.api.routes as routes

    monkeypatch.setattr(routes, "_clients", lambda: (_StubTdxClient(), _StubWeatherClient()))

    settings = get_settings()
    tz = ZoneInfo(settings.app.timezone)
    start = datetime(2026, 1, 5, 10, 0, tzinfo=tz)
    end = datetime(2026, 1, 5, 18, 0, tzinfo=tz)

    payload = {
        "origin": {"lat": 25.0478, "lon": 121.5170},
        "time_window": {"start": start.isoformat(), "end": end.isoformat()},
        "max_results": 3,
    }

    with TestClient(app) as c:
        resp = c.post("/api/recommendations", json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert "meta" in data
    assert "debug" in data["meta"]
    assert data["meta"]["debug"]["request_id"]
    assert isinstance(data["meta"]["debug"]["api_ms"], int)
