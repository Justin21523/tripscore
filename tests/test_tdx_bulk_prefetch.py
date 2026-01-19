import httpx

from tripscore.core.cache import FileCache
from tripscore.config.settings import get_settings
from tripscore.ingestion.tdx_bulk import bulk_fetch_paged_odata
from tripscore.ingestion.tdx_client import TdxClient


def test_tdx_bulk_prefetch_resumes_progress(monkeypatch, tmp_path):
    settings = get_settings()
    retry = settings.ingestion.tdx.retry.model_copy(
        update={"max_attempts": 0, "base_delay_seconds": 0.0, "max_delay_seconds": 0.0}
    )
    tdx = settings.ingestion.tdx.model_copy(
        update={
            "client_id": "test",
            "client_secret": "test",
            "request_spacing_seconds": 0.0,
            "retry": retry,
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
            return [{"StopUID": "a"}, {"StopUID": "b"}]
        if skip == 2:
            return [{"StopUID": "c"}]
        request = httpx.Request("GET", url)
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("unexpected skip", request=request, response=response)

    monkeypatch.setattr("tripscore.ingestion.tdx_client.get_json", fake_get_json)

    cache = FileCache(tmp_path, enabled=True)
    client = TdxClient(settings=settings, cache=cache)

    r1 = bulk_fetch_paged_odata(
        tdx_client=client,
        cache=cache,
        dataset="bus_stops",
        scope="city_Taipei",
        endpoint="https://example.test/Bus/Stop/City/Taipei",
        select="StopUID",
        top=2,
        key_field="StopUID",
        max_pages=1,
    )
    assert r1.pages_fetched == 1
    assert r1.total_items == 2
    assert r1.done is False
    assert r1.next_skip == 2

    r2 = bulk_fetch_paged_odata(
        tdx_client=client,
        cache=cache,
        dataset="bus_stops",
        scope="city_Taipei",
        endpoint="https://example.test/Bus/Stop/City/Taipei",
        select="StopUID",
        top=2,
        key_field="StopUID",
        max_pages=10,
    )
    assert r2.total_items == 3
    assert r2.done is True

