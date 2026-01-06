"""
Accessibility feature (destination-level).

This module lives in the `features/` layer:
- Ingestion (`ingestion/tdx_client.py`) fetches raw transport data from TDX (bus/metro/bike).
- Features convert those raw signals into stable numeric inputs (metrics + 0..1 scores).
- Scoring (`scoring/`) later combines feature scores into a final composite score.

Design goals:
- Explainable: return a score *and* a human-readable reason list.
- Config-driven: every cap/threshold/weight comes from `config/defaults.yaml`.
- Fail-open: if a dataset is missing, we degrade to a neutral score instead of crashing.
"""

from __future__ import annotations

# We use `dataclass` for light-weight immutable containers (faster and simpler than Pydantic here).
from dataclasses import asdict, dataclass

# `Settings` provides typed access to config values (weights, radii, caps, etc.).
from tripscore.config.settings import Settings
# We reuse the shared GeoPoint + distance function so all modules agree on distance math.
from tripscore.core.geo import GeoPoint as CoreGeoPoint
from tripscore.core.geo import haversine_m
# Domain models define what a destination and origin look like at the API boundary.
from tripscore.domain.models import Destination, GeoPoint as DomainGeoPoint
# These ingestion-layer dataclasses represent parsed TDX transport datasets.
from tripscore.ingestion.tdx_client import BikeStationStatus, BusStop, MetroStation
# Composite helpers: clamp scores to 0..1 and normalize a weight dict to sum to 1.
from tripscore.scoring.composite import clamp01, normalize_weights


@dataclass(frozen=True)
class AccessibilityMetrics:
    # NOTE: Most fields are optional because ingestion can fail (network, auth, dataset shape, etc.).
    # `None` means "data unavailable" (not "zero").
    bus_stops_within_radius: int | None
    bus_nearest_stop_distance_m: float | None
    bike_stations_within_radius: int | None
    bike_nearest_station_distance_m: float | None
    bike_available_rent_bikes_within_radius: int | None
    bike_available_return_bikes_within_radius: int | None
    metro_stations_within_radius: int | None
    metro_nearest_station_distance_m: float | None
    # This one is always computed because we always have an origin + destination coordinate.
    origin_distance_m: float


