from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import httpx

from tripscore.core.cache import FileCache


DatasetName = Literal[
    "bus_stops",
    "bus_routes",
    "bike_stations",
    "bike_availability",
    "metro_stations",
    "parking_lots",
    "parking_availability",
]


@dataclass(frozen=True)
class BulkFetchResult:
    dataset: DatasetName
    scope: str
    pages_fetched: int
    items_added: int
    total_items: int
    next_skip: int
    done: bool
    data_path: Path
    progress_path: Path


class _TdxClientLike(Protocol):
    def _tdx_get_json(self, url: str, *, params: dict[str, Any]) -> Any: ...

    @property
    def _settings(self) -> Any: ...


def _bulk_dir(cache: FileCache) -> Path:
    return cache.base_dir / "tdx_bulk"

def bulk_data_path(cache: FileCache, dataset: DatasetName, scope: str) -> Path:
    data_path, _ = _paths(cache, dataset, scope)
    return data_path


def bulk_progress_path(cache: FileCache, dataset: DatasetName, scope: str) -> Path:
    _, progress_path = _paths(cache, dataset, scope)
    return progress_path


def read_bulk_data(cache: FileCache, dataset: DatasetName, scope: str) -> list[dict[str, Any]]:
    path = bulk_data_path(cache, dataset, scope)
    payload = _load_json(path, default=[])
    return payload if isinstance(payload, list) else []


def read_bulk_progress(cache: FileCache, dataset: DatasetName, scope: str) -> dict[str, Any]:
    path = bulk_progress_path(cache, dataset, scope)
    payload = _load_json(path, default={})
    return payload if isinstance(payload, dict) else {}


def bulk_is_unsupported(cache: FileCache, dataset: DatasetName, scope: str) -> bool:
    """Return True if the bulk progress indicates the dataset is unsupported (typically HTTP 404/400)."""
    p = read_bulk_progress(cache, dataset, scope) or {}
    status = p.get("error_status")
    status_i = int(status) if isinstance(status, (int, float)) else None
    return bool(p.get("unsupported", False)) or status_i in {404, 400}


def _paths(cache: FileCache, dataset: DatasetName, scope: str) -> tuple[Path, Path]:
    base = _bulk_dir(cache) / dataset
    return base / f"{scope}.json", base / f"{scope}.progress.json"


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _merge_by_key(existing: list[dict[str, Any]], new_items: list[dict[str, Any]], *, key_field: str) -> int:
    by_key: dict[str, dict[str, Any]] = {}
    for item in existing:
        k = item.get(key_field)
        if k:
            by_key[str(k)] = item
    before = len(by_key)
    for item in new_items:
        k = item.get(key_field)
        if k:
            by_key[str(k)] = item
    existing[:] = list(by_key.values())
    return len(by_key) - before


def bulk_fetch_paged_odata(
    *,
    tdx_client: _TdxClientLike,
    cache: FileCache,
    dataset: DatasetName,
    scope: str,
    endpoint: str,
    select: str,
    top: int,
    key_field: str,
    max_pages: int = 1,
    max_seconds: float | None = None,
    reset: bool = False,
) -> BulkFetchResult:
    """Fetch OData pages gradually and persist partial results under the cache directory."""
    data_path, progress_path = _paths(cache, dataset, scope)
    if reset:
        if data_path.exists():
            data_path.unlink()
        if progress_path.exists():
            progress_path.unlink()

    progress = _load_json(progress_path, default={})
    next_skip = int(progress.get("next_skip", 0))
    done = bool(progress.get("done", False))

    existing = _load_json(data_path, default=[])
    if not isinstance(existing, list):
        existing = []

    start = time.monotonic()
    pages_fetched = 0
    items_added = 0

    if done:
        return BulkFetchResult(
            dataset=dataset,
            scope=scope,
            pages_fetched=0,
            items_added=0,
            total_items=len(existing),
            next_skip=next_skip,
            done=True,
            data_path=data_path,
            progress_path=progress_path,
        )

    for _ in range(int(max_pages)):
        if max_seconds is not None and (time.monotonic() - start) >= float(max_seconds):
            break

        params = {"$format": "JSON", "$top": int(top), "$skip": int(next_skip), "$select": select}
        try:
            page = tdx_client._tdx_get_json(endpoint, params=params)
        except httpx.HTTPStatusError as exc:
            status = int(exc.response.status_code)

            # Persist a status snapshot so offline tools can show rate-limit/unsupported visibility.
            prev_errors = int(progress.get("error_count", 0) or 0)
            progress.update(
                {
                    "dataset": dataset,
                    "scope": scope,
                    "next_skip": next_skip,
                    "top": int(top),
                    "done": bool(done),
                    "error_status": status,
                    "error": str(exc),
                    "error_count": prev_errors + 1,
                    "last_error_at_unix": int(time.time()),
                    "updated_at_unix": int(time.time()),
                }
            )

            unsupported_by_status = status == 404 or (status == 400 and dataset in {"bike_stations", "bike_availability"})
            if unsupported_by_status:
                done = True
                progress["done"] = True
                progress["unsupported"] = True
                progress["unsupported_reason"] = "http_404" if status == 404 else "http_400"
                _write_json(data_path, existing)
                _write_json(progress_path, progress)
                return BulkFetchResult(
                    dataset=dataset,
                    scope=scope,
                    pages_fetched=pages_fetched,
                    items_added=items_added,
                    total_items=len(existing),
                    next_skip=next_skip,
                    done=True,
                    data_path=data_path,
                    progress_path=progress_path,
                )

            _write_json(data_path, existing)
            _write_json(progress_path, progress)
            raise
        if not isinstance(page, list):
            raise RuntimeError("Unexpected TDX response shape; expected a list.")

        pages_fetched += 1
        items_added += _merge_by_key(existing, page, key_field=key_field)

        if len(page) < int(top):
            done = True
        else:
            next_skip += int(top)

        _write_json(data_path, existing)
        _write_json(
            progress_path,
            {
                "dataset": dataset,
                "scope": scope,
                "next_skip": next_skip,
                "top": int(top),
                "done": bool(done),
                "updated_at_unix": int(time.time()),
            },
        )

        if done:
            break

    return BulkFetchResult(
        dataset=dataset,
        scope=scope,
        pages_fetched=pages_fetched,
        items_added=items_added,
        total_items=len(existing),
        next_skip=next_skip,
        done=bool(done),
        data_path=data_path,
        progress_path=progress_path,
    )


