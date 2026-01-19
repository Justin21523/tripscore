import httpx

from tripscore.core.cache import FileCache
from tripscore.ingestion.tdx_bulk import bulk_fetch_paged_odata


class StubTdx400:
    def _tdx_get_json(self, url: str, *, params: dict):  # noqa: ARG002
        request = httpx.Request("GET", url)
        response = httpx.Response(400, request=request)
        raise httpx.HTTPStatusError("400", request=request, response=response)


def test_bulk_fetch_bike_400_marks_unsupported(tmp_path):
    cache = FileCache(tmp_path, enabled=True)
    r = bulk_fetch_paged_odata(
        tdx_client=StubTdx400(),
        cache=cache,
        dataset="bike_stations",
        scope="city_HualienCounty",
        endpoint="https://example.test/badrequest",
        select="x",
        top=1000,
        key_field="StationUID",
        max_pages=1,
        max_seconds=None,
        reset=True,
    )
    assert r.done is True
    progress = (tmp_path / "tdx_bulk" / "bike_stations" / "city_HualienCounty.progress.json").read_text(
        encoding="utf-8"
    )
    assert '"error_status": 400' in progress
    assert '"unsupported": true' in progress

