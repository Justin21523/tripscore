import json

from tripscore.config.settings import get_settings
from tripscore.core.cache import FileCache
from tripscore.ingestion.tdx_client import TdxClient


def test_get_bus_stops_stageable_bulk_resume(monkeypatch, tmp_path):
    settings = get_settings()
    retry = settings.ingestion.tdx.retry.model_copy(
        update={"max_attempts": 0, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}
    )
    bulk = settings.ingestion.tdx.bulk.model_copy(
        update={"enabled": True, "max_pages_per_call": 1, "max_seconds_per_call": None}
    )
    tdx = settings.ingestion.tdx.model_copy(
        update={
            "client_id": "test",
            "client_secret": "test",
            "request_spacing_seconds": 0.0,
            "retry": retry,
            "bulk": bulk,
            "bus_stops": settings.ingestion.tdx.bus_stops.model_copy(update={"top": 2}),
        }
    )
    ingestion = settings.ingestion.model_copy(update={"tdx": tdx})
    settings = settings.model_copy(update={"ingestion": ingestion})

    monkeypatch.setattr(
        "tripscore.ingestion.tdx_client.post_form",
        lambda *_args, **_kwargs: {"access_token": "token", "expires_in": 3600},
    )

    def fake_get_json(url, *, params=None, headers=None, timeout_seconds=15):  # noqa: ARG001
        skip = int((params or {}).get("$skip", 0))
        if skip == 0:
            return [
                {
                    "StopUID": "a",
                    "StopName": {"Zh_tw": "A"},
                    "StopPosition": {"PositionLat": 25.0, "PositionLon": 121.0},
                },
                {
                    "StopUID": "b",
                    "StopName": {"Zh_tw": "B"},
                    "StopPosition": {"PositionLat": 25.1, "PositionLon": 121.1},
                },
            ]
        if skip == 2:
            return [
                {
                    "StopUID": "c",
                    "StopName": {"Zh_tw": "C"},
                    "StopPosition": {"PositionLat": 25.2, "PositionLon": 121.2},
                }
            ]
        return []

    monkeypatch.setattr("tripscore.ingestion.tdx_client.get_json", fake_get_json)

    cache = FileCache(tmp_path, enabled=True)
    client = TdxClient(settings=settings, cache=cache)

    stops1 = client.get_bus_stops(city="Taipei")
    assert len(stops1) == 2

    progress_path = tmp_path / "tdx_bulk" / "bus_stops" / "city_Taipei.progress.json"
    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["done"] is False
    assert progress["next_skip"] == 2

    stops2 = client.get_bus_stops(city="Taipei")
    assert len(stops2) == 3

    progress = json.loads(progress_path.read_text(encoding="utf-8"))
    assert progress["done"] is True