def bulk_prefetch_all(
    *,
    tdx_client: _TdxClientLike,
    cache: FileCache,
    city: str,
    datasets: list[DatasetName],
    max_pages_per_dataset: int = 1,
    max_seconds_total: float | None = None,
    reset: bool = False,
) -> list[BulkFetchResult]:
    """Prefetch multiple datasets stage-by-stage; safe to run repeatedly."""
    settings = tdx_client._settings
    base_url = settings.ingestion.tdx.base_url.rstrip("/")
    start = time.monotonic()

    out: list[BulkFetchResult] = []

    def remaining_budget() -> float | None:
        if max_seconds_total is None:
            return None
        return max(0.0, float(max_seconds_total) - (time.monotonic() - start))

    for ds in datasets:
        budget = remaining_budget()
        if budget is not None and budget <= 0:
            break

        if ds == "bus_stops":
            out.append(
                bulk_fetch_paged_odata(
                    tdx_client=tdx_client,
                    cache=cache,
                    dataset=ds,
                    scope=f"city_{city}",
                    endpoint=f"{base_url}/Bus/Stop/City/{city}",
                    select=settings.ingestion.tdx.bus_stops.select,
                    top=settings.ingestion.tdx.bus_stops.top,
                    key_field="StopUID",
                    max_pages=max_pages_per_dataset,
                    max_seconds=budget,
                    reset=reset,
                )
            )
        elif ds == "bus_routes":
            out.append(
                bulk_fetch_paged_odata(
                    tdx_client=tdx_client,
                    cache=cache,
                    dataset=ds,
                    scope=f"city_{city}",
                    endpoint=f"{base_url}/Bus/Route/City/{city}",
                    select=settings.ingestion.tdx.bus_routes.select,
                    top=settings.ingestion.tdx.bus_routes.top,
                    key_field="RouteUID",
                    max_pages=max_pages_per_dataset,
                    max_seconds=budget,
                    reset=reset,
                )
            )
        elif ds == "bike_stations":
            out.append(
                bulk_fetch_paged_odata(
                    tdx_client=tdx_client,
                    cache=cache,
                    dataset=ds,
                    scope=f"city_{city}",
                    endpoint=f"{base_url}/Bike/Station/City/{city}",
                    select=settings.ingestion.tdx.bike_stations.select,
                    top=settings.ingestion.tdx.bike_stations.top,
                    key_field="StationUID",
                    max_pages=max_pages_per_dataset,
                    max_seconds=budget,
                    reset=reset,
                )
            )
        elif ds == "bike_availability":
            out.append(
                bulk_fetch_paged_odata(
                    tdx_client=tdx_client,
                    cache=cache,
                    dataset=ds,
                    scope=f"city_{city}",
                    endpoint=f"{base_url}/Bike/Availability/City/{city}",
                    select=settings.ingestion.tdx.bike_availability.select,
                    top=settings.ingestion.tdx.bike_availability.top,
                    key_field="StationUID",
                    max_pages=max_pages_per_dataset,
                    max_seconds=budget,
                    reset=reset,
                )
            )
        elif ds == "parking_lots":
            out.append(
                bulk_fetch_paged_odata(
                    tdx_client=tdx_client,
                    cache=cache,
                    dataset=ds,
                    scope=f"city_{city}",
                    endpoint=f"{base_url}/Parking/OffStreet/ParkingLot/City/{city}",
                    select=settings.ingestion.tdx.parking_lots.select,
                    top=settings.ingestion.tdx.parking_lots.top,
                    key_field="ParkingLotUID",
                    max_pages=max_pages_per_dataset,
                    max_seconds=budget,
                    reset=reset,
                )
            )
        elif ds == "parking_availability":
            out.append(
                bulk_fetch_paged_odata(
                    tdx_client=tdx_client,
                    cache=cache,
                    dataset=ds,
                    scope=f"city_{city}",
                    endpoint=f"{base_url}/Parking/OffStreet/ParkingAvailability/City/{city}",
                    select=settings.ingestion.tdx.parking_availability.select,
                    top=settings.ingestion.tdx.parking_availability.top,
                    key_field="ParkingLotUID",
                    max_pages=max_pages_per_dataset,
                    max_seconds=budget,
                    reset=reset,
                )
            )
        elif ds == "metro_stations":
            for operator in settings.ingestion.tdx.metro_stations.operators:
                budget = remaining_budget()
                if budget is not None and budget <= 0:
                    break
                out.append(
                    bulk_fetch_paged_odata(
                        tdx_client=tdx_client,
                        cache=cache,
                        dataset=ds,
                        scope=f"operator_{operator}",
                        endpoint=f"{base_url}/Rail/Metro/Station/{operator}",
                        select=settings.ingestion.tdx.metro_stations.select,
                        top=settings.ingestion.tdx.metro_stations.top,
                        key_field="StationUID",
                        max_pages=max_pages_per_dataset,
                        max_seconds=budget,
                        reset=reset,
                    )
                )
        else:
            raise ValueError(f"Unknown dataset: {ds}")

    return out
