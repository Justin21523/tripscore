import httpx

from tripscore.config.settings import get_settings
from tripscore.core.cache import FileCache
from tripscore.ingestion.tdx_client import TdxClient


def test_tdx_pagination_retries_on_429(monkeypatch, tmp_path):
    settings = get_settings()
    retry = settings.ingestion.tdx.retry.model_copy(
        update={"max_attempts": 2, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}
    )
    tdx = settings.ingestion.tdx.model_copy(
        update={
            "client_id": "test",
            "client_secret": "test",
            "request_spacing_seconds": 0.0,
            "retry": retry,
        }
    )
    ingestion = settings.ingestion.model_copy(update={"tdx": tdx})
    settings = settings.model_copy(update={"ingestion": ingestion})

    monkeypatch.setattr(
        "tripscore.ingestion.tdx_client.post_form",
        lambda *_args, **_kwargs: {"access_token": "token", "expires_in": 3600},
    )

    calls: list[tuple[int, int]] = []
    seen_429 = False

    def fake_get_json(url, *, params=None, headers=None, timeout_seconds=15):  # noqa: ARG001
        nonlocal seen_429
        skip = int((params or {}).get("$skip", 0))
        top = int((params or {}).get("$top", 0))
        calls.append((skip, top))

        if skip == 0 and not seen_429:
            seen_429 = True
            request = httpx.Request("GET", url)
            response = httpx.Response(429, request=request, headers={"Retry-After": "0"})
            raise httpx.HTTPStatusError("429", request=request, response=response)

        if skip == 0:
            return [{"x": 1}, {"x": 2}]
        if skip == 2:
            return [{"x": 3}]
        return []

    monkeypatch.setattr("tripscore.ingestion.tdx_client.get_json", fake_get_json)
    monkeypatch.setattr("tripscore.ingestion.tdx_client.time.sleep", lambda *_args, **_kwargs: None)

    client = TdxClient(settings=settings, cache=FileCache(tmp_path, enabled=False))
    items = client._fetch_paged_list("https://example.test/odata", top=2, select="x")

    assert items == [{"x": 1}, {"x": 2}, {"x": 3}]
    assert calls[:3] == [(0, 2), (0, 2), (2, 2)]
