from __future__ import annotations

from dataclasses import asdict, dataclass

from tripscore.config.settings import Settings
from tripscore.core.geo import GeoPoint as CoreGeoPoint
from tripscore.core.geo import haversine_m
from tripscore.domain.models import Destination
from tripscore.ingestion.tdx_client import ParkingLotStatus
from tripscore.scoring.composite import clamp01, normalize_weights


@dataclass(frozen=True)
class ParkingMetrics:
    lots_within_radius: int
    nearest_lot_distance_m: float
    available_spaces_within_radius: int | None
    total_spaces_within_radius: int | None
    radius_m: int


def compute_parking_metrics(
    destination: Destination, *, lots: list[ParkingLotStatus], radius_m: int
) -> ParkingMetrics:
    dest_pt = CoreGeoPoint(lat=destination.location.lat, lon=destination.location.lon)

    nearest: float | None = None
    lot_count = 0

    available_total = 0
    total_total = 0
    any_available = False
    any_total = False

    for lot in lots:
        lot_pt = CoreGeoPoint(lat=lot.lat, lon=lot.lon)
        d = haversine_m(dest_pt, lot_pt)
        nearest = d if nearest is None else min(nearest, d)
        if d <= radius_m:
            lot_count += 1
            if lot.available_spaces is not None:
                available_total += int(lot.available_spaces)
                any_available = True
            if lot.total_spaces is not None:
                total_total += int(lot.total_spaces)
                any_total = True

    return ParkingMetrics(
        lots_within_radius=lot_count,
        nearest_lot_distance_m=nearest if nearest is not None else float("inf"),
        available_spaces_within_radius=available_total if any_available else None,
        total_spaces_within_radius=total_total if any_total else None,
        radius_m=radius_m,
    )


def score_parking_availability(metrics: ParkingMetrics, *, settings: Settings) -> tuple[float, dict, list[str]]:
    cfg = settings.features.parking

    lot_score = min(metrics.lots_within_radius, cfg.lot_cap) / max(cfg.lot_cap, 1)

    if metrics.available_spaces_within_radius is None:
        available_score = None
    else:
        available_score = min(metrics.available_spaces_within_radius, cfg.available_spaces_cap) / max(
            cfg.available_spaces_cap, 1
        )

    weights = dict(cfg.score_weights)
    if available_score is None:
        weights["available_spaces"] = 0.0

    weights = normalize_weights(weights)
    score = weights["lots"] * lot_score + weights["available_spaces"] * (
        available_score if available_score is not None else 0.0
    )
    score = clamp01(score)

    reasons = [
        f"{metrics.lots_within_radius} parking lots within {metrics.radius_m}m",
    ]
    if metrics.available_spaces_within_radius is not None:
        reasons.append(f"Available spaces nearby: {metrics.available_spaces_within_radius}")
    else:
        reasons.append("Parking availability unavailable")

    if metrics.nearest_lot_distance_m != float("inf"):
        reasons.append(f"Nearest parking lot ~{int(metrics.nearest_lot_distance_m)}m")

    details = {
        **asdict(metrics),
        "lot_score": lot_score,
        "available_score": available_score,
        "weights": weights,
    }
    return score, details, reasons

