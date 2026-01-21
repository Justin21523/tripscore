from __future__ import annotations

# This module is the "orchestrator" for the recommendation pipeline.
# It wires together:
# - domain input (UserPreferences)
# - ingestion (TDX + weather clients)
# - feature scoring (accessibility, weather, preference, context)
# - final ranking + explainable breakdown (RecommendationResult)
#
# Design goal (beginner-friendly):
# - Keep each layer focused (ingestion does fetching; features do math; this file does orchestration).
# - Fail open when external data is missing (still return best-effort recommendations).

# Standard library imports (keep core runtime lightweight and predictable).
import logging  # Use structured logs instead of print() so apps can route/format logs consistently.
from datetime import datetime  # Used for timestamps in API responses (generated_at).
from pathlib import Path  # Used for OS-independent path handling when loading local catalogs.
from typing import Any
import time
from zoneinfo import ZoneInfo  # Used for timezone-aware timestamps (important for correct "generated_at").

# Local application imports (each layer stays separate to keep the codebase maintainable).
from tripscore.catalog.loader import (
    load_destinations_with_details,
)  # Loads Destination objects from on-disk catalogs.
from tripscore.config.overrides import apply_settings_overrides  # Safely applies per-request config overrides.
from tripscore.config.settings import Settings, get_settings  # Loads typed settings from YAML (and env overrides).
from tripscore.core.cache import FileCache  # Local file cache used by ingestion clients to avoid extra API calls.
from tripscore.core.env import resolve_project_path  # Resolve relative paths against the repo root.
from tripscore.core.time import ensure_tz  # Ensures datetimes are timezone-aware for correct comparisons.
from tripscore.domain.models import (
    ComponentWeights,  # Per-component weights used by the composite scorer (accessibility/weather/etc.).
    Destination,  # Catalog item to score (includes location, tags, and optional metadata).
    RecommendationItem,  # One ranked output item (Destination + ScoreBreakdown).
    RecommendationResult,  # Full response payload (query + list of ranked items).
    ScoreBreakdown,  # Explainable breakdown containing component scores and reasons.
    ScoreComponent,  # One component contribution entry inside ScoreBreakdown.
    UserPreferences,  # Input schema (origin, time window, weights, tags, optional overrides).
)
# Feature scorers (pure functions that convert raw data into normalized 0..1 scores + reasons).
from tripscore.features.accessibility import compute_accessibility_metrics, score_accessibility  # Transit + distance.
from tripscore.features.context import score_context  # Crowd/family score using district baselines + heuristics.
from tripscore.features.parking import compute_parking_metrics, score_parking_availability  # Parking proxy signal.
from tripscore.features.preference_match import score_preference_match  # Tag-based preference matching.
from tripscore.features.weather import score_weather  # Weather suitability (rain + temperature).
# Ingestion clients (fetch external data; may fail, so we handle errors gracefully).
from tripscore.ingestion.tdx_client import TdxClient  # Transport Data eXchange (Taiwan) client for transit signals.
from tripscore.ingestion.tdx_city_match import to_tdx_city
from tripscore.ingestion.weather_client import WeatherClient, WeatherSummary  # Open-Meteo client + summary schema.
# Scoring utilities (shared math helpers).
from tripscore.scoring.composite import clamp01, normalize_weights  # Clamp and normalize for stable scoring.
from tripscore.core.spatial_index import SpatialGridIndex

logger = logging.getLogger(__name__)  # Module-level logger (configured by app entrypoint).

SIGNAL_RULES_VERSION = "2026-01-21"


def _signal_status(*, required_missing: list[str], optional_missing: list[str]) -> tuple[str, list[str]]:
    issues: list[str] = []
    if required_missing:
        issues.extend(required_missing)
        return "degraded", issues
    if optional_missing:
        issues.extend(optional_missing)
        return "partial", issues
    return "ok", issues


def build_cache(settings: Settings) -> FileCache:
    # Convert the configured cache directory to a Path for cross-platform correctness.
    cache_dir = resolve_project_path(settings.cache.dir)
    # Build a cache object used by ingestion clients to reduce API calls (faster + fewer rate limits).
    return FileCache(
        cache_dir,
        enabled=settings.cache.enabled,
        default_ttl_seconds=settings.cache.default_ttl_seconds,
    )