def compute_accessibility_metrics(
    destination: Destination,
    *,
    origin: DomainGeoPoint,
    bus_stops: list[BusStop] | None,
    bus_radius_m: int,
    bike_stations: list[BikeStationStatus] | None,
    bike_radius_m: int,
    metro_stations: list[MetroStation] | None,
    metro_radius_m: int,
) -> AccessibilityMetrics:
    """
    Compute raw, explainable accessibility metrics for a single destination.

    Why "metrics first"?
    - It keeps scoring deterministic and debuggable: we can log/inspect the raw numbers.
    - Multiple scoring strategies can reuse the same metrics (e.g., different weight presets).

    Performance note:
    - This implementation is O(N) over each station list per destination (no spatial index yet).
    - For a small MVP catalog (tens of destinations) this is acceptable, but it will not scale
      to thousands of destinations without pre-indexing (e.g., k-d tree / geohash buckets).
    """

    # Convert destination coordinates (domain model) into the shared core GeoPoint type.
    dest_pt = CoreGeoPoint(lat=destination.location.lat, lon=destination.location.lon)
    # Convert the user origin into the same representation so distance math is consistent.
    origin_pt = CoreGeoPoint(lat=origin.lat, lon=origin.lon)
    # Use haversine (great-circle distance) as a simple, robust city-scale distance proxy.
    origin_distance_m = haversine_m(origin_pt, dest_pt)

    # --- Bus stop metrics (density + nearest stop distance) ---
    if not bus_stops:
        # `None` indicates ingestion is missing, so the scoring layer can "fail open" to neutral.
        bus_within = None
        bus_nearest_m = None
    else:
        # Start with no nearest distance, then take a running minimum across all stops.
        bus_nearest_m: float | None = None
        # Count how many stops are within the configured walk radius.
        bus_within = 0
        # Iterate every stop because we do not have a spatial index in the MVP.
        for stop in bus_stops:
            # Convert each stop into a GeoPoint so we can reuse the distance function.
            stop_pt = CoreGeoPoint(lat=stop.lat, lon=stop.lon)
            # Compute destination -> stop distance (meters).
            distance_m = haversine_m(dest_pt, stop_pt)
            # Keep the smallest observed distance as the "nearest stop" metric.
            bus_nearest_m = distance_m if bus_nearest_m is None else min(bus_nearest_m, distance_m)
            # Treat a stop as "nearby" if it is inside the walk radius.
            if distance_m <= bus_radius_m:
                bus_within += 1
        # Use +inf as a sentinel if something goes wrong (should not happen with non-empty lists).
        bus_nearest_m = bus_nearest_m if bus_nearest_m is not None else float("inf")

    # --- YouBike metrics (station density + last-mile availability) ---
    if not bike_stations:
        # Missing YouBike ingestion -> downstream scoring will return a neutral bike score.
        bike_within = None
        bike_nearest_m = None
        bike_rent_total = None
        bike_return_total = None
    else:
        # Track nearest station distance across all stations.
        bike_nearest_m: float | None = None
        # Count stations within the radius so we can measure station density.
        bike_within = 0
        # Aggregate bike availability only for stations inside the radius (last-mile relevance).
        rent_sum = 0
        return_sum = 0
        # We track whether availability was ever present because some datasets can omit these fields.
        any_rent = False
        any_return = False

        # Iterate every station because we do not have a spatial index in the MVP.
        for station in bike_stations:
            # Convert station coordinates into a core GeoPoint for distance math.
            station_pt = CoreGeoPoint(lat=station.lat, lon=station.lon)
            # Compute destination -> station distance (meters).
            distance_m = haversine_m(dest_pt, station_pt)
            # Update nearest distance with a running minimum.
            bike_nearest_m = distance_m if bike_nearest_m is None else min(bike_nearest_m, distance_m)
            # Only stations inside the radius contribute to density and availability features.
            if distance_m <= bike_radius_m:
                bike_within += 1
                # Availability may be missing; we only sum values when present.
                if station.available_rent_bikes is not None:
                    rent_sum += int(station.available_rent_bikes)
                    any_rent = True
                # Return docks matter for the "can I park the bike?" part of the last mile.
                if station.available_return_bikes is not None:
                    return_sum += int(station.available_return_bikes)
                    any_return = True

        # Use +inf as a sentinel if something goes wrong (should not happen with non-empty lists).
        bike_nearest_m = bike_nearest_m if bike_nearest_m is not None else float("inf")
        # If no stations are within radius, treat availability as "0 nearby" rather than "missing".
        if bike_within == 0:
            bike_rent_total = 0
            bike_return_total = 0
        else:
            # If stations exist but availability fields are missing, preserve `None` as "unavailable".
            bike_rent_total = rent_sum if any_rent else None
            bike_return_total = return_sum if any_return else None

    # --- Metro metrics (density + nearest station distance) ---
    if not metro_stations:
        # Missing metro ingestion -> downstream scoring will return a neutral metro score.
        metro_within = None
        metro_nearest_m = None
    else:
        # Track nearest station distance across all stations.
        metro_nearest_m: float | None = None
        # Count stations within the walk radius so we can measure station density.
        metro_within = 0
        # Iterate every station because we do not have a spatial index in the MVP.
        for station in metro_stations:
            # Convert station coordinates into a core GeoPoint for distance math.
            station_pt = CoreGeoPoint(lat=station.lat, lon=station.lon)
            # Compute destination -> station distance (meters).
            distance_m = haversine_m(dest_pt, station_pt)
            # Update nearest distance with a running minimum.
            metro_nearest_m = distance_m if metro_nearest_m is None else min(metro_nearest_m, distance_m)
            # Treat a station as "nearby" if it is inside the configured radius.
            if distance_m <= metro_radius_m:
                metro_within += 1
        # Use +inf as a sentinel if something goes wrong (should not happen with non-empty lists).
        metro_nearest_m = metro_nearest_m if metro_nearest_m is not None else float("inf")

    # Return a single immutable bundle so the scoring layer can consume it consistently.
    return AccessibilityMetrics(
        bus_stops_within_radius=bus_within,
        bus_nearest_stop_distance_m=bus_nearest_m,
        bike_stations_within_radius=bike_within,
        bike_nearest_station_distance_m=bike_nearest_m,
        bike_available_rent_bikes_within_radius=bike_rent_total,
        bike_available_return_bikes_within_radius=bike_return_total,
        metro_stations_within_radius=metro_within,
        metro_nearest_station_distance_m=metro_nearest_m,
        origin_distance_m=origin_distance_m,
    )


