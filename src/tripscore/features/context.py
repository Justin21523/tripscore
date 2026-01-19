"""
Context feature (destination-level).

This module estimates "context suitability" as a blend of:
- crowd risk (time-window heuristics + optional parking-derived congestion proxy)
- family-friendliness (district baseline + optional `family_friendly` tag bonus)

It is intentionally heuristic and explainable, with all knobs living in config.
"""

from __future__ import annotations

import json
from datetime import datetime
from functools import lru_cache

from pydantic import BaseModel, Field, TypeAdapter

from tripscore.config.settings import Settings
from tripscore.core.env import resolve_project_path
from tripscore.domain.models import Destination, UserPreferences
from tripscore.scoring.composite import clamp01, normalize_weights


class DistrictFactor(BaseModel):
    city: str
    district: str
    crowd_risk_base: float = Field(0.5, ge=0, le=1)
    family_friendliness_base: float = Field(0.5, ge=0, le=1)


_DISTRICT_FACTORS_ADAPTER = TypeAdapter(list[DistrictFactor])


def _overlaps_hours(start_hour: int, end_hour: int, window_start: int, window_end: int) -> bool:
    # Treat [start, end) as hours in local time, end can be 24.
    if end_hour < start_hour:
        # Overnight window; treat as overlapping if either segment overlaps.
        return _overlaps_hours(start_hour, 24, window_start, window_end) or _overlaps_hours(
            0, end_hour, window_start, window_end
        )
    return max(start_hour, window_start) < min(end_hour, window_end)


def _time_window_multiplier(
    start: datetime, end: datetime, *, settings: Settings, tags: list[str]
) -> tuple[float, float]:
    cfg = settings.features.context.crowd

    multiplier = 1.0
    weekday = start.weekday()  # 0=Mon ... 5=Sat 6=Sun
    if weekday >= 5:
        multiplier *= float(cfg.weekend_multiplier)

    start_h = int(start.hour)
    end_h = int(end.hour)
    if end.minute or end.second:
        end_h = min(24, end_h + 1)

    peak_multiplier = 1.0
    for w in cfg.peak_hours:
        if _overlaps_hours(start_h, end_h, int(w.start_hour), int(w.end_hour)):
            peak_multiplier = max(peak_multiplier, float(w.multiplier))
    multiplier *= peak_multiplier

    tag_adj = 0.0
    adjustments = cfg.tag_risk_adjustments or {}
    for t in tags:
        tag_adj += float(adjustments.get(t, 0.0))

    # Positive adjustments should increase risk; negative should decrease.
    return multiplier, tag_adj


@lru_cache
def _load_district_factors(path: str) -> dict[tuple[str, str], DistrictFactor]:
    resolved = resolve_project_path(path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    factors = _DISTRICT_FACTORS_ADAPTER.validate_python(payload)
    out: dict[tuple[str, str], DistrictFactor] = {}
    for f in factors:
        out[(f.city.strip().lower(), f.district.strip().lower())] = f
    return out


def score_context(
    destination: Destination,
    *,
    preferences: UserPreferences,
    settings: Settings,
    parking_availability_score: float | None = None,
    parking_details: dict | None = None,
) -> tuple[float, dict, list[str]]:
    cfg = settings.features.context
    factors = _load_district_factors(cfg.district_factors_path)

    city = (destination.city or "").strip().lower()
    district = (destination.district or "").strip().lower()
    factor = factors.get((city, district))

    base_risk = float(factor.crowd_risk_base) if factor else float(cfg.crowd.default_risk)
    multiplier, tag_adj = _time_window_multiplier(
        preferences.time_window.start, preferences.time_window.end, settings=settings, tags=destination.tags
    )
    baseline_risk = clamp01(base_risk * float(multiplier) + float(tag_adj))

    if parking_availability_score is None:
        predicted_risk = baseline_risk
        parking_risk = None
    else:
        parking_risk = clamp01(1.0 - clamp01(float(parking_availability_score)))
        w_parking = clamp01(float(cfg.crowd.parking_risk_weight))
        w_baseline = 1.0 - w_parking
        predicted_risk = clamp01(w_baseline * baseline_risk + w_parking * parking_risk)

    crowd_score = clamp01(1.0 - predicted_risk)

    base_family = float(factor.family_friendliness_base) if factor else float(cfg.family.default_score)
    family_bonus = float(cfg.family.tag_bonus) if "family_friendly" in destination.tags else 0.0
    family_score = clamp01(base_family + family_bonus)

    w_crowd = (
        float(preferences.avoid_crowds_importance)
        if preferences.avoid_crowds_importance is not None
        else float(cfg.default_avoid_crowds_importance)
    )
    w_family = (
        float(preferences.family_friendly_importance)
        if preferences.family_friendly_importance is not None
        else float(cfg.default_family_friendly_importance)
    )
    internal_weights = normalize_weights({"crowd": w_crowd, "family": w_family})

    score = clamp01(internal_weights["crowd"] * crowd_score + internal_weights["family"] * family_score)

    if predicted_risk < 0.33:
        crowd_label = "low"
    elif predicted_risk < 0.66:
        crowd_label = "moderate"
    else:
        crowd_label = "high"

    reasons: list[str] = [
        f"Predicted crowd risk {crowd_label} ({predicted_risk:.2f})",
    ]
    if parking_availability_score is not None:
        if parking_risk is not None and parking_risk < 0.33:
            reasons.append("Parking availability suggests low congestion")
        elif parking_risk is not None and parking_risk > 0.66:
            reasons.append("Parking availability suggests high congestion")
        else:
            reasons.append("Parking availability suggests moderate congestion")
    if "family_friendly" in destination.tags:
        reasons.append("Family-friendly destination")

    details = {
        "city": destination.city,
        "district": destination.district,
        "base_crowd_risk": base_risk,
        "time_multiplier": float(multiplier),
        "tag_risk_adjustment": float(tag_adj),
        "baseline_crowd_risk": baseline_risk,
        "parking_availability_score": parking_availability_score,
        "parking_crowd_risk": parking_risk,
        "parking_risk_weight": float(cfg.crowd.parking_risk_weight),
        "predicted_crowd_risk": predicted_risk,
        "crowd_suitability_score": crowd_score,
        "base_family_score": base_family,
        "family_tag_bonus": family_bonus,
        "family_friendliness_score": family_score,
        "internal_weights": internal_weights,
    }
    if parking_details is not None:
        details["parking_details"] = parking_details
    return score, details, reasons