def _effective_component_weights(
    preferences: UserPreferences,
    settings: Settings,
    *,
    preset_component_weights: dict[str, float] | None = None,
) -> dict[str, float]:
    # Start from server defaults so we always have a complete set of component keys.
    weights = dict(settings.scoring.composite_weights)
    # Apply preset weights first so user-provided weights can override presets.
    if preset_component_weights:
        weights.update(preset_component_weights)
    # Apply per-request component weight overrides (None values are excluded to avoid clobbering defaults).
    if preferences.component_weights:
        override = preferences.component_weights.model_dump(exclude_none=True)
        weights.update(override)
    # Normalize so weights sum to 1.0 (stable, comparable contributions across components).
    return normalize_weights(weights)


def _effective_tag_weights(
    preferences: UserPreferences, settings: Settings, *, preset_tag_weights: dict[str, float] | None = None
) -> dict[str, float]:
    # Start from default tag weights in config so missing user values still have deterministic behavior.
    weights = dict(settings.features.preference_match.tag_weights_default)
    # Apply preset weights before per-request weights (presets provide a baseline intent).
    if preset_tag_weights:
        weights.update(preset_tag_weights)
    # Apply per-request weights last so users can override both defaults and presets.
    if preferences.tag_weights:
        weights.update(preferences.tag_weights)
    # We do not normalize tag weights here because tag scoring is component-specific (not global weights).
    return weights


def _effective_max_results(preferences: UserPreferences, settings: Settings) -> int:
    # Prefer an explicit request value, otherwise fall back to the server default from config.
    return int(preferences.max_results or settings.scoring.top_n_default)


def _passes_tag_filters(destination: Destination, *, required: list[str], excluded: list[str]) -> bool:
    # Convert destination tags to a set for fast membership checks (O(1) average lookup).
    tags = set(destination.tags)
    # If the user requires tags, every required tag must be present on the destination.
    if required and not set(required).issubset(tags):
        return False
    # If the user excludes tags, any overlap disqualifies the destination.
    if excluded and set(excluded).intersection(tags):
        return False
    # Passing both checks means the destination remains a candidate.
    return True


