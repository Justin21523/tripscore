from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, HTTPException

from tripscore.config.settings import get_settings
from tripscore.core.cache import FileCache
from tripscore.domain.models import RecommendationResult, UserPreferences
from tripscore.ingestion.tdx_client import TdxClient
from tripscore.ingestion.weather_client import WeatherClient
from tripscore.recommender.recommend import recommend

router = APIRouter()


@lru_cache
def _cache() -> FileCache:
    settings = get_settings()
    return FileCache(
        Path(settings.cache.dir),
        enabled=settings.cache.enabled,
        default_ttl_seconds=settings.cache.default_ttl_seconds,
    )


@lru_cache
def _clients() -> tuple[TdxClient, WeatherClient]:
    settings = get_settings()
    cache = _cache()
    return TdxClient(settings, cache), WeatherClient(settings, cache)


@router.post("/api/recommendations", response_model=RecommendationResult)
def post_recommendations(preferences: UserPreferences) -> RecommendationResult:
    settings = get_settings()
    tdx_client, weather_client = _clients()
    try:
        return recommend(preferences, settings=settings, tdx_client=tdx_client, weather_client=weather_client)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e


@router.get("/api/presets")
def get_presets() -> dict:
    settings = get_settings()
    presets = []
    for name, preset in (settings.presets or {}).items():
        presets.append({"name": name, **preset.model_dump(mode="json")})
    presets.sort(key=lambda p: p["name"])
    return {"presets": presets}


@router.get("/api/settings")
def get_public_settings() -> dict:
    settings = get_settings()
    data = settings.model_dump(mode="json")

    # Avoid leaking secrets into the browser.
    try:
        data["ingestion"]["tdx"].pop("client_id", None)
        data["ingestion"]["tdx"].pop("client_secret", None)
    except Exception:
        pass

    return {
        "app": {"timezone": data.get("app", {}).get("timezone", "Asia/Taipei")},
        "scoring": data.get("scoring", {}),
        "features": data.get("features", {}),
        "ingestion": {
            "tdx": {
                "city": data.get("ingestion", {}).get("tdx", {}).get("city", "Taipei"),
                "accessibility": data.get("ingestion", {}).get("tdx", {}).get("accessibility", {}),
            },
            "weather": data.get("ingestion", {}).get("weather", {}),
        },
    }
