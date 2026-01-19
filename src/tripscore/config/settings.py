"""
Application settings (Pydantic).

Settings are loaded from `src/tripscore/config/defaults.yaml`, then optionally overridden by:
- environment variables (e.g., `TDX_CLIENT_ID`, `TDX_CLIENT_SECRET`)
- an external YAML file via `TRIPSCORE_CONFIG_PATH`

Design rule:
- Tuning knobs live in YAML, not hard-coded in business logic.
"""

from __future__ import annotations

import os
from functools import lru_cache
from importlib import resources
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field

from tripscore.core.env import load_dotenv_if_present, resolve_project_path

def _read_package_yaml(filename: str) -> dict[str, Any]:
    """Read a YAML file packaged inside `tripscore.config`."""
    text = resources.files("tripscore.config").joinpath(filename).read_text(encoding="utf-8")
    data = yaml.safe_load(text) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root object for {filename}; expected a mapping.")
    return data


def _read_yaml_file(path: str | Path) -> dict[str, Any]:
    """Read a YAML file from disk and return its mapping root."""
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML root object for {path}; expected a mapping.")
    return data


class AppSettings(BaseModel):
    name: str = "TripScore"
    timezone: str = "Asia/Taipei"
    http_timeout_seconds: float = 15
    log_level: str = "INFO"


class CacheSettings(BaseModel):
    enabled: bool = True
    dir: str = ".cache/tripscore"
    default_ttl_seconds: int = 60 * 60 * 24


class CatalogSettings(BaseModel):
    path: str = "data/catalogs/destinations.json"
    details_path: str | None = None


class TdxBusStopsSettings(BaseModel):
    top: int = 1000
    select: str = "StopUID,StopName,StopPosition"


class TdxBusEstimatedTimeSettings(BaseModel):
    top: int = 2000
    select: str = "StopUID,StopName,RouteUID,RouteName,EstimateTime,StopSequence,Direction,UpdateTime"


class TdxBikeStationsSettings(BaseModel):
    top: int = 1000
    select: str = "StationUID,StationName,StationPosition"


class TdxBikeAvailabilitySettings(BaseModel):
    top: int = 1000
    select: str = "StationUID,AvailableRentBikes,AvailableReturnBikes"


class TdxMetroStationsSettings(BaseModel):
    operators: list[str] = Field(default_factory=lambda: ["TRTC"])
    top: int = 1000
    select: str = "StationUID,StationName,StationPosition"


class TdxParkingLotsSettings(BaseModel):
    top: int = 1000
    select: str = "ParkingLotUID,ParkingLotName,ParkingLotPosition,TotalSpaces"


class TdxParkingAvailabilitySettings(BaseModel):
    top: int = 1000
    select: str = "ParkingLotUID,AvailableSpaces,TotalSpaces"


class TdxBusRoutesSettings(BaseModel):
    top: int = 1000
    select: str = "RouteUID,RouteName"


class TdxRetrySettings(BaseModel):
    max_attempts: int = Field(5, ge=0)
    base_delay_seconds: float = Field(0.5, ge=0)
    max_delay_seconds: float = Field(10.0, ge=0)

class TdxBulkSettings(BaseModel):
    enabled: bool = True
    max_pages_per_call: int = Field(1, ge=1)
    max_seconds_per_call: float | None = Field(20.0, ge=0)


class MetroAccessibilitySettings(BaseModel):
    radius_m: int = 700
    count_cap: int = 10
    distance_cap_m: int = 900
    score_weights: dict[Literal["count", "distance"], float] = Field(
        default_factory=lambda: {"count": 0.6, "distance": 0.4}
    )


class BikeAccessibilitySettings(BaseModel):
    radius_m: int = 500
    station_cap: int = 8
    available_bikes_cap: int = 40
    score_weights: dict[Literal["stations", "available_bikes"], float] = Field(
        default_factory=lambda: {"stations": 0.4, "available_bikes": 0.6}
    )


