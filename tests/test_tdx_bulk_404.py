import httpx
import pytest

from tripscore.core.cache import FileCache
from tripscore.ingestion.tdx_bulk import bulk_fetch_paged_odata


class StubTdx:
    def _tdx_get_json(self, url: str, *, params: dict):  # noqa: ARG002
        request = httpx.Request("GET", url)
        response = httpx.Response(404, request=request)
        raise httpx.HTTPStatusError("404", request=request, response=response)


def test_bulk_fetch_marks_404_done(tmp_path):
    cache = FileCache(tmp_path, enabled=True)
    r = bulk_fetch_paged_odata(
        tdx_client=StubTdx(),
        cache=cache,
        dataset="parking_lots",
        scope="city_Taipei",
        endpoint="https://example.test/notfound",
        select="x",
        top=1000,
        key_field="ParkingLotUID",
        max_pages=1,
        max_seconds=None,
        reset=True,
    )
    assert r.done is True
    progress = (tmp_path / "tdx_bulk" / "parking_lots" / "city_Taipei.progress.json").read_text(
        encoding="utf-8"
    )
    assert '"error_status": 404' in progress


def test_bulk_fetch_non_404_propagates(tmp_path):
    class Stub500:
        def _tdx_get_json(self, url: str, *, params: dict):  # noqa: ARG002
            request = httpx.Request("GET", url)
            response = httpx.Response(500, request=request)
            raise httpx.HTTPStatusError("500", request=request, response=response)

    cache = FileCache(tmp_path, enabled=True)
    with pytest.raises(httpx.HTTPStatusError):
        bulk_fetch_paged_odata(
            tdx_client=Stub500(),
            cache=cache,
            dataset="parking_lots",
            scope="city_Taipei",
            endpoint="https://example.test/servererror",
            select="x",
            top=1000,
            key_field="ParkingLotUID",
            max_pages=1,
            max_seconds=None,
            reset=True,
        )

