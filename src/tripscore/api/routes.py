"""
API routes.

Endpoints:
- POST `/api/recommendations`: main recommender entrypoint.
- GET  `/api/presets`: list configured presets.
- GET  `/api/settings`: public settings for the web UI (secrets redacted).
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from fastapi import APIRouter, HTTPException

from tripscore.catalog.loader import load_destinations_with_details
from tripscore.config.settings import get_settings
from tripscore.core.cache import FileCache, record_cache_stats
from tripscore.core.geo import GeoPoint as CoreGeoPoint, haversine_m
from tripscore.core.env import resolve_project_path
from tripscore.core.ingestion_meta import capture_ingestion_meta
from tripscore.domain.models import RecommendationResult, UserPreferences
from tripscore.ingestion.tdx_client import TdxClient
from tripscore.ingestion.weather_client import WeatherClient
from tripscore.quality.report import build_quality_report
from tripscore.recommender.recommend import recommend

router = APIRouter()


@lru_cache
def _cache() -> FileCache:
    settings = get_settings()
    return FileCache(
        resolve_project_path(settings.cache.dir),
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
    """Run the recommender with validated preferences and return Top-N results."""
    settings = get_settings()
    tdx_client, weather_client = _clients()
    try:
        with record_cache_stats() as stats, capture_ingestion_meta() as ing:
            result = recommend(preferences, settings=settings, tdx_client=tdx_client, weather_client=weather_client)
        meta = {**(result.meta or {}), "cache": stats.as_dict(), "freshness": ing.sources}
        return result.model_copy(update={"meta": meta})
    except ValueError as e:
        raise HTTPException(
            status_code=400,
            detail={"code": "VALIDATION_ERROR", "message": str(e)},
        ) from e
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail={"code": "INTERNAL_ERROR", "message": str(e)},
        ) from e


@router.get("/api/presets")
def get_presets() -> dict:
    """Return all server-configured presets (name + description + tuning knobs)."""
    settings = get_settings()
    presets = []
    for name, preset in (settings.presets or {}).items():
        payload = preset.model_dump(mode="json")
        try:
            import json
            from hashlib import sha256

            version = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
        except Exception:
            version = "unknown"
        presets.append({"name": name, "version": version, **payload})
    presets.sort(key=lambda p: p["name"])
    return {"presets": presets}


@router.get("/api/settings")
def get_public_settings() -> dict:
    """Return safe-to-expose settings for UI defaults (credentials removed)."""
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
        "catalog": {"path": data.get("catalog", {}).get("path"), "details_path": data.get("catalog", {}).get("details_path")},
        "ingestion": {
            "tdx": {
                "city": data.get("ingestion", {}).get("tdx", {}).get("city", "Taipei"),
                "accessibility": data.get("ingestion", {}).get("tdx", {}).get("accessibility", {}),
            },
            "weather": data.get("ingestion", {}).get("weather", {}),
        },
    }


@router.get("/api/catalog/meta")
def get_catalog_meta() -> dict:
    """Return discoverable catalog metadata for the web UI (tags/cities/districts counts)."""
    settings = get_settings()
    resolved = resolve_project_path(settings.catalog.path)
    destinations = load_destinations_with_details(
        catalog_path=resolved, details_path=getattr(settings.catalog, "details_path", None)
    )

    tag_counts: dict[str, int] = {}
    city_counts: dict[str, int] = {}
    districts_by_city: dict[str, set[str]] = {}

    for d in destinations:
        for t in d.tags:
            tag_counts[t] = tag_counts.get(t, 0) + 1
        if d.city:
            city_counts[d.city] = city_counts.get(d.city, 0) + 1
            if d.district:
                districts_by_city.setdefault(d.city, set()).add(d.district)

    return {
        "catalog_path": str(settings.catalog.path),
        "updated_at_unix": int(resolved.stat().st_mtime) if resolved.exists() else None,
        "destination_count": len(destinations),
        "tags": sorted(tag_counts.keys()),
        "tag_counts": dict(sorted(tag_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "cities": sorted(city_counts.keys()),
        "city_counts": dict(sorted(city_counts.items(), key=lambda kv: (-kv[1], kv[0]))),
        "districts_by_city": {c: sorted(list(ds)) for c, ds in districts_by_city.items()},
    }


@router.get("/api/geo/destinations")
def get_geo_destinations(ids: str | None = None) -> dict:
    """Return map-friendly destination points (optionally filtered by IDs)."""
    settings = get_settings()
    resolved = resolve_project_path(settings.catalog.path)
    destinations = load_destinations_with_details(
        catalog_path=resolved, details_path=getattr(settings.catalog, "details_path", None)
    )

    want: set[str] | None = None
    if ids:
        want = {s.strip() for s in ids.split(",") if s.strip()}

    out = []
    for d in destinations:
        if want is not None and d.id not in want:
            continue
        out.append(
            {
                "id": d.id,
                "name": d.name,
                "lat": d.location.lat,
                "lon": d.location.lon,
                "city": d.city,
                "district": d.district,
                "tags": d.tags,
            }
        )

    return {"updated_at_unix": int(resolved.stat().st_mtime) if resolved.exists() else None, "destinations": out}


@router.get("/api/quality/report")
def get_quality_report() -> dict:
    """Return an offline data quality report (no network)."""
    settings = get_settings()
    return build_quality_report(settings)


@router.get("/api/tdx/bus/routes")
def get_tdx_bus_routes(city: str | None = None) -> dict:
    """Return cached (or bulk-prefetched) bus routes for a city."""
    settings = get_settings()
    tdx_client, _ = _clients()
    city_name = str(city or settings.ingestion.tdx.city)
    routes = tdx_client.get_bus_routes(city=city_name)
    return {"city": city_name, "count": len(routes), "routes": [r.__dict__ for r in routes]}


@router.get("/api/tdx/bus/eta/nearby")
def get_tdx_bus_eta_nearby(
    lat: float,
    lon: float,
    city: str | None = None,
    radius_m: int = 400,
    max_stops: int = 8,
    max_rows: int = 40,
) -> dict:
    """Return bus ETA rows for nearby stops (targeted, short TTL, best-effort)."""
    settings = get_settings()
    tdx_client, _ = _clients()
    city_name = str(city or settings.ingestion.tdx.city)

    max_stops = max(1, min(20, int(max_stops)))
    max_rows = max(1, min(200, int(max_rows)))
    radius_m = max(50, min(3000, int(radius_m)))

    stops = tdx_client.get_bus_stops(city=city_name)
    origin = CoreGeoPoint(lat=float(lat), lon=float(lon))
    nearby: list[tuple[float, Any]] = []
    for s in stops:
        try:
            d = haversine_m(origin, CoreGeoPoint(lat=float(s.lat), lon=float(s.lon)))
        except Exception:
            continue
        if d <= radius_m:
            nearby.append((d, s))
    nearby.sort(key=lambda x: x[0])
    chosen = [s for _, s in nearby[:max_stops]]
    stop_uids = [s.stop_uid for s in chosen]
    stop_names = {s.stop_uid: s.name for s in chosen}

    rows = tdx_client.get_bus_eta(city=city_name, stop_uids=stop_uids)
    eta = []
    for r in rows:
        if r.estimate_seconds is None:
            continue
        eta.append(
            {
                "stop_uid": r.stop_uid,
                "stop_name": r.stop_name or stop_names.get(r.stop_uid),
                "route_uid": r.route_uid,
                "route_name": r.route_name,
                "estimate_seconds": r.estimate_seconds,
                "direction": r.direction,
                "updated_at": r.updated_at,
            }
        )
    eta.sort(key=lambda x: int(x["estimate_seconds"]))
    eta = eta[:max_rows]

    soonest = eta[0]["estimate_seconds"] if eta else None
    route_count = len({(e["route_uid"], e.get("direction")) for e in eta})

    return {
        "city": city_name,
        "query": {"lat": float(lat), "lon": float(lon), "radius_m": radius_m, "max_stops": max_stops},
        "stops": [{"stop_uid": s.stop_uid, "name": s.name, "lat": s.lat, "lon": s.lon} for s in chosen],
        "eta": eta,
        "summary": {"soonest_seconds": soonest, "route_count": route_count},
    }


@router.get("/api/tdx/parking/lots")
def get_tdx_parking_lots(city: str | None = None) -> dict:
    """Return cached (or bulk-prefetched) parking lots + availability for a city."""
    settings = get_settings()
    tdx_client, _ = _clients()
    city_name = str(city or settings.ingestion.tdx.city)
    lots = tdx_client.get_parking_lot_statuses(city=city_name)
    return {"city": city_name, "count": len(lots), "lots": [lot.__dict__ for lot in lots]}


@router.get("/config")
def get_config_legacy() -> dict:
    """Legacy alias for UI config (backwards compatible)."""
    return get_public_settings()


@router.get("/api/tdx/status")
def get_tdx_status() -> dict:
    """Return bulk-prefetch progress for the configured TDX city and metro operators."""
    settings = get_settings()
    cache_dir = resolve_project_path(settings.cache.dir)
    base = cache_dir / "tdx_bulk"

    def progress(dataset: str, scope: str) -> dict:
        p = base / dataset / f"{scope}.progress.json"
        d = base / dataset / f"{scope}.json"
        out = {
            "dataset": dataset,
            "scope": scope,
            "done": False,
            "error_status": None,
            "updated_at_unix": None,
            "progress_mtime_unix": int(p.stat().st_mtime) if p.exists() else None,
            "data_mtime_unix": int(d.stat().st_mtime) if d.exists() else None,
        }
        if not p.exists():
            return out
        try:
            import json

            payload = json.loads(p.read_text(encoding="utf-8")) or {}
            out["done"] = bool(payload.get("done", False))
            out["error_status"] = payload.get("error_status")
            out["updated_at_unix"] = payload.get("updated_at_unix")
        except Exception:
            pass
        return out

    city = settings.ingestion.tdx.city
    rows: list[dict] = []
    for ds in [
        "bus_stops",
        "bus_routes",
        "bike_stations",
        "bike_availability",
        "parking_lots",
        "parking_availability",
    ]:
        rows.append(progress(ds, f"city_{city}"))
    for op in settings.ingestion.tdx.metro_stations.operators:
        rows.append(progress("metro_stations", f"operator_{op}"))

    updated = [r.get("updated_at_unix") for r in rows if r.get("updated_at_unix")]
    last_updated = max(updated) if updated else None
    return {"city": city, "items": rows, "last_updated_at_unix": last_updated}