def recommend(
    preferences: UserPreferences,
    *,
    settings: Settings | None = None,
    destinations: list[Destination] | None = None,
    tdx_client: TdxClient | None = None,
    weather_client: WeatherClient | None = None,
) -> RecommendationResult:
    t0 = time.monotonic()
    timings_ms: dict[str, int] = {}

    # ---- Step 1: Resolve settings for THIS run (data flow: API/CLI -> recommender -> features) ----
    # Use injected settings (tests) or load the default config from YAML (normal runtime).
    settings = settings or get_settings()
    # Apply per-request settings overrides (safe allowlist) so tuning is isolated to this request.
    settings = apply_settings_overrides(settings, preferences.settings_overrides)

    # ---- Step 2: Construct clients (unless the caller injected stubs for testing) ----
    # We only build a FileCache when we need to create real clients (injected clients already exist).
    cache: FileCache | None = None
    # Create a real TDX client if the caller did not inject one (keeps tests offline and deterministic).
    if tdx_client is None:
        # Lazily create cache so API runtime does not build unused objects when clients are injected.
        if cache is None:
            cache = build_cache(settings)
        # The TDX client uses the cache to memoize OAuth tokens and API responses.
        tdx_client = TdxClient(settings, cache)
    # Create a real Weather client if the caller did not inject one (same injection pattern as above).
    if weather_client is None:
        # Reuse the same cache instance for both clients to avoid duplicating storage and IO.
        if cache is None:
            cache = build_cache(settings)
        # The weather client caches responses per (lat, lon, time window).
        weather_client = WeatherClient(settings, cache)

    # ---- Step 3: Resolve an optional preset (server-defined config profile) ----
    # Presets live in config and are shared across clients; we validate the name early for clear errors.
    preset = None
    if preferences.preset:
        preset = settings.presets.get(preferences.preset)
        # Raising ValueError here becomes a 400 in the API layer (invalid user input).
        if preset is None:
            raise ValueError(f"Unknown preset '{preferences.preset}'.")

    # ---- Step 4: Normalize times (timezone-aware comparisons are less error-prone) ----
    # ensure_tz only adds a timezone when missing; it does not convert between timezones.
    start = ensure_tz(preferences.time_window.start, settings.app.timezone)
    end = ensure_tz(preferences.time_window.end, settings.app.timezone)

    # ---- Step 5: Compute effective weights/tags/top-N with a clear precedence order ----
    # Precedence: config defaults -> preset -> per-request override.
    effective_weights = _effective_component_weights(
        preferences,
        settings,
        preset_component_weights=(preset.component_weights if preset else None),
    )

    # Tag weights are used by the tag-based preference feature scorer (not the global composite).
    effective_tags = _effective_tag_weights(
        preferences,
        settings,
        preset_tag_weights=(preset.tag_weights if preset else None),
    )
    # Top-N controls how many ranked results we return to the user.
    effective_top_n = _effective_max_results(preferences, settings)

    # ---- Step 6: Resolve "importance" knobs (some may come from presets) ----
    weather_rain_importance = (
        preferences.weather_rain_importance
        if preferences.weather_rain_importance is not None
        else (preset.weather_rain_importance if preset else None)
    )
    avoid_crowds_importance = (
        preferences.avoid_crowds_importance
        if preferences.avoid_crowds_importance is not None
        else (preset.avoid_crowds_importance if preset else None)
    )
    family_friendly_importance = (
        preferences.family_friendly_importance
        if preferences.family_friendly_importance is not None
        else (preset.family_friendly_importance if preset else None)
    )
    required_tags = sorted(
        {*(preferences.required_tags or []), *((preset.required_tags or []) if preset else [])}
    )
    excluded_tags = sorted(
        {*(preferences.excluded_tags or []), *((preset.excluded_tags or []) if preset else [])}
    )

    # ---- Step 7: Build a normalized query to return in the response (debuggable + reproducible) ----
    # We return the effective values (after applying defaults/presets) so users can see what was used.
    normalized_query = preferences.model_copy(
        update={
            # Store timezone-fixed timestamps so downstream feature scorers compare correctly.
            "time_window": preferences.time_window.model_copy(update={"start": start, "end": end}),
            # Store the effective top-N to make the response self-describing.
            "max_results": effective_top_n,
            # Store tag weights actually used (includes defaults/preset overrides).
            "tag_weights": effective_tags,
            # Store normalized component weights so the sum is always 1.0.
            "component_weights": ComponentWeights(
                accessibility=effective_weights["accessibility"],
                weather=effective_weights["weather"],
                preference=effective_weights["preference"],
                context=effective_weights["context"],
            ),
            # Store the resolved per-request "importance" knobs (may be None to use config defaults).
            "weather_rain_importance": weather_rain_importance,
            "avoid_crowds_importance": avoid_crowds_importance,
            "family_friendly_importance": family_friendly_importance,
            # Store the resolved tag filters used for candidate pruning.
            "required_tags": required_tags,
            "excluded_tags": excluded_tags,
        }
    )

    # ---- Step 8: Load destination catalog (unless tests inject a small in-memory list) ----
    if destinations is None:
        # We keep the catalog path in config so the system is reproducible across environments.
        catalog_path = Path(settings.catalog.path)
        # The loader parses the JSON into typed Destination objects.
        destinations = load_destinations_with_details(
            catalog_path=catalog_path, details_path=getattr(settings.catalog, "details_path", None)
        )
    timings_ms["load_catalog"] = int((time.monotonic() - t0) * 1000)

    # ---- Step 9: Apply tag filters (fast pruning before we do any expensive API calls) ----
    candidates = [
        d
        for d in destinations
        if _passes_tag_filters(d, required=normalized_query.required_tags, excluded=normalized_query.excluded_tags)
    ]
    timings_ms["candidate_filter"] = int((time.monotonic() - t0) * 1000)

    # ---- Step 10: Ingest external signals (fail-open, because external APIs can be unavailable) ----
    # We fetch once per request (not per destination) to keep network usage bounded.
    t_ingest = time.monotonic()
    bus_stops_by_city: dict[str, list] = {}
    bike_stations_by_city: dict[str, list] = {}
    parking_lots_by_city: dict[str, list] = {}
    metro_stations = None
    tdx_missing: dict[str, dict[str, bool]] = {}
    cities: set[str] = set()
    weather_error_count = 0
    # Multi-city mode: do not make network calls during recommendation runs.
    # We rely on the background daemon to prefetch bulk datasets into the cache.
    for d in candidates:
        c = to_tdx_city(getattr(d, "city", None))
        if c:
            cities.add(c)
    if not cities:
        cities.add(settings.ingestion.tdx.city)

    for city in sorted(cities):
        try:
            bus = tdx_client.get_bus_stops_bulk(city=city)
        except Exception:
            bus = []
        bus_stops_by_city[city] = bus

        try:
            bike = tdx_client.get_bike_stations_bulk(city=city)
        except Exception:
            bike = []
        bike_stations_by_city[city] = bike

        try:
            park = tdx_client.get_parking_lots_bulk(city=city)
        except Exception:
            park = []
        parking_lots_by_city[city] = park

        tdx_missing[city] = {
            "bus_stops": not bool(bus),
            "bike_stations": not bool(bike),
            "parking_lots": not bool(park),
        }

    try:
        metro_stations = tdx_client.get_metro_stations_bulk()
    except Exception:
        metro_stations = None
    timings_ms["ingest_tdx"] = int((time.monotonic() - t_ingest) * 1000)

    # Build spatial indices once per city (huge speedup for large catalogs).
    bus_index_by_city: dict[str, SpatialGridIndex] = {}
    bike_index_by_city: dict[str, SpatialGridIndex] = {}
    parking_index_by_city: dict[str, SpatialGridIndex] = {}
    metro_index: SpatialGridIndex | None = None
    try:
        for city, items in bus_stops_by_city.items():
            if items:
                bus_index_by_city[city] = SpatialGridIndex(items, get_latlon=lambda s: (s.lat, s.lon))
        for city, items in bike_stations_by_city.items():
            if items:
                bike_index_by_city[city] = SpatialGridIndex(items, get_latlon=lambda s: (s.lat, s.lon))
        for city, items in parking_lots_by_city.items():
            if items:
                parking_index_by_city[city] = SpatialGridIndex(items, get_latlon=lambda s: (s.lat, s.lon))
        if metro_stations:
            metro_index = SpatialGridIndex(metro_stations, get_latlon=lambda s: (s.lat, s.lon))
    except Exception:
        pass

    # ---- Step 11: Score every candidate destination (pure math + best-effort ingestion) ----
    # Note: This loop may call the weather API per destination; caching is critical for speed.
    t_score = time.monotonic()
    t_weather = 0.0
    results: list[RecommendationItem] = []
    for dest in candidates:
        dest_city = to_tdx_city(getattr(dest, "city", None)) or settings.ingestion.tdx.city
        bus_stops = bus_stops_by_city.get(dest_city) or None
        bike_stations = bike_stations_by_city.get(dest_city) or None
        parking_lots = parking_lots_by_city.get(dest_city) or None
        bus_index = bus_index_by_city.get(dest_city)
        bike_index = bike_index_by_city.get(dest_city)
        parking_index = parking_index_by_city.get(dest_city)

        # --- 11a) Accessibility scoring (origin proximity + local transit density) ---
        metrics = compute_accessibility_metrics(
            dest,
            origin=normalized_query.origin,
            bus_stops=bus_stops,
            bus_radius_m=settings.ingestion.tdx.accessibility.radius_m,
            bike_stations=bike_stations,
            bike_radius_m=settings.ingestion.tdx.accessibility.bike.radius_m,
            metro_stations=metro_stations,
            metro_radius_m=settings.ingestion.tdx.accessibility.metro.radius_m,
            bus_index=bus_index,
            bike_index=bike_index,
            metro_index=metro_index,
        )
        # Convert raw accessibility metrics into a normalized 0..1 score + explainable details.
        a_score, a_details, a_reasons = score_accessibility(metrics, settings=settings)
        # Attach ingestion errors so the UI can explain why a score may look "neutral" or degraded.
        tdx_errors: dict[str, str] = {}
        if not bus_stops:
            tdx_errors["bus_stops"] = f"No bulk bus_stops data for city={dest_city} yet."
            a_reasons = [*a_reasons, "TDX bus stop data unavailable"]
        if not bike_stations:
            tdx_errors["bike"] = f"No bulk bike_stations data for city={dest_city} yet."
            a_reasons = [*a_reasons, "TDX bike station data unavailable"]
        if not metro_stations:
            tdx_errors["metro"] = "No bulk metro station data yet."
            a_reasons = [*a_reasons, "TDX metro station data unavailable"]
        a_status, a_issues = _signal_status(required_missing=list(tdx_errors.keys()), optional_missing=[])
        if tdx_errors:
            a_details = {**a_details, "tdx_errors": tdx_errors}
        a_details = {**a_details, "signal_status": a_status, "signal_issues": a_issues}

        weather_ok = True
        t_w0 = time.monotonic()
        try:
            # Fetch a weather summary for this destination and time window (may be cached).
            summary = weather_client.get_summary(lat=dest.location.lat, lon=dest.location.lon, start=start, end=end)
        except Exception as e:
            # Fail open: if weather fails, we return neutral values so the system still produces output.
            summary = WeatherSummary(max_precipitation_probability=None, mean_temperature_c=None)
            # Log the failure with destination ID so operators can correlate with upstream outages.
            logger.warning("Weather ingestion failed for %s: %s", dest.id, str(e))
            weather_ok = False
            weather_error_count += 1
        finally:
            t_weather += time.monotonic() - t_w0

        # --- 11b) Weather scoring (rain + temperature, adjusted by indoor/outdoor tags) ---
        w_score, w_details, w_reasons = score_weather(
            summary, destination=dest, preferences=normalized_query, settings=settings
        )
        w_status, w_issues = _signal_status(required_missing=[] if weather_ok else ["weather"], optional_missing=[])
        w_details = {**(w_details or {}), "signal_status": w_status, "signal_issues": w_issues}
        # --- 11c) Preference scoring (tag-based match against user weights) ---
        p_score, p_details, p_reasons = score_preference_match(dest, preferences=normalized_query, settings=settings)
        p_details = {**(p_details or {}), "signal_status": "ok", "signal_issues": []}

        # --- 11d) Parking signal (optional) -> context scorer can blend it into crowd risk ---
        parking_score: float | None = None
        parking_details: dict | None = None
        if parking_lots:
            p_metrics = compute_parking_metrics(
                dest,
                lots=parking_lots,
                radius_m=settings.features.parking.radius_m,
                lots_index=parking_index,
            )
            parking_score, parking_details, _ = score_parking_availability(p_metrics, settings=settings)
        else:
            parking_details = {"error": f"No bulk parking_lots data for city={dest_city} (or unsupported)."}

        # --- 11e) Context scoring (crowd risk + family friendliness, optionally blended with parking) ---
        c_score, c_details, c_reasons = score_context(
            dest,
            preferences=normalized_query,
            settings=settings,
            parking_availability_score=parking_score,
            parking_details=parking_details,
        )
        c_optional_missing = ["parking"] if (parking_details and parking_details.get("error")) else []
        c_status, c_issues = _signal_status(required_missing=[], optional_missing=c_optional_missing)
        c_details = {**(c_details or {}), "signal_status": c_status, "signal_issues": c_issues}

        # ---- Step 11f: Build the explainable score breakdown used by API + UI ----
        # Each component contributes: contribution = score * normalized_weight (clamped into 0..1).
        # Clamping keeps the UI stable even if a scorer accidentally returns values out of range.
        components = [
            ScoreComponent(
                name="accessibility",
                score=clamp01(a_score),
                weight=float(effective_weights["accessibility"]),
                contribution=clamp01(a_score * float(effective_weights["accessibility"])),
                details=a_details,
                reasons=a_reasons,
            ),
            ScoreComponent(
                name="weather",
                score=clamp01(w_score),
                weight=float(effective_weights["weather"]),
                contribution=clamp01(w_score * float(effective_weights["weather"])),
                details=w_details,
                reasons=w_reasons,
            ),
            ScoreComponent(
                name="preference",
                score=clamp01(p_score),
                weight=float(effective_weights["preference"]),
                contribution=clamp01(p_score * float(effective_weights["preference"])),
                details=p_details,
                reasons=p_reasons,
            ),
            ScoreComponent(
                name="context",
                score=clamp01(c_score),
                weight=float(effective_weights["context"]),
                contribution=clamp01(c_score * float(effective_weights["context"])),
                details=c_details,
                reasons=c_reasons,
            ),
        ]

        # Total score is the sum of contributions (weights already sum to 1.0 by construction).
        total_score = clamp01(sum(c.contribution for c in components))
        # ScoreBreakdown is what makes the system "explainable" (UI can show components + reasons).
        breakdown = ScoreBreakdown(
            destination_id=dest.id,
            destination_name=dest.name,
            total_score=total_score,
            components=components,
        )
        # We keep the full Destination payload so the UI can render name, tags, and map position.
        item_meta = {
            "data_completeness": {
                "tdx_city": dest_city,
                "tdx_bus_stops": bool(bus_stops),
                "tdx_bike": bool(bike_stations),
                "tdx_metro": bool(metro_stations),
                "tdx_parking": bool(parking_lots),
                "weather": bool(weather_ok),
            },
            "signal_status": {
                "accessibility": a_status,
                "weather": w_status,
                "preference": "ok",
                "context": c_status,
            },
            "signal_issues": {
                "accessibility": a_issues,
                "weather": w_issues,
                "preference": [],
                "context": c_issues,
            },
        }
        results.append(RecommendationItem(destination=dest, breakdown=breakdown, meta=item_meta))
    timings_ms["score_total"] = int((time.monotonic() - t_score) * 1000)
    timings_ms["weather_total"] = int(t_weather * 1000)

    # ---- Step 12: Rank results (descending score) and return Top-N ----
    t_rank = time.monotonic()
    results.sort(key=lambda r: r.breakdown.total_score, reverse=True)
    timings_ms["rank"] = int((time.monotonic() - t_rank) * 1000)

    # Use server timezone for generated_at so timestamps are consistent across API and UI.
    generated_at = datetime.now(ZoneInfo(settings.app.timezone))
    warnings: list[dict[str, Any]] = []
    missing_cities = [c for c, v in tdx_missing.items() if any(bool(x) for x in (v or {}).values())]
    if missing_cities:
        warnings.append(
            {
                "code": "TDX_BULK_PARTIAL",
                "message": "Some TDX bulk datasets are missing or incomplete for selected cities.",
                "detail": {"cities": sorted(missing_cities), "missing": tdx_missing},
            }
        )
    if weather_error_count:
        warnings.append(
            {
                "code": "WEATHER_PARTIAL",
                "message": "Weather data failed for some destinations; scores may be less precise.",
                "detail": {"failed_destination_count": int(weather_error_count)},
            }
        )

    meta = {
        "data_sources": {
            "tdx": {
                "cities": sorted(list(cities)),
                "bus_stops_count_by_city": {c: len(v or []) for c, v in bus_stops_by_city.items()},
                "bike_stations_count_by_city": {c: len(v or []) for c, v in bike_stations_by_city.items()},
                "metro_stations_count": len(metro_stations or []),
                "parking_lots_count_by_city": {c: len(v or []) for c, v in parking_lots_by_city.items()},
            },
            "weather": {"failed_destination_count": int(weather_error_count)},
            "catalog": {"candidates_scored": len(candidates)},
        },
        "settings_snapshot": {
            "preset": normalized_query.preset,
            "max_results": int(effective_top_n),
            "component_weights": {k: float(v) for k, v in effective_weights.items()},
            "tag_weights": normalized_query.tag_weights or {},
            "required_tags": list(normalized_query.required_tags or []),
            "excluded_tags": list(normalized_query.excluded_tags or []),
            "overrides_enabled": bool(normalized_query.settings_overrides),
            "settings_overrides": normalized_query.settings_overrides or None,
            "effective_tdx_city": str(settings.ingestion.tdx.city),
            "timezone": str(settings.app.timezone),
            "bulk_mode": True,
        },
        "warnings": warnings,
        "timings_ms": timings_ms,
        "rules": {
            "version": SIGNAL_RULES_VERSION,
            "signal_status": {
                "ok": "All required signals were available.",
                "partial": "A non-critical signal was missing (score still computed with reduced confidence).",
                "degraded": "A required upstream signal was missing (fallback logic applied).",
            },
            "fallbacks": {
                "accessibility": "If transit signals are missing, uses origin distance and neutral transit density.",
                "weather": "If weather is unavailable, uses neutral weather values.",
                "context": "If parking is unavailable, crowd/parking risk is reduced to conservative defaults.",
            },
        },
    }

    # Return a structured result so clients (CLI/API/Web) all share the same response format.
    return RecommendationResult(
        generated_at=generated_at,
        query=normalized_query,
        results=results[:effective_top_n],
        meta=meta,
    )