def score_accessibility(metrics: AccessibilityMetrics, *, settings: Settings) -> tuple[float, dict, list[str]]:
    """
    Convert raw metrics into a normalized accessibility score in the range [0, 1].

    Scoring strategy (rule-based and explainable):
    1) Compute an "origin proximity" score: closer destinations score higher.
    2) Compute a "local transit" score by blending bus + metro + bike signals.
    3) Blend (1) and (2) using configurable weights.

    Fail-open behavior:
    - If a signal is missing (None), we use `settings.scoring.neutral_score` instead of crashing.
    """

    # Read the accessibility tuning knobs from settings (all are user-configurable via YAML).
    cfg = settings.ingestion.tdx.accessibility

    # --- 1) Origin proximity score (distance from user origin to destination) ---
    # Cap prevents extremely far destinations from dominating the scale; beyond the cap -> score 0.
    origin_cap_m = int(cfg.origin_distance_cap_m)
    if origin_cap_m <= 0:
        # Misconfiguration safety: never crash because of a bad cap; return a neutral score instead.
        origin_score = float(settings.scoring.neutral_score)
        origin_reason = "Origin distance cap misconfigured; using neutral proximity score"
    else:
        # We map distance to a 0..1 score by `1 - clamp(distance / cap)`.
        origin_score = 1 - clamp01(metrics.origin_distance_m / origin_cap_m)
        # Human-friendly reason string for the UI (kilometers are easier to read than meters).
        origin_reason = f"~{metrics.origin_distance_m/1000:.1f} km from origin"

    # --- 2a) Local transit: bus signal (stop density + nearest stop) ---
    if metrics.bus_stops_within_radius is None or metrics.bus_nearest_stop_distance_m is None:
        # Missing bus ingestion -> neutral bus score, with a clear explanation.
        bus_score = float(settings.scoring.neutral_score)
        bus_reasons = ["Bus stop data unavailable"]
        bus_details = {"available": False}
    else:
        # Normalize stop count into 0..1 by applying a cap (prevents huge counts from dominating).
        count_score = min(metrics.bus_stops_within_radius, cfg.count_cap) / max(cfg.count_cap, 1)
        # Use 0 when nearest is inf (sentinel meaning "no stop found / bad input").
        if metrics.bus_nearest_stop_distance_m == float("inf"):
            distance_score = 0.0
        else:
            # Normalize distance into 0..1 where shorter distance => higher score.
            distance_score = 1 - min(metrics.bus_nearest_stop_distance_m, cfg.distance_cap_m) / max(
                cfg.distance_cap_m, 1
            )

        # Weights are configurable to let product tune "density vs nearest distance".
        w_count = float(cfg.local_score_weights.get("count", 0.0))
        w_distance = float(cfg.local_score_weights.get("distance", 0.0))
        denom_local = w_count + w_distance
        if denom_local <= 0:
            # Misconfiguration safety: fall back to neutral when weights do not make sense.
            bus_score = float(settings.scoring.neutral_score)
            bus_reasons = ["Bus transit weights misconfigured; using neutral bus score"]
        else:
            # Weighted average, then clamp to protect against numeric issues.
            bus_score = clamp01((w_count * count_score + w_distance * distance_score) / denom_local)
            # Provide at most two UI-friendly reason strings for this signal.
            bus_reasons = [
                f"{metrics.bus_stops_within_radius} bus stops within {cfg.radius_m}m",
                (
                    "No nearby bus stop found"
                    if metrics.bus_nearest_stop_distance_m == float("inf")
                    else f"Nearest bus stop ~{int(metrics.bus_nearest_stop_distance_m)}m"
                ),
            ]
        # Details are structured so the UI can render tooltips / debug panels.
        bus_details = {
            "available": True,
            "stops_within_radius": metrics.bus_stops_within_radius,
            "nearest_stop_distance_m": metrics.bus_nearest_stop_distance_m,
            "radius_m": cfg.radius_m,
            "count_score": count_score,
            "distance_score": distance_score,
        }

    # --- 2b) Local transit: metro signal (station density + nearest station) ---
    if metrics.metro_stations_within_radius is None or metrics.metro_nearest_station_distance_m is None:
        # Missing metro ingestion -> neutral metro score, with a clear explanation.
        metro_score = float(settings.scoring.neutral_score)
        metro_reasons = ["Metro station data unavailable"]
        metro_details = {"available": False}
    else:
        # Metro has its own tuning knobs because station spacing differs from bus stops.
        metro_cfg = cfg.metro
        # Normalize station count into 0..1 by applying a cap.
        count_score = min(metrics.metro_stations_within_radius, metro_cfg.count_cap) / max(metro_cfg.count_cap, 1)
        # Use 0 when nearest is inf (sentinel meaning "no station found / bad input").
        if metrics.metro_nearest_station_distance_m == float("inf"):
            distance_score = 0.0
        else:
            # Normalize distance into 0..1 where shorter distance => higher score.
            distance_score = 1 - min(metrics.metro_nearest_station_distance_m, metro_cfg.distance_cap_m) / max(
                metro_cfg.distance_cap_m, 1
            )

        # Metro weights are configurable to tune "density vs nearest distance".
        w_count = float(metro_cfg.score_weights.get("count", 0.0))
        w_distance = float(metro_cfg.score_weights.get("distance", 0.0))
        denom_metro = w_count + w_distance
        if denom_metro <= 0:
            # Misconfiguration safety: fall back to neutral when weights do not make sense.
            metro_score = float(settings.scoring.neutral_score)
            metro_reasons = ["Metro weights misconfigured; using neutral metro score"]
        else:
            # Weighted average, then clamp to protect against numeric issues.
            metro_score = clamp01((w_count * count_score + w_distance * distance_score) / denom_metro)
            # Provide at most two UI-friendly reason strings for this signal.
            metro_reasons = [
                f"{metrics.metro_stations_within_radius} metro stations within {metro_cfg.radius_m}m",
                (
                    "No nearby metro station found"
                    if metrics.metro_nearest_station_distance_m == float("inf")
                    else f"Nearest metro station ~{int(metrics.metro_nearest_station_distance_m)}m"
                ),
            ]

        # Details are structured so the UI can render tooltips / debug panels.
        metro_details = {
            "available": True,
            "stations_within_radius": metrics.metro_stations_within_radius,
            "nearest_station_distance_m": metrics.metro_nearest_station_distance_m,
            "radius_m": metro_cfg.radius_m,
            "count_score": count_score,
            "distance_score": distance_score,
        }

    # --- 2c) Local transit: YouBike signal (station density + available bikes) ---
    if metrics.bike_stations_within_radius is None or metrics.bike_nearest_station_distance_m is None:
        # Missing bike ingestion -> neutral bike score, with a clear explanation.
        bike_score = float(settings.scoring.neutral_score)
        bike_reasons = ["Bike station data unavailable"]
        bike_details = {"available": False}
    else:
        # Bike has its own tuning knobs because it models "last-mile" convenience.
        bike_cfg = cfg.bike
        # Normalize station count into 0..1 by applying a cap.
        station_score = min(metrics.bike_stations_within_radius, bike_cfg.station_cap) / max(
            bike_cfg.station_cap, 1
        )

        # Bike availability can be missing even when stations are present (dataset gaps / parsing).
        if metrics.bike_available_rent_bikes_within_radius is None:
            availability_score = float(settings.scoring.neutral_score)
            availability_reason = "Bike availability unavailable"
        else:
            # Normalize available bikes into 0..1 by applying a cap.
            availability_score = min(
                metrics.bike_available_rent_bikes_within_radius, bike_cfg.available_bikes_cap
            ) / max(bike_cfg.available_bikes_cap, 1)
            availability_reason = f"Available bikes nearby: {metrics.bike_available_rent_bikes_within_radius}"

        # Bike weights are configurable to tune "station density vs bike availability".
        w_stations = float(bike_cfg.score_weights.get("stations", 0.0))
        w_avail = float(bike_cfg.score_weights.get("available_bikes", 0.0))
        denom_bike = w_stations + w_avail
        if denom_bike <= 0:
            # Misconfiguration safety: fall back to neutral when weights do not make sense.
            bike_score = float(settings.scoring.neutral_score)
            bike_reasons = ["Bike weights misconfigured; using neutral bike score"]
        else:
            # Weighted average, then clamp to protect against numeric issues.
            bike_score = clamp01((w_stations * station_score + w_avail * availability_score) / denom_bike)
            # Provide at most two UI-friendly reason strings for this signal.
            bike_reasons = [
                f"{metrics.bike_stations_within_radius} bike stations within {bike_cfg.radius_m}m",
                availability_reason,
            ]

        # Details are structured so the UI can render tooltips / debug panels.
        bike_details = {
            "available": True,
            "stations_within_radius": metrics.bike_stations_within_radius,
            "nearest_station_distance_m": metrics.bike_nearest_station_distance_m,
            "radius_m": bike_cfg.radius_m,
            "station_score": station_score,
            "availability_score": availability_score,
            "available_rent_bikes_within_radius": metrics.bike_available_rent_bikes_within_radius,
            "available_return_bikes_within_radius": metrics.bike_available_return_bikes_within_radius,
        }

    # --- 2d) Combine local transit signals (bus + metro + bike) ---
    # Start from the configured signal mix (product can tune "bus vs metro vs bike").
    raw_signal_weights = {
        "bus": float(cfg.local_transit_signal_weights.get("bus", 0.0)),
        "metro": float(cfg.local_transit_signal_weights.get("metro", 0.0)),
        "bike": float(cfg.local_transit_signal_weights.get("bike", 0.0)),
    }
    # If a signal is unavailable, force its weight to 0 so it cannot influence the combined score.
    if bus_details.get("available") is False:
        raw_signal_weights["bus"] = 0.0
    if metro_details.get("available") is False:
        raw_signal_weights["metro"] = 0.0
    if bike_details.get("available") is False:
        raw_signal_weights["bike"] = 0.0

    # Compute "is anything available?" before normalizing, because normalization may re-assign weights.
    any_local_available = any(d.get("available") is True for d in (bus_details, metro_details, bike_details))
    # Normalize weights to sum to 1.0 so we can use them as a convex combination.
    signal_weights = normalize_weights(raw_signal_weights)
    if not any_local_available:
        # Missing all local transit data -> neutral local transit score.
        local_transit_score = float(settings.scoring.neutral_score)
        local_reasons = ["Local transit data unavailable"]
    else:
        # Weighted blend of sub-scores, clamped for numeric stability.
        local_transit_score = clamp01(
            signal_weights["bus"] * bus_score
            + signal_weights["metro"] * metro_score
            + signal_weights["bike"] * bike_score
        )
        # Order reason groups by weight so the most important signals explain the score first.
        ordered = sorted(
            [
                ("bus", signal_weights["bus"], bus_reasons),
                ("metro", signal_weights["metro"], metro_reasons),
                ("bike", signal_weights["bike"], bike_reasons),
            ],
            key=lambda x: x[1],
            reverse=True,
        )
        # Deduplicate short reason strings so the final list is compact and readable.
        local_reasons = []
        for _, _, reasons in ordered:
            for reason in reasons[:2]:
                if reason and reason not in local_reasons:
                    local_reasons.append(reason)

    # --- 3) Blend local transit with origin proximity (final accessibility score) ---
    # These weights let the product decide whether "nearby" or "transit-rich" matters more.
    w_local = float(cfg.blend_weights.get("local_transit", 0.0))
    w_origin = float(cfg.blend_weights.get("origin_proximity", 0.0))
    denom = w_local + w_origin
    if denom <= 0:
        # Misconfiguration safety: fall back to neutral when weights do not make sense.
        score = float(settings.scoring.neutral_score)
        reasons = ["Accessibility blend weights misconfigured; using neutral score"]
    else:
        # Weighted average of the two major factors, clamped for numeric stability.
        score = clamp01((w_local * local_transit_score + w_origin * origin_score) / denom)
        # Compose a final reason list (origin first, then local transit reasons).
        reasons = [origin_reason, *local_reasons]

    # Details are returned for debugging and for a "score breakdown" UI panel.
    details = {
        # Flatten raw metrics so callers can inspect the exact inputs used.
        **asdict(metrics),
        # Include the origin cap so the UI can explain the proximity normalization.
        "origin_distance_cap_m": origin_cap_m,
        # Include the intermediate scores for transparency.
        "origin_proximity_score": origin_score,
        "local_transit_score": local_transit_score,
        # Include the normalized signal weights used in the local transit blend.
        "local_transit_signal_weights": signal_weights,
        # Include per-signal details for drill-down debugging.
        "bus": bus_details,
        "metro": metro_details,
        "bike": bike_details,
        # Include the blend weights so the UI can show how the final score was computed.
        "blend_weights": {"local_transit": w_local, "origin_proximity": w_origin},
    }
    # Return the score plus structured details and human-readable reasons.
    return score, details, reasons