class AccessibilitySettings(BaseModel):
    radius_m: int = 500
    count_cap: int = 20
    distance_cap_m: int = 800
    origin_distance_cap_m: int = 15_000
    local_score_weights: dict[Literal["count", "distance"], float] = Field(
        default_factory=lambda: {"count": 0.7, "distance": 0.3}
    )
    metro: MetroAccessibilitySettings = Field(default_factory=MetroAccessibilitySettings)
    bike: BikeAccessibilitySettings = Field(default_factory=BikeAccessibilitySettings)
    local_transit_signal_weights: dict[Literal["bus", "metro", "bike"], float] = Field(
        default_factory=lambda: {"bus": 0.55, "metro": 0.3, "bike": 0.15}
    )
    blend_weights: dict[Literal["local_transit", "origin_proximity"], float] = Field(
        default_factory=lambda: {"local_transit": 0.7, "origin_proximity": 0.3}
    )


class TdxSettings(BaseModel):
    base_url: str
    token_url: str
    city: str = "Taipei"
    bus_stops: TdxBusStopsSettings = Field(default_factory=TdxBusStopsSettings)
    bus_estimated_time: TdxBusEstimatedTimeSettings = Field(default_factory=TdxBusEstimatedTimeSettings)
    bike_stations: TdxBikeStationsSettings = Field(default_factory=TdxBikeStationsSettings)
    bike_availability: TdxBikeAvailabilitySettings = Field(default_factory=TdxBikeAvailabilitySettings)
    metro_stations: TdxMetroStationsSettings = Field(default_factory=TdxMetroStationsSettings)
    parking_lots: TdxParkingLotsSettings = Field(default_factory=TdxParkingLotsSettings)
    parking_availability: TdxParkingAvailabilitySettings = Field(default_factory=TdxParkingAvailabilitySettings)
    bus_routes: TdxBusRoutesSettings = Field(default_factory=TdxBusRoutesSettings)
    parking_availability_cache_ttl_seconds: int = 300
    bike_availability_cache_ttl_seconds: int = 300
    bus_estimated_time_cache_ttl_seconds: int = 30
    cache_ttl_seconds: int = 60 * 60 * 24
    request_spacing_seconds: float = Field(0.0, ge=0)
    retry: TdxRetrySettings = Field(default_factory=TdxRetrySettings)
    bulk: TdxBulkSettings = Field(default_factory=TdxBulkSettings)
    accessibility: AccessibilitySettings = Field(default_factory=AccessibilitySettings)
    client_id: str | None = None
    client_secret: str | None = None


class ComfortTemperatureC(BaseModel):
    min: float = 22
    max: float = 28


class WeatherAggregationSettings(BaseModel):
    precipitation_probability: Literal["max", "mean"] = "max"
    temperature_2m: Literal["max", "mean"] = "mean"


class WeatherScoreWeights(BaseModel):
    rain: float = 0.7
    temperature: float = 0.3


class WeatherSettings(BaseModel):
    base_url: str
    timezone: str = "Asia/Taipei"
    hourly_fields: list[str] = Field(
        default_factory=lambda: ["temperature_2m", "precipitation_probability"]
    )
    cache_ttl_seconds: int = 60 * 60
    aggregation: WeatherAggregationSettings = Field(default_factory=WeatherAggregationSettings)
    comfort_temperature_c: ComfortTemperatureC = Field(default_factory=ComfortTemperatureC)
    temperature_penalty_scale_c: float = 10
    score_weights: WeatherScoreWeights = Field(default_factory=WeatherScoreWeights)


class IngestionSettings(BaseModel):
    tdx: TdxSettings
    weather: WeatherSettings


class PreferenceMatchSettings(BaseModel):
    tag_weights_default: dict[str, float] = Field(default_factory=dict)


class WeatherFeatureSettings(BaseModel):
    rain_importance_multiplier: dict[Literal["indoor", "outdoor"], float] = Field(
        default_factory=lambda: {"indoor": 0.7, "outdoor": 1.2}
    )


class ParkingFeatureSettings(BaseModel):
    radius_m: int = 800
    lot_cap: int = 10
    available_spaces_cap: int = 400
    score_weights: dict[Literal["lots", "available_spaces"], float] = Field(
        default_factory=lambda: {"lots": 0.3, "available_spaces": 0.7}
    )


class ContextPeakHourWindow(BaseModel):
    start_hour: int = Field(..., ge=0, le=23)
    end_hour: int = Field(..., ge=0, le=24)
    multiplier: float = Field(1.0, ge=0)


