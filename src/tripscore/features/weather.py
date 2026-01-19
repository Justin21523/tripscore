# src/tripscore/features/weather.py
"""
Weather feature (destination-level).

This module converts a weather summary (ingestion output) into an explainable 0..1 score.

Why a separate `features/weather.py` layer?
- Ingestion returns *raw-ish* values (rain probability, temperature) that can be missing.
- Features define *product rules* for how those values translate into a stable score:
  - Missing data -> neutral score (fail-open) so we can still recommend places.
  - "Comfort temperature" window -> full score in-range, linear penalty outside.
  - Indoor/outdoor tags can change how much rain matters (a simple preference proxy).
"""

from __future__ import annotations

# `Settings` carries config-defined weights, thresholds, and constants (no hard-coded tuning).
from tripscore.config.settings import Settings
# Destination tags influence how we interpret rain impact (indoor vs outdoor).
from tripscore.domain.models import Destination, UserPreferences
# WeatherSummary is the ingestion-layer DTO (may contain None for missing fields).
from tripscore.ingestion.weather_client import WeatherSummary
# Clamp protects downstream UI/scorers from invalid ranges when inputs are weird.
from tripscore.scoring.composite import clamp01


def score_weather(
    summary: WeatherSummary, *, destination: Destination, preferences: UserPreferences, settings: Settings
) -> tuple[float, dict, list[str]]:
    # Read config for weather scoring: comfort ranges, penalties, and default weights.
    cfg = settings.ingestion.weather

    # --- Step 1) Convert precipitation probability into a 0..1 "rain comfort" score ---
    # We treat higher rain probability as worse (lower score).
    # Note: Open-Meteo precipitation_probability is 0..100 (%), but can be missing -> None.
    if summary.max_precipitation_probability is None:
        # Fail open: when rain data is missing, return a neutral signal instead of crashing.
        rain_score = float(settings.scoring.neutral_score)
    else:
        # Map 0% -> 1.0 and 100% -> 0.0 using a simple linear transform.
        rain_score = 1 - clamp01(float(summary.max_precipitation_probability) / 100.0)

    # --- Step 2) Convert temperature into a 0..1 "temperature comfort" score ---
    # We give a full score inside the comfort window [min, max], and apply a linear penalty outside.
    if summary.mean_temperature_c is None:
        # Fail open: when temperature is missing, return a neutral signal instead of crashing.
        temp_score = float(settings.scoring.neutral_score)
    else:
        # Cast to float early so downstream math is predictable (Pydantic may store as Decimal-like).
        t = float(summary.mean_temperature_c)
        # Comfort thresholds are product tuning knobs in config (not hard-coded).
        t_min = float(cfg.comfort_temperature_c.min)
        t_max = float(cfg.comfort_temperature_c.max)
        if t_min <= t <= t_max:
            # Inside the comfort window: treat as perfect temperature.
            temp_score = 1.0
        else:
            # Outside the comfort window: penalize by the absolute distance to the nearest bound.
            distance = (t_min - t) if t < t_min else (t - t_max)
            # Scale controls how quickly the score drops as temperature deviates from comfort.
            # We guard the denominator to avoid division by zero if misconfigured.
            temp_score = 1 - clamp01(distance / max(float(cfg.temperature_penalty_scale_c), 0.1))

    # --- Step 3) Choose component weights (rain vs temperature) ---
    # Users can override the default mix with `weather_rain_importance` (0..1).
    if preferences.weather_rain_importance is not None:
        # When the user sets rain importance, we treat temperature as "the rest" (1 - rain).
        w_rain_base = float(preferences.weather_rain_importance)
        w_temp_base = 1.0 - w_rain_base
    else:
        # Otherwise we use the config defaults (may be tuned per product).
        w_rain_base = float(cfg.score_weights.rain)
        w_temp_base = float(cfg.score_weights.temperature)

    # --- Step 4) Adjust rain importance based on destination tags (indoor/outdoor proxy) ---
    # This is a simple heuristic: rain matters less for purely indoor places and more for outdoor.
    multiplier = 1.0
    multipliers = settings.features.weather.rain_importance_multiplier
    # Tag checks are case-sensitive in our catalog, so we standardize tags as lower-case elsewhere.
    is_indoor = "indoor" in destination.tags
    is_outdoor = "outdoor" in destination.tags
    if is_indoor and not is_outdoor:
        # Pure indoor -> reduce rain impact (e.g., museums are less sensitive to rain).
        multiplier = float(multipliers.get("indoor", 1.0))
    elif is_outdoor and not is_indoor:
        # Pure outdoor -> increase rain impact (e.g., parks are more sensitive to rain).
        multiplier = float(multipliers.get("outdoor", 1.0))

    # Apply the multiplier only to the rain weight (temperature weight stays unchanged).
    w_rain = w_rain_base * multiplier
    w_temp = w_temp_base

    # --- Step 5) Combine rain + temperature into a single normalized score ---
    # We renormalize by dividing by (w_rain + w_temp) so absolute weight scales do not matter.
    denom = w_rain + w_temp
    if denom <= 0:
        # Misconfiguration safety: if weights are broken, return a neutral score with explanation.
        score = float(settings.scoring.neutral_score)
        reasons = ["Weather weights misconfigured; using neutral score"]
        details = {
            # Preserve raw fields so callers can see what was missing.
            "max_precipitation_probability": summary.max_precipitation_probability,
            "mean_temperature_c": summary.mean_temperature_c,
            # Include intermediate scores to support debugging and UI breakdowns.
            "rain_score": rain_score,
            "temperature_score": temp_score,
        }
        return score, details, reasons

    # Weighted average of the two sub-scores (each already in 0..1).
    score = (w_rain * rain_score + w_temp * temp_score) / denom
    # Clamp protects against subtle floating-point drift and misbehaving upstream data.
    score = clamp01(score)

    # --- Step 6) Build human-readable reasons for explainability ---
    reasons: list[str] = []
    if summary.max_precipitation_probability is not None:
        # Use integer percent to keep CLI/UI text compact.
        reasons.append(f"Max rain probability {int(summary.max_precipitation_probability)}%")
    else:
        # Missing rain still produces an output; we must be transparent about it.
        reasons.append("Rain probability unavailable")

    if summary.mean_temperature_c is not None:
        # Show one decimal place; temperature often changes slowly and does not need full precision.
        reasons.append(f"Avg temperature {summary.mean_temperature_c:.1f}°C")
    else:
        # Missing temperature still produces an output; we must be transparent about it.
        reasons.append("Temperature unavailable")

    if multiplier != 1.0:
        # Explain why rain weight differs from the base weight.
        reasons.append("Rain impact adjusted for indoor/outdoor")

    # --- Step 7) Return structured details for debugging/UI panels ---
    details = {
        # Raw ingestion values (may be None).
        "max_precipitation_probability": summary.max_precipitation_probability,
        "mean_temperature_c": summary.mean_temperature_c,
        # Intermediate normalized sub-scores.
        "rain_score": rain_score,
        "temperature_score": temp_score,
        # Expose the multiplier so the UI can show "indoor/outdoor adjustment".
        "rain_importance_multiplier": multiplier,
        # Expose final effective weights so the score can be recomputed or audited.
        "weights": {"rain": w_rain, "temperature": w_temp},
    }
    # Return the normalized score plus structured details and human-readable reasons.
    return score, details, reasons
