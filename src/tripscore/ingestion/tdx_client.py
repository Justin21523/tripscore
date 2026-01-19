"""
TDX ingestion client (Transport Data eXchange, Taiwan).

This module is responsible only for:
- authenticating via OAuth client-credentials,
- fetching TDX datasets (bus stops, YouBike stations + availability, metro stations, parking lots + availability),
- parsing them into small typed dataclasses used by feature scoring.

It intentionally does not implement scoring logic; see `tripscore.features.*` for that.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

import httpx

from tripscore.config.settings import Settings
from tripscore.core.cache import FileCache
from tripscore.core.http import get_json, post_form
from tripscore.core.ingestion_meta import record_ingestion_source
from tripscore.ingestion.tdx_bulk import (
    DatasetName,
    bulk_fetch_paged_odata,
    read_bulk_data,
    read_bulk_progress,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BusStop:
    """Minimal bus stop record used for proximity scoring."""

    stop_uid: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class BusRoute:
    """Minimal bus route record (for coverage/headway enrichment)."""

    route_uid: str
    name: str


@dataclass(frozen=True)
class BusEta:
    """One real-time bus arrival estimate for a stop/route."""

    stop_uid: str
    stop_name: str | None
    route_uid: str
    route_name: str | None
    estimate_seconds: int | None
    direction: int | None
    updated_at: str | None


@dataclass(frozen=True)
class BikeStationStatus:
    """YouBike station location + current availability (if provided)."""

    station_uid: str
    name: str
    lat: float
    lon: float
    available_rent_bikes: int | None
    available_return_bikes: int | None


@dataclass(frozen=True)
class MetroStation:
    """Metro station record for a specific operator (e.g., TRTC)."""

    station_uid: str
    name: str
    lat: float
    lon: float
    operator: str


@dataclass(frozen=True)
class ParkingLotStatus:
    """Parking lot location + current availability (if provided)."""

    parking_lot_uid: str
    name: str
    lat: float
    lon: float
    available_spaces: int | None
    total_spaces: int | None
    address: str | None = None
    service_time: str | None = None
    fare_description: str | None = None


class TdxClient:
    """TDX API client with caching and token management."""

    def __init__(self, settings: Settings, cache: FileCache):
        self._settings = settings
        self._cache = cache
        self._access_token: str | None = None
        self._token_expires_at_unix: int = 0
        self._last_request_monotonic: float | None = None

    @staticmethod
    def _parse_retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            seconds = float(value)
        except Exception:
            return None
        return seconds if seconds >= 0 else None

    @staticmethod
    def _stale_ok(exc: Exception) -> bool:
        return isinstance(exc, httpx.HTTPError)

    def _throttle_requests(self) -> None:
        spacing_seconds = float(self._settings.ingestion.tdx.request_spacing_seconds)
        if spacing_seconds <= 0:
            return

        now = time.monotonic()
        if self._last_request_monotonic is None:
            self._last_request_monotonic = now
            return

        elapsed = now - self._last_request_monotonic
        remaining = spacing_seconds - elapsed
        if remaining > 0:
            time.sleep(remaining)
            now = time.monotonic()

        self._last_request_monotonic = now

    def _require_credentials(self) -> tuple[str, str]:
        """Return (client_id, client_secret) or raise if missing."""
        client_id = self._settings.ingestion.tdx.client_id
        client_secret = self._settings.ingestion.tdx.client_secret
        if not client_id or not client_secret:
            raise RuntimeError(
                "TDX credentials are not configured. Set TDX_CLIENT_ID and TDX_CLIENT_SECRET."
            )
        return client_id, client_secret

    def _get_access_token(self) -> str:
        """Get a valid bearer token, refreshing it when needed."""
        now = int(time.time())
        if self._access_token and now < self._token_expires_at_unix - 30:
            return self._access_token

        client_id, client_secret = self._require_credentials()

        payload = post_form(
            self._settings.ingestion.tdx.token_url,
            data={
                "grant_type": "client_credentials",
                "client_id": client_id,
                "client_secret": client_secret,
            },
            timeout_seconds=self._settings.app.http_timeout_seconds,
        )
        access_token = payload.get("access_token")
        expires_in = int(payload.get("expires_in", 0))
        if not access_token or expires_in <= 0:
            raise RuntimeError("TDX token response is missing access_token/expires_in.")

        self._access_token = str(access_token)
        self._token_expires_at_unix = now + expires_in
        return self._access_token

    def _tdx_get_json(self, url: str, *, params: dict[str, Any]) -> Any:
        """GET JSON with TDX auth + simple retry/backoff for 429/transient errors."""
        retry = self._settings.ingestion.tdx.retry
        max_attempts = int(retry.max_attempts)
        base_delay_seconds = float(retry.base_delay_seconds)
        max_delay_seconds = float(retry.max_delay_seconds)

        refreshed_token = False
        last_exc: Exception | None = None

        for attempt in range(max_attempts + 1):
            token = self._get_access_token()
            headers = {"Authorization": f"Bearer {token}"}

            try:
                self._throttle_requests()
                return get_json(
                    url,
                    params=params,
                    headers=headers,
                    timeout_seconds=self._settings.app.http_timeout_seconds,
                )
            except httpx.HTTPStatusError as exc:
                last_exc = exc
                status = exc.response.status_code

                if status == 401 and not refreshed_token:
                    logger.info("TDX request unauthorized; refreshing token and retrying.")
                    self._access_token = None
                    self._token_expires_at_unix = 0
                    refreshed_token = True
                    continue

                retry_after = self._parse_retry_after_seconds(
                    exc.response.headers.get("Retry-After")
                )
                is_retryable_status = status in {429, 500, 502, 503, 504}
                if not is_retryable_status or attempt >= max_attempts:
                    raise

                delay = min(max_delay_seconds, base_delay_seconds * (2**attempt))
                if retry_after is not None:
                    delay = max(delay, retry_after)

                logger.warning(
                    "TDX request failed with status=%s; retrying in %.2fs (attempt %s/%s)",
                    status,
                    delay,
                    attempt + 1,
                    max_attempts,
                )
                time.sleep(delay)
                continue
            except httpx.TransportError as exc:
                last_exc = exc
                if attempt >= max_attempts:
                    raise
                delay = min(max_delay_seconds, base_delay_seconds * (2**attempt))
                logger.warning(
                    "TDX transport error; retrying in %.2fs (attempt %s/%s)",
                    delay,
                    attempt + 1,
                    max_attempts,
                )
                time.sleep(delay)
                continue

        if last_exc:
            raise last_exc
        raise RuntimeError("TDX request failed without an exception (unexpected).")

    def _get_raw_list(
        self,
        *,
        dataset: DatasetName,
        scope: str,
        cache_key: str,
        endpoint: str,
        select: str,
        top: int,
        key_field: str,
        ttl_seconds: int,
        allow_bulk: bool = True,
    ) -> list[dict[str, Any]]:
        source_name = f"tdx:{dataset}:{scope}"

        cached = self._cache.get("tdx", cache_key, ttl_seconds=ttl_seconds)
        if isinstance(cached, list):
            meta = self._cache.get_entry_meta("tdx", cache_key) or {}
            record_ingestion_source(
                source_name,
                {
                    "mode": "cache",
                    "dataset": dataset,
                    "scope": scope,
                    "as_of_unix": meta.get("created_at_unix"),
                    "ttl_seconds": meta.get("ttl_seconds"),
                },
            )
            return cached

        bulk_settings = self._settings.ingestion.tdx.bulk
        can_bulk = allow_bulk and bool(self._cache.enabled) and bool(bulk_settings.enabled)
        if can_bulk:
            bulk_data = read_bulk_data(self._cache, dataset, scope)
            bulk_progress = read_bulk_progress(self._cache, dataset, scope)
            done = bool(bulk_progress.get("done", False))

            if not done:
                try:
                    bulk_fetch_paged_odata(
                        tdx_client=self,
                        cache=self._cache,
                        dataset=dataset,
                        scope=scope,
                        endpoint=endpoint,
                        select=select,
                        top=top,
                        key_field=key_field,
                        max_pages=int(bulk_settings.max_pages_per_call),
                        max_seconds=(
                            float(bulk_settings.max_seconds_per_call)
                            if bulk_settings.max_seconds_per_call is not None
                            else None
                        ),
                        reset=False,
                    )
                except httpx.HTTPError as exc:
                    logger.warning("TDX bulk stage failed (%s/%s): %s", dataset, scope, str(exc))

                bulk_data = read_bulk_data(self._cache, dataset, scope)
                bulk_progress = read_bulk_progress(self._cache, dataset, scope)
                done = bool(bulk_progress.get("done", False))

            if done and bulk_data:
                self._cache.set("tdx", cache_key, bulk_data, ttl_seconds=ttl_seconds)

            if bulk_data:
                record_ingestion_source(
                    source_name,
                    {
                        "mode": "bulk" if done else "bulk_partial",
                        "dataset": dataset,
                        "scope": scope,
                        "as_of_unix": bulk_progress.get("updated_at_unix"),
                        "done": bool(done),
                        "next_skip": bulk_progress.get("next_skip"),
                    },
                )
            return bulk_data

        try:
            raw = self._fetch_paged_list(endpoint, top=top, select=select)
        except httpx.HTTPError:
            stale = self._cache.get_stale("tdx", cache_key)
            if isinstance(stale, list):
                meta = self._cache.get_entry_meta("tdx", cache_key) or {}
                record_ingestion_source(
                    source_name,
                    {
                        "mode": "stale",
                        "dataset": dataset,
                        "scope": scope,
                        "as_of_unix": meta.get("created_at_unix"),
                        "ttl_seconds": meta.get("ttl_seconds"),
                    },
                )
                return stale
            record_ingestion_source(source_name, {"mode": "none", "dataset": dataset, "scope": scope})
            raise

        if isinstance(raw, list):
            self._cache.set("tdx", cache_key, raw, ttl_seconds=ttl_seconds)
            meta = self._cache.get_entry_meta("tdx", cache_key) or {}
            record_ingestion_source(
                source_name,
                {
                    "mode": "live",
                    "dataset": dataset,
                    "scope": scope,
                    "as_of_unix": meta.get("created_at_unix"),
                    "ttl_seconds": meta.get("ttl_seconds"),
                },
            )
            return raw
        record_ingestion_source(source_name, {"mode": "none", "dataset": dataset, "scope": scope})
        return []

    def _fetch_bus_stops_raw(self, city: str) -> list[dict[str, Any]]:
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Bus/Stop/City/{city}"
        top = self._settings.ingestion.tdx.bus_stops.top
        select = self._settings.ingestion.tdx.bus_stops.select
        return self._fetch_paged_list(endpoint, top=top, select=select)

    def _fetch_bike_stations_raw(self, city: str) -> list[dict[str, Any]]:
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Bike/Station/City/{city}"
        top = self._settings.ingestion.tdx.bike_stations.top
        select = self._settings.ingestion.tdx.bike_stations.select
        return self._fetch_paged_list(endpoint, top=top, select=select)

    def _fetch_bike_availability_raw(self, city: str) -> list[dict[str, Any]]:
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Bike/Availability/City/{city}"
        top = self._settings.ingestion.tdx.bike_availability.top
        select = self._settings.ingestion.tdx.bike_availability.select
        return self._fetch_paged_list(endpoint, top=top, select=select)

    def _fetch_metro_stations_raw(self, operator: str) -> list[dict[str, Any]]:
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Rail/Metro/Station/{operator}"
        top = self._settings.ingestion.tdx.metro_stations.top
        select = self._settings.ingestion.tdx.metro_stations.select
        return self._fetch_paged_list(endpoint, top=top, select=select)

    def _fetch_parking_lots_raw(self, city: str) -> list[dict[str, Any]]:
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Parking/OffStreet/ParkingLot/City/{city}"
        top = self._settings.ingestion.tdx.parking_lots.top
        select = self._settings.ingestion.tdx.parking_lots.select
        return self._fetch_paged_list(endpoint, top=top, select=select)

    def _fetch_parking_availability_raw(self, city: str) -> list[dict[str, Any]]:
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Parking/OffStreet/ParkingAvailability/City/{city}"
        top = self._settings.ingestion.tdx.parking_availability.top
        select = self._settings.ingestion.tdx.parking_availability.select
        return self._fetch_paged_list(endpoint, top=top, select=select)

    def _fetch_paged_list(self, endpoint: str, *, top: int, select: str) -> list[dict[str, Any]]:
        """Fetch a complete OData list endpoint using `$top`/`$skip` pagination."""
        results: list[dict[str, Any]] = []
        skip = 0

        while True:
            params = {"$format": "JSON", "$top": top, "$skip": skip, "$select": select}
            page = self._tdx_get_json(endpoint, params=params)
            if not isinstance(page, list):
                raise RuntimeError("Unexpected TDX response shape; expected a list.")

            results.extend(page)
            if len(page) < top:
                break
            skip += top

        return results

    def _fetch_first_page(self, endpoint: str, *, top: int, select: str) -> list[dict[str, Any]]:
        """Fetch only the first page of an OData list endpoint.

        This is intended for smoke checks and debugging to avoid rate limits caused by
        paginating large datasets.
        """
        params = {"$format": "JSON", "$top": int(top), "$skip": 0, "$select": select}
        page = self._tdx_get_json(endpoint, params=params)
        if not isinstance(page, list):
            raise RuntimeError("Unexpected TDX response shape; expected a list.")
        return page

    def get_bus_stops(self, *, city: str | None = None) -> list[BusStop]:
        """Return parsed bus stops for a city (cached)."""
        city = city or self._settings.ingestion.tdx.city
        cache_key = f"tdx_bus_stops:{city}"
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Bus/Stop/City/{city}"
        raw = self._get_raw_list(
            dataset="bus_stops",
            scope=f"city_{city}",
            cache_key=cache_key,
            endpoint=endpoint,
            select=self._settings.ingestion.tdx.bus_stops.select,
            top=self._settings.ingestion.tdx.bus_stops.top,
            key_field="StopUID",
            ttl_seconds=self._settings.ingestion.tdx.cache_ttl_seconds,
        )

        stops: list[BusStop] = []
        for item in raw:
            try:
                pos = item.get("StopPosition") or {}
                stop_uid = str(item.get("StopUID"))
                stop_name = item.get("StopName", {}).get("Zh_tw") or item.get("StopName", {}).get("En")
                lat = float(pos.get("PositionLat"))
                lon = float(pos.get("PositionLon"))
                if stop_uid and stop_name:
                    stops.append(BusStop(stop_uid=stop_uid, name=str(stop_name), lat=lat, lon=lon))
            except Exception:
                continue

        if not stops:
            logger.warning("TDX returned 0 bus stops after parsing; continuing with empty list.")
        return stops

    def get_bus_routes(self, *, city: str | None = None) -> list[BusRoute]:
        """Return parsed bus routes for a city (cached)."""
        city = city or self._settings.ingestion.tdx.city
        cache_key = f"tdx_bus_routes:{city}"
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Bus/Route/City/{city}"
        raw = self._get_raw_list(
            dataset="bus_routes",
            scope=f"city_{city}",
            cache_key=cache_key,
            endpoint=endpoint,
            select=self._settings.ingestion.tdx.bus_routes.select,
            top=self._settings.ingestion.tdx.bus_routes.top,
            key_field="RouteUID",
            ttl_seconds=self._settings.ingestion.tdx.cache_ttl_seconds,
        )

        routes: list[BusRoute] = []
        for item in raw:
            try:
                uid = str(item.get("RouteUID") or "")
                name_obj = item.get("RouteName") or {}
                name = (
                    str(name_obj.get("Zh_tw"))
                    if isinstance(name_obj, dict) and name_obj.get("Zh_tw")
                    else str(item.get("RouteName") or "")
                )
                if uid and name:
                    routes.append(BusRoute(route_uid=uid, name=name))
            except Exception:
                continue
        return routes

    def get_bus_eta(self, *, city: str | None = None, stop_uids: list[str] | None = None) -> list[BusEta]:
        """Return real-time bus ETA rows for the requested stop UIDs (cached, short TTL).

        Notes:
        - This is designed for *targeted* queries (e.g., a few nearby stops), not a full-city crawl.
        - We cache with a short TTL to reduce TDX load and tolerate bursts.
        """
        city = city or self._settings.ingestion.tdx.city
        stop_uids = [str(s).strip() for s in (stop_uids or []) if str(s).strip()]
        if not stop_uids:
            record_ingestion_source(f"tdx:bus_eta:city_{city}", {"mode": "none", "city": city})
            return []

        # Keep filter size bounded (avoid oversized URLs); caller should pre-filter.
        stop_uids = stop_uids[:12]
        stop_uids_sorted = sorted(set(stop_uids))
        cache_key = f"tdx_bus_eta:{city}:{'|'.join(stop_uids_sorted)}"
        ttl = int(self._settings.ingestion.tdx.bus_estimated_time_cache_ttl_seconds)
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        endpoint = f"{base_url}/Bus/EstimatedTimeOfArrival/City/{city}"
        select = self._settings.ingestion.tdx.bus_estimated_time.select
        top = int(self._settings.ingestion.tdx.bus_estimated_time.top)

        def _escape(v: str) -> str:
            return v.replace("'", "''")

        filt = " or ".join([f"StopUID eq '{_escape(uid)}'" for uid in stop_uids_sorted])

        source_name = f"tdx:bus_eta:city_{city}"
        cached = self._cache.get("tdx", cache_key, ttl_seconds=ttl)
        if isinstance(cached, list):
            meta = self._cache.get_entry_meta("tdx", cache_key) or {}
            record_ingestion_source(
                source_name,
                {
                    "mode": "cache",
                    "dataset": "bus_eta",
                    "city": city,
                    "as_of_unix": meta.get("created_at_unix"),
                    "ttl_seconds": meta.get("ttl_seconds"),
                    "stop_count": len(stop_uids_sorted),
                },
            )
            raw = cached
        else:

            def builder() -> list[dict[str, Any]]:
                params = {"$format": "JSON", "$top": top, "$select": select, "$filter": filt}
                return self._tdx_get_json(endpoint, params=params)

            try:
                raw = self._cache.get_or_set(
                    "tdx",
                    cache_key,
                    builder,
                    ttl_seconds=ttl,
                    stale_if_error=True,
                    stale_predicate=self._stale_ok,
                )
                meta = self._cache.get_entry_meta("tdx", cache_key) or {}
                record_ingestion_source(
                    source_name,
                    {
                        "mode": "live",
                        "dataset": "bus_eta",
                        "city": city,
                        "as_of_unix": meta.get("created_at_unix"),
                        "ttl_seconds": meta.get("ttl_seconds"),
                        "stop_count": len(stop_uids_sorted),
                    },
                )
            except Exception:
                stale = self._cache.get_stale("tdx", cache_key)
                if isinstance(stale, list):
                    meta = self._cache.get_entry_meta("tdx", cache_key) or {}
                    record_ingestion_source(
                        source_name,
                        {
                            "mode": "stale",
                            "dataset": "bus_eta",
                            "city": city,
                            "as_of_unix": meta.get("created_at_unix"),
                            "ttl_seconds": meta.get("ttl_seconds"),
                            "stop_count": len(stop_uids_sorted),
                        },
                    )
                    raw = stale
                else:
                    record_ingestion_source(
                        source_name,
                        {"mode": "none", "dataset": "bus_eta", "city": city, "stop_count": len(stop_uids_sorted)},
                    )
                    raise

        if not isinstance(raw, list):
            return []

        out: list[BusEta] = []
        for item in raw:
            try:
                stop_uid = str(item.get("StopUID") or "")
                route_uid = str(item.get("RouteUID") or "")
                if not stop_uid or not route_uid:
                    continue
                stop_name_obj = item.get("StopName") or {}
                route_name_obj = item.get("RouteName") or {}
                stop_name = (
                    str(stop_name_obj.get("Zh_tw"))
                    if isinstance(stop_name_obj, dict) and stop_name_obj.get("Zh_tw")
                    else None
                )
                route_name = (
                    str(route_name_obj.get("Zh_tw"))
                    if isinstance(route_name_obj, dict) and route_name_obj.get("Zh_tw")
                    else None
                )
                est = item.get("EstimateTime")
                estimate_seconds: int | None = None
                if isinstance(est, (int, float)):
                    try:
                        estimate_seconds = int(est)
                    except Exception:
                        estimate_seconds = None
                direction = item.get("Direction")
                direction_i = int(direction) if isinstance(direction, (int, float)) else None
                updated_at = str(item.get("UpdateTime") or "") or None
                out.append(
                    BusEta(
                        stop_uid=stop_uid,
                        stop_name=stop_name,
                        route_uid=route_uid,
                        route_name=route_name,
                        estimate_seconds=estimate_seconds,
                        direction=direction_i,
                        updated_at=updated_at,
                    )
                )
            except Exception:
                continue

        return out

    def get_bus_stops_sample(self, *, city: str | None = None, top: int = 10) -> list[BusStop]:
        """Return a small sample of parsed bus stops (cached, first page only)."""
        city = city or self._settings.ingestion.tdx.city
        cache_key = f"tdx_bus_stops_sample:{city}:{int(top)}"

        def builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX bus stop sample for city=%s top=%s", city, top)
            base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
            endpoint = f"{base_url}/Bus/Stop/City/{city}"
            select = self._settings.ingestion.tdx.bus_stops.select
            return self._fetch_first_page(endpoint, top=int(top), select=select)

        raw = self._cache.get_or_set(
            "tdx",
            cache_key,
            builder,
            ttl_seconds=min(self._settings.ingestion.tdx.cache_ttl_seconds, 60 * 10),
            stale_if_error=True,
            stale_predicate=self._stale_ok,
        )

        stops: list[BusStop] = []
        for item in raw:
            try:
                pos = item.get("StopPosition") or {}
                stop_uid = str(item.get("StopUID"))
                stop_name = item.get("StopName", {}).get("Zh_tw") or item.get("StopName", {}).get("En")
                lat = float(pos.get("PositionLat"))
                lon = float(pos.get("PositionLon"))
                if stop_uid and stop_name:
                    stops.append(BusStop(stop_uid=stop_uid, name=str(stop_name), lat=lat, lon=lon))
            except Exception:
                continue

        if not stops:
            raise RuntimeError("TDX returned 0 bus stops in sample after parsing; check dataset/fields.")
        return stops

    def get_youbike_station_statuses(self, *, city: str | None = None) -> list[BikeStationStatus]:
        """Return YouBike stations merged with live availability (cached)."""
        city = city or self._settings.ingestion.tdx.city

        stations_cache_key = f"tdx_bike_stations:{city}"
        availability_cache_key = f"tdx_bike_availability:{city}"
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        raw_stations = self._get_raw_list(
            dataset="bike_stations",
            scope=f"city_{city}",
            cache_key=stations_cache_key,
            endpoint=f"{base_url}/Bike/Station/City/{city}",
            select=self._settings.ingestion.tdx.bike_stations.select,
            top=self._settings.ingestion.tdx.bike_stations.top,
            key_field="StationUID",
            ttl_seconds=self._settings.ingestion.tdx.cache_ttl_seconds,
        )
        raw_availability = self._get_raw_list(
            dataset="bike_availability",
            scope=f"city_{city}",
            cache_key=availability_cache_key,
            endpoint=f"{base_url}/Bike/Availability/City/{city}",
            select=self._settings.ingestion.tdx.bike_availability.select,
            top=self._settings.ingestion.tdx.bike_availability.top,
            key_field="StationUID",
            ttl_seconds=self._settings.ingestion.tdx.bike_availability_cache_ttl_seconds,
            allow_bulk=False,
        )

        availability_by_uid: dict[str, tuple[int | None, int | None]] = {}
        for item in raw_availability:
            try:
                station_uid = str(item.get("StationUID"))
                if not station_uid:
                    continue
                rent = item.get("AvailableRentBikes")
                ret = item.get("AvailableReturnBikes")
                rent_i = int(rent) if rent is not None else None
                ret_i = int(ret) if ret is not None else None
                availability_by_uid[station_uid] = (rent_i, ret_i)
            except Exception:
                continue

        stations: list[BikeStationStatus] = []
        for item in raw_stations:
            try:
                pos = item.get("StationPosition") or {}
                station_uid = str(item.get("StationUID"))
                name = item.get("StationName", {}).get("Zh_tw") or item.get("StationName", {}).get("En")
                lat = float(pos.get("PositionLat"))
                lon = float(pos.get("PositionLon"))
                if not station_uid or not name:
                    continue
                rent_i, ret_i = availability_by_uid.get(station_uid, (None, None))
                stations.append(
                    BikeStationStatus(
                        station_uid=station_uid,
                        name=str(name),
                        lat=lat,
                        lon=lon,
                        available_rent_bikes=rent_i,
                        available_return_bikes=ret_i,
                    )
                )
            except Exception:
                continue

        if not stations:
            logger.warning("TDX returned 0 bike stations after parsing; continuing with empty list.")
        return stations

    def get_youbike_station_statuses_sample(
        self, *, city: str | None = None, top: int = 10
    ) -> list[BikeStationStatus]:
        """Return a small sample of YouBike stations merged with availability (first page only)."""
        city = city or self._settings.ingestion.tdx.city
        stations_cache_key = f"tdx_bike_stations_sample:{city}:{int(top)}"
        availability_cache_key = f"tdx_bike_availability_sample:{city}:{int(top)}"

        def stations_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX bike station sample for city=%s top=%s", city, top)
            base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
            endpoint = f"{base_url}/Bike/Station/City/{city}"
            select = self._settings.ingestion.tdx.bike_stations.select
            return self._fetch_first_page(endpoint, top=int(top), select=select)

        def availability_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX bike availability sample for city=%s top=%s", city, top)
            base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
            endpoint = f"{base_url}/Bike/Availability/City/{city}"
            select = self._settings.ingestion.tdx.bike_availability.select
            return self._fetch_first_page(endpoint, top=int(top), select=select)

        raw_stations = self._cache.get_or_set(
            "tdx",
            stations_cache_key,
            stations_builder,
            ttl_seconds=min(self._settings.ingestion.tdx.cache_ttl_seconds, 60 * 10),
            stale_if_error=True,
            stale_predicate=self._stale_ok,
        )
        raw_availability = self._cache.get_or_set(
            "tdx",
            availability_cache_key,
            availability_builder,
            ttl_seconds=min(self._settings.ingestion.tdx.bike_availability_cache_ttl_seconds, 60 * 10),
            stale_if_error=True,
            stale_predicate=self._stale_ok,
        )

        availability_by_uid: dict[str, tuple[int | None, int | None]] = {}
        for item in raw_availability:
            try:
                station_uid = str(item.get("StationUID"))
                if not station_uid:
                    continue
                rent = item.get("AvailableRentBikes")
                ret = item.get("AvailableReturnBikes")
                availability_by_uid[station_uid] = (
                    int(rent) if rent is not None else None,
                    int(ret) if ret is not None else None,
                )
            except Exception:
                continue

        stations: list[BikeStationStatus] = []
        for item in raw_stations:
            try:
                pos = item.get("StationPosition") or {}
                station_uid = str(item.get("StationUID"))
                name = item.get("StationName", {}).get("Zh_tw") or item.get("StationName", {}).get("En")
                lat = float(pos.get("PositionLat"))
                lon = float(pos.get("PositionLon"))
                if not station_uid or not name:
                    continue
                rent_i, ret_i = availability_by_uid.get(station_uid, (None, None))
                stations.append(
                    BikeStationStatus(
                        station_uid=station_uid,
                        name=str(name),
                        lat=lat,
                        lon=lon,
                        available_rent_bikes=rent_i,
                        available_return_bikes=ret_i,
                    )
                )
            except Exception:
                continue

        if not stations:
            raise RuntimeError("TDX returned 0 bike stations in sample after parsing; check dataset/fields.")
        return stations

    def get_metro_stations(self, *, operators: list[str] | None = None) -> list[MetroStation]:
        operators = operators or self._settings.ingestion.tdx.metro_stations.operators
        if not operators:
            raise RuntimeError("TDX metro operators are not configured.")

        stations: list[MetroStation] = []
        for operator in operators:
            cache_key = f"tdx_metro_stations:{operator}"
            base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
            raw = self._get_raw_list(
                dataset="metro_stations",
                scope=f"operator_{operator}",
                cache_key=cache_key,
                endpoint=f"{base_url}/Rail/Metro/Station/{operator}",
                select=self._settings.ingestion.tdx.metro_stations.select,
                top=self._settings.ingestion.tdx.metro_stations.top,
                key_field="StationUID",
                ttl_seconds=self._settings.ingestion.tdx.cache_ttl_seconds,
            )

            for item in raw:
                try:
                    pos = item.get("StationPosition") or {}
                    station_uid = str(item.get("StationUID"))
                    name = item.get("StationName", {}).get("Zh_tw") or item.get("StationName", {}).get("En")
                    lat = float(pos.get("PositionLat"))
                    lon = float(pos.get("PositionLon"))
                    if not station_uid or not name:
                        continue
                    stations.append(
                        MetroStation(
                            station_uid=station_uid,
                            name=str(name),
                            lat=lat,
                            lon=lon,
                            operator=str(operator),
                        )
                    )
                except Exception:
                    continue

        if not stations:
            logger.warning("TDX returned 0 metro stations after parsing; continuing with empty list.")
        return stations

    def get_metro_stations_sample(self, *, operator: str | None = None, top: int = 10) -> list[MetroStation]:
        """Return a small sample of metro stations for one operator (first page only)."""
        op = operator or (self._settings.ingestion.tdx.metro_stations.operators or [None])[0]
        if not op:
            raise RuntimeError("TDX metro operators are not configured.")

        cache_key = f"tdx_metro_stations_sample:{op}:{int(top)}"

        def builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX metro station sample for operator=%s top=%s", op, top)
            base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
            endpoint = f"{base_url}/Rail/Metro/Station/{op}"
            select = self._settings.ingestion.tdx.metro_stations.select
            return self._fetch_first_page(endpoint, top=int(top), select=select)

        raw = self._cache.get_or_set(
            "tdx",
            cache_key,
            builder,
            ttl_seconds=min(self._settings.ingestion.tdx.cache_ttl_seconds, 60 * 10),
            stale_if_error=True,
            stale_predicate=self._stale_ok,
        )

        stations: list[MetroStation] = []
        for item in raw:
            try:
                pos = item.get("StationPosition") or {}
                station_uid = str(item.get("StationUID"))
                name = item.get("StationName", {}).get("Zh_tw") or item.get("StationName", {}).get("En")
                lat = float(pos.get("PositionLat"))
                lon = float(pos.get("PositionLon"))
                if not station_uid or not name:
                    continue
                stations.append(
                    MetroStation(
                        station_uid=station_uid,
                        name=str(name),
                        lat=lat,
                        lon=lon,
                        operator=str(op),
                    )
                )
            except Exception:
                continue

        if not stations:
            raise RuntimeError("TDX returned 0 metro stations in sample after parsing; check dataset/fields.")
        return stations

    def get_parking_lot_statuses(self, *, city: str | None = None) -> list[ParkingLotStatus]:
        city = city or self._settings.ingestion.tdx.city

        lots_cache_key = f"tdx_parking_lots:{city}"
        availability_cache_key = f"tdx_parking_availability:{city}"
        base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
        raw_lots = self._get_raw_list(
            dataset="parking_lots",
            scope=f"city_{city}",
            cache_key=lots_cache_key,
            endpoint=f"{base_url}/Parking/OffStreet/ParkingLot/City/{city}",
            select=self._settings.ingestion.tdx.parking_lots.select,
            top=self._settings.ingestion.tdx.parking_lots.top,
            key_field="ParkingLotUID",
            ttl_seconds=self._settings.ingestion.tdx.cache_ttl_seconds,
        )
        raw_availability = self._get_raw_list(
            dataset="parking_availability",
            scope=f"city_{city}",
            cache_key=availability_cache_key,
            endpoint=f"{base_url}/Parking/OffStreet/ParkingAvailability/City/{city}",
            select=self._settings.ingestion.tdx.parking_availability.select,
            top=self._settings.ingestion.tdx.parking_availability.top,
            key_field="ParkingLotUID",
            ttl_seconds=self._settings.ingestion.tdx.parking_availability_cache_ttl_seconds,
            allow_bulk=False,
        )

        availability_by_uid: dict[str, tuple[int | None, int | None]] = {}
        for item in raw_availability:
            try:
                lot_uid = str(item.get("ParkingLotUID"))
                if not lot_uid:
                    continue
                available = item.get("AvailableSpaces")
                total = item.get("TotalSpaces")
                available_i = int(available) if available is not None else None
                total_i = int(total) if total is not None else None
                availability_by_uid[lot_uid] = (available_i, total_i)
            except Exception:
                continue

        lots: list[ParkingLotStatus] = []
        for item in raw_lots:
            try:
                pos = item.get("ParkingLotPosition") or {}
                lot_uid = str(item.get("ParkingLotUID"))
                name = item.get("ParkingLotName", {}).get("Zh_tw") or item.get("ParkingLotName", {}).get("En")
                lat = float(pos.get("PositionLat"))
                lon = float(pos.get("PositionLon"))
                if not lot_uid or not name:
                    continue

                # TDX schemas differ by city; try multiple sources for totals.
                total_spaces = item.get("TotalSpaces")
                if total_spaces is None:
                    _, total_spaces = availability_by_uid.get(lot_uid, (None, None))

                available_i, total_i = availability_by_uid.get(lot_uid, (None, None))
                if total_i is None and total_spaces is not None:
                    try:
                        total_i = int(total_spaces)
                    except Exception:
                        total_i = None

                lots.append(
                    ParkingLotStatus(
                        parking_lot_uid=lot_uid,
                        name=str(name),
                        lat=lat,
                        lon=lon,
                        available_spaces=available_i,
                        total_spaces=total_i,
                        address=(
                            str(item.get("ParkingLotAddress") or item.get("Address") or "").strip()
                            or None
                        ),
                        service_time=(
                            str(item.get("ServiceTime") or item.get("OpenTime") or "").strip() or None
                        ),
                        fare_description=(
                            str(item.get("FareDescription") or item.get("FareInfo") or "").strip() or None
                        ),
                    )
                )
            except Exception:
                continue

        if not lots:
            logger.warning("TDX returned 0 parking lots after parsing; continuing with empty list.")
        return lots

    def get_parking_lot_statuses_sample(
        self, *, city: str | None = None, top: int = 10
    ) -> list[ParkingLotStatus]:
        """Return a small sample of parking lots merged with availability (first page only)."""
        city = city or self._settings.ingestion.tdx.city

        lots_cache_key = f"tdx_parking_lots_sample:{city}:{int(top)}"
        availability_cache_key = f"tdx_parking_availability_sample:{city}:{int(top)}"

        def lots_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX parking lot sample for city=%s top=%s", city, top)
            base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
            endpoint = f"{base_url}/Parking/OffStreet/ParkingLot/City/{city}"
            select = self._settings.ingestion.tdx.parking_lots.select
            return self._fetch_first_page(endpoint, top=int(top), select=select)

        def availability_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX parking availability sample for city=%s top=%s", city, top)
            base_url = self._settings.ingestion.tdx.base_url.rstrip("/")
            endpoint = f"{base_url}/Parking/OffStreet/ParkingAvailability/City/{city}"
            select = self._settings.ingestion.tdx.parking_availability.select
            return self._fetch_first_page(endpoint, top=int(top), select=select)

        raw_lots = self._cache.get_or_set(
            "tdx",
            lots_cache_key,
            lots_builder,
            ttl_seconds=min(self._settings.ingestion.tdx.cache_ttl_seconds, 60 * 10),
            stale_if_error=True,
            stale_predicate=self._stale_ok,
        )
        raw_availability = self._cache.get_or_set(
            "tdx",
            availability_cache_key,
            availability_builder,
            ttl_seconds=min(self._settings.ingestion.tdx.parking_availability_cache_ttl_seconds, 60 * 10),
            stale_if_error=True,
            stale_predicate=self._stale_ok,
        )

        availability_by_uid: dict[str, tuple[int | None, int | None]] = {}
        for item in raw_availability:
            try:
                lot_uid = str(item.get("ParkingLotUID"))
                if not lot_uid:
                    continue
                available = item.get("AvailableSpaces")
                total = item.get("TotalSpaces")
                availability_by_uid[lot_uid] = (
                    int(available) if available is not None else None,
                    int(total) if total is not None else None,
                )
            except Exception:
                continue

        lots: list[ParkingLotStatus] = []
        for item in raw_lots:
            try:
                pos = item.get("ParkingLotPosition") or {}
                lot_uid = str(item.get("ParkingLotUID"))
                name = item.get("ParkingLotName", {}).get("Zh_tw") or item.get("ParkingLotName", {}).get("En")
                lat = float(pos.get("PositionLat"))
                lon = float(pos.get("PositionLon"))
                if not lot_uid or not name:
                    continue

                total_spaces = item.get("TotalSpaces")
                if total_spaces is None:
                    _, total_spaces = availability_by_uid.get(lot_uid, (None, None))

                available_i, total_i = availability_by_uid.get(lot_uid, (None, None))
                if total_i is None and total_spaces is not None:
                    try:
                        total_i = int(total_spaces)
                    except Exception:
                        total_i = None

                lots.append(
                    ParkingLotStatus(
                        parking_lot_uid=lot_uid,
                        name=str(name),
                        lat=lat,
                        lon=lon,
                        available_spaces=available_i,
                        total_spaces=total_i,
                    )
                )
            except Exception:
                continue

        if not lots:
            raise RuntimeError("TDX returned 0 parking lots in sample after parsing; check dataset/fields.")
        return lots
