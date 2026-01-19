from tripscore.config.settings import get_settings
from tripscore.core.cache import FileCache
from tripscore.ingestion.tdx_client import TdxClient


def test_tdx_global_request_spacing(monkeypatch, tmp_path):
    settings = get_settings()
    retry = settings.ingestion.tdx.retry.model_copy(
        update={"max_attempts": 0, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}
    )
    tdx = settings.ingestion.tdx.model_copy(
        update={
            "client_id": "test",
            "client_secret": "test",
            "request_spacing_seconds": 1.0,
            "retry": retry,
        }
    )
    ingestion = settings.ingestion.model_copy(update={"tdx": tdx})
    settings = settings.model_copy(update={"ingestion": ingestion})

    monkeypatch.setattr(
        "tripscore.ingestion.tdx_client.post_form",
        lambda *_args, **_kwargs: {"access_token": "token", "expires_in": 3600},
    )

    monotonic_values = iter([0.0, 0.2, 1.0])
    monkeypatch.setattr("tripscore.ingestion.tdx_client.time.monotonic", lambda: next(monotonic_values))

    sleeps: list[float] = []
    monkeypatch.setattr("tripscore.ingestion.tdx_client.time.sleep", lambda s: sleeps.append(float(s)))

    monkeypatch.setattr("tripscore.ingestion.tdx_client.get_json", lambda *_a, **_k: {"ok": True})

    client = TdxClient(settings=settings, cache=FileCache(tmp_path, enabled=False))
    client._tdx_get_json("https://example.test/a", params={"x": 1})
    client._tdx_get_json("https://example.test/b", params={"x": 2})

    assert sleeps == [0.8]

