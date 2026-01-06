from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from tripscore.config.settings import Settings
from tripscore.core.cache import FileCache
from tripscore.core.http import get_json
from tripscore.core.time import ensure_tz

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WeatherSummary:
    max_precipitation_probability: float | None
    mean_temperature_c: float | None


class WeatherClient:
    def __init__(self, settings: Settings, cache: FileCache):
        self._settings = settings
        self._cache = cache

    def _fetch_open_meteo(self, lat: float, lon: float, start: datetime, end: datetime) -> dict[str, Any]:
        start_date = start.date().isoformat()
        end_date = end.date().isoformat()
        hourly = ",".join(self._settings.ingestion.weather.hourly_fields)

        params = {
            "latitude": lat,
            "longitude": lon,
            "hourly": hourly,
            "timezone": self._settings.ingestion.weather.timezone,
            "start_date": start_date,
            "end_date": end_date,
        }
        return get_json(
            self._settings.ingestion.weather.base_url,
            params=params,
            timeout_seconds=self._settings.app.http_timeout_seconds,
        )

    def get_summary(self, *, lat: float, lon: float, start: datetime, end: datetime) -> WeatherSummary:
        cache_key = f"openmeteo:{lat:.4f}:{lon:.4f}:{start.isoformat()}:{end.isoformat()}"

        def builder() -> dict[str, Any]:
            logger.info("Fetching weather for lat=%.4f lon=%.4f", lat, lon)
            return self._fetch_open_meteo(lat, lon, start, end)

        payload = self._cache.get_or_set(
            "weather",
            cache_key,
            builder,
            ttl_seconds=self._settings.ingestion.weather.cache_ttl_seconds,
        )

        hourly = payload.get("hourly") or {}
        times = hourly.get("time") or []
        temps = hourly.get("temperature_2m") or []
        rains = hourly.get("precipitation_probability") or []

        if not (isinstance(times, list) and isinstance(temps, list) and isinstance(rains, list)):
            return WeatherSummary(max_precipitation_probability=None, mean_temperature_c=None)

        tz = self._settings.ingestion.weather.timezone
        points: list[tuple[datetime, float | None, float | None]] = []
        for t, temp, rain in zip(times, temps, rains):
            try:
                dt = ensure_tz(datetime.fromisoformat(str(t)), tz)
                points.append((dt, float(temp) if temp is not None else None, float(rain) if rain is not None else None))
            except Exception:
                continue

        window_points = [p for p in points if start <= p[0] <= end]
        if not window_points:
            return WeatherSummary(max_precipitation_probability=None, mean_temperature_c=None)

        rain_vals = [p[2] for p in window_points if p[2] is not None]
        temp_vals = [p[1] for p in window_points if p[1] is not None]

        max_rain = max(rain_vals) if rain_vals else None
        mean_temp = (sum(temp_vals) / len(temp_vals)) if temp_vals else None

        return WeatherSummary(max_precipitation_probability=max_rain, mean_temperature_c=mean_temp)