class ContextCrowdSettings(BaseModel):
    default_risk: float = Field(0.5, ge=0, le=1)
    weekend_multiplier: float = Field(1.0, ge=0)
    parking_risk_weight: float = Field(0.35, ge=0, le=1)
    peak_hours: list[ContextPeakHourWindow] = Field(default_factory=list)
    tag_risk_adjustments: dict[str, float] = Field(default_factory=dict)


class ContextFamilySettings(BaseModel):
    default_score: float = Field(0.5, ge=0, le=1)
    tag_bonus: float = Field(0.25, ge=0, le=1)


class ContextSettings(BaseModel):
    district_factors_path: str = "data/factors/district_factors.json"
    default_avoid_crowds_importance: float = Field(0.7, ge=0, le=1)
    default_family_friendly_importance: float = Field(0.3, ge=0, le=1)
    crowd: ContextCrowdSettings = Field(default_factory=ContextCrowdSettings)
    family: ContextFamilySettings = Field(default_factory=ContextFamilySettings)


class FeaturesSettings(BaseModel):
    weather: WeatherFeatureSettings = Field(default_factory=WeatherFeatureSettings)
    parking: ParkingFeatureSettings = Field(default_factory=ParkingFeatureSettings)
    preference_match: PreferenceMatchSettings = Field(default_factory=PreferenceMatchSettings)
    context: ContextSettings = Field(default_factory=ContextSettings)


class ScoringSettings(BaseModel):
    neutral_score: float = 0.5
    composite_weights: dict[Literal["accessibility", "weather", "preference", "context"], float] = Field(
        default_factory=lambda: {
            "accessibility": 0.35,
            "weather": 0.3,
            "preference": 0.2,
            "context": 0.15,
        }
    )
    top_n_default: int = 10


class PresetDefinition(BaseModel):
    description: str = ""
    component_weights: dict[str, float] = Field(default_factory=dict)
    weather_rain_importance: float | None = Field(default=None, ge=0, le=1)
    avoid_crowds_importance: float | None = Field(default=None, ge=0, le=1)
    family_friendly_importance: float | None = Field(default=None, ge=0, le=1)
    tag_weights: dict[str, float] = Field(default_factory=dict)
    required_tags: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=list)


class Settings(BaseModel):
    app: AppSettings = Field(default_factory=AppSettings)
    cache: CacheSettings = Field(default_factory=CacheSettings)
    catalog: CatalogSettings = Field(default_factory=CatalogSettings)
    ingestion: IngestionSettings
    features: FeaturesSettings = Field(default_factory=FeaturesSettings)
    scoring: ScoringSettings = Field(default_factory=ScoringSettings)
    presets: dict[str, PresetDefinition] = Field(default_factory=dict)


def _apply_env_overrides(data: dict[str, Any]) -> dict[str, Any]:
    """Overlay selected environment variables onto raw settings payload.

    Note: We intentionally keep this whitelist small to avoid exposing unsafe overrides.
    """
    data = dict(data)
    cache_dir = os.getenv("TRIPSCORE_CACHE_DIR")
    if cache_dir:
        data.setdefault("cache", {})["dir"] = cache_dir

    log_level = os.getenv("TRIPSCORE_LOG_LEVEL")
    if log_level:
        data.setdefault("app", {})["log_level"] = log_level

    tdx_id = os.getenv("TDX_CLIENT_ID")
    tdx_secret = os.getenv("TDX_CLIENT_SECRET")
    if tdx_id:
        data.setdefault("ingestion", {}).setdefault("tdx", {})["client_id"] = tdx_id
    if tdx_secret:
        data.setdefault("ingestion", {}).setdefault("tdx", {})["client_secret"] = tdx_secret

    return data


@lru_cache
def get_settings() -> Settings:
    """Load and validate settings (cached)."""
    # Ensure repo-local `.env` is loaded (best-effort) before reading any env vars.
    # This makes CLI/API/notebooks work without manually `source`-ing shell profiles.
    load_dotenv_if_present()

    config_path = os.getenv("TRIPSCORE_CONFIG_PATH")
    raw = (
        _read_yaml_file(resolve_project_path(config_path))
        if config_path
        else _read_package_yaml("defaults.yaml")
    )
    raw = _apply_env_overrides(raw)
    return Settings.model_validate(raw)


@lru_cache
def get_logging_config() -> dict[str, Any]:
    """Load logging configuration (cached)."""
    return _read_package_yaml("logging.yaml")
