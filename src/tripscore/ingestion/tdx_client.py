from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from tripscore.config.settings import Settings
from tripscore.core.cache import FileCache
from tripscore.core.http import get_json, post_form

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BusStop:
    stop_uid: str
    name: str
    lat: float
    lon: float


@dataclass(frozen=True)
class BikeStationStatus:
    station_uid: str
    name: str
    lat: float
    lon: float
    available_rent_bikes: int | None
    available_return_bikes: int | None


@dataclass(frozen=True)
class MetroStation:
    station_uid: str
    name: str
    lat: float
    lon: float
    operator: str


@dataclass(frozen=True)
class ParkingLotStatus:
    parking_lot_uid: str
    name: str
    lat: float
    lon: float
    available_spaces: int | None
    total_spaces: int | None


class TdxClient:
    def __init__(self, settings: Settings, cache: FileCache):
        self._settings = settings
        self._cache = cache
        self._access_token: str | None = None
        self._token_expires_at_unix: int = 0

    def _require_credentials(self) -> tuple[str, str]:
        client_id = self._settings.ingestion.tdx.client_id
        client_secret = self._settings.ingestion.tdx.client_secret
        if not client_id or not client_secret:
            raise RuntimeError(
                "TDX credentials are not configured. Set TDX_CLIENT_ID and TDX_CLIENT_SECRET."
            )
        return client_id, client_secret

    def _get_access_token(self) -> str:
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
        token = self._get_access_token()
        headers = {"Authorization": f"Bearer {token}"}

        results: list[dict[str, Any]] = []
        skip = 0

        while True:
            params = {"$format": "JSON", "$top": top, "$skip": skip, "$select": select}
            page = get_json(
                endpoint,
                params=params,
                headers=headers,
                timeout_seconds=self._settings.app.http_timeout_seconds,
            )
            if not isinstance(page, list):
                raise RuntimeError("Unexpected TDX response shape; expected a list.")

            results.extend(page)
            if len(page) < top:
                break
            skip += top

        return results

    def get_bus_stops(self, *, city: str | None = None) -> list[BusStop]:
        city = city or self._settings.ingestion.tdx.city
        cache_key = f"tdx_bus_stops:{city}"

        def builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX bus stops for city=%s", city)
            return self._fetch_bus_stops_raw(city)

        raw = self._cache.get_or_set(
            "tdx",
            cache_key,
            builder,
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
            raise RuntimeError("TDX returned 0 bus stops after parsing; check dataset/fields.")
        return stops

    def get_youbike_station_statuses(self, *, city: str | None = None) -> list[BikeStationStatus]:
        city = city or self._settings.ingestion.tdx.city

        stations_cache_key = f"tdx_bike_stations:{city}"
        availability_cache_key = f"tdx_bike_availability:{city}"

        def stations_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX bike stations for city=%s", city)
            return self._fetch_bike_stations_raw(city)

        def availability_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX bike availability for city=%s", city)
            return self._fetch_bike_availability_raw(city)

        raw_stations = self._cache.get_or_set(
            "tdx",
            stations_cache_key,
            stations_builder,
            ttl_seconds=self._settings.ingestion.tdx.cache_ttl_seconds,
        )
        raw_availability = self._cache.get_or_set(
            "tdx",
            availability_cache_key,
            availability_builder,
            ttl_seconds=self._settings.ingestion.tdx.bike_availability_cache_ttl_seconds,
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
            raise RuntimeError("TDX returned 0 bike stations after parsing; check dataset/fields.")
        return stations

    def get_metro_stations(self, *, operators: list[str] | None = None) -> list[MetroStation]:
        operators = operators or self._settings.ingestion.tdx.metro_stations.operators
        if not operators:
            raise RuntimeError("TDX metro operators are not configured.")

        stations: list[MetroStation] = []
        for operator in operators:
            cache_key = f"tdx_metro_stations:{operator}"

            def builder(op: str = operator) -> list[dict[str, Any]]:
                logger.info("Fetching TDX metro stations for operator=%s", op)
                return self._fetch_metro_stations_raw(op)

            raw = self._cache.get_or_set(
                "tdx",
                cache_key,
                builder,
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
            raise RuntimeError("TDX returned 0 metro stations after parsing; check dataset/fields.")
        return stations

    def get_parking_lot_statuses(self, *, city: str | None = None) -> list[ParkingLotStatus]:
        city = city or self._settings.ingestion.tdx.city

        lots_cache_key = f"tdx_parking_lots:{city}"
        availability_cache_key = f"tdx_parking_availability:{city}"

        def lots_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX parking lots for city=%s", city)
            return self._fetch_parking_lots_raw(city)

        def availability_builder() -> list[dict[str, Any]]:
            logger.info("Fetching TDX parking availability for city=%s", city)
            return self._fetch_parking_availability_raw(city)

        raw_lots = self._cache.get_or_set(
            "tdx",
            lots_cache_key,
            lots_builder,
            ttl_seconds=self._settings.ingestion.tdx.cache_ttl_seconds,
        )
        raw_availability = self._cache.get_or_set(
            "tdx",
            availability_cache_key,
            availability_builder,
            ttl_seconds=self._settings.ingestion.tdx.parking_availability_cache_ttl_seconds,
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
                    )
                )
            except Exception:
                continue

        if not lots:
            raise RuntimeError("TDX returned 0 parking lots after parsing; check dataset/fields.")
        return lots
