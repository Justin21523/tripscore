# src/tripscore/features/preference_match.py
"""
Preference match feature (destination-level).

This module implements a simple, explainable "tag match" score:
- The user (or preset) assigns weights to tags like `indoor`, `food`, `culture`, etc.
- A destination earns points for each tag it has that appears in the positive-weight set.
- The final score is normalized by the sum of all positive tag weights (so it is always 0..1).

Important scope note:
- Required/excluded tags are handled earlier in the recommender (candidate filtering).
  This scorer only measures "how well does this destination match my weighted preferences?"
"""

from __future__ import annotations

# Settings provides default tag weights (config-driven; no tuning constants in code).
from tripscore.config.settings import Settings
# Destination carries the catalog tags; preferences carry optional user-supplied tag weights.
from tripscore.domain.models import Destination, UserPreferences
# Clamp keeps the score stable even if inputs/weights are weird.
from tripscore.scoring.composite import clamp01


def score_preference_match(
    destination: Destination, *, preferences: UserPreferences, settings: Settings
) -> tuple[float, dict, list[str]]:
    # Choose which tag weights to use:
    # - If the user (or preset normalization step) provides tag_weights, prefer those.
    # - Otherwise fall back to config defaults so the system has a reasonable baseline.
    #
    # Note: The `or` chain means an empty dict is treated as "no override" (falls back to defaults).
    tag_weights = (preferences.tag_weights or settings.features.preference_match.tag_weights_default) or {}

    # We only treat *positive* weights as "things the user wants more of".
    # Negative weights could be interpreted as "avoid", but in this MVP we do avoidance via tag filters.
    positive_weights = {k: float(v) for k, v in tag_weights.items() if float(v) > 0}
    # The normalization denominator is "the maximum score possible" if a destination matched everything.
    max_score = sum(positive_weights.values())

    # A destination "matches" a tag if that tag exists on the destination and has a positive weight.
    matched = [t for t in destination.tags if t in positive_weights]
    # Sum the weights for matched tags to get the raw (unnormalized) preference score.
    matched_score = sum(positive_weights[t] for t in matched)

    if max_score <= 0:
        # Misconfiguration / empty weights: fail open to neutral so recommendations still work.
        score = float(settings.scoring.neutral_score)
    else:
        # Normalize to 0..1 so this feature is comparable to other feature scores.
        score = matched_score / max_score
    # Clamp for numeric stability (e.g., floating-point rounding or unexpected weight shapes).
    score = clamp01(score)

    # Build a short human-readable explanation for list views.
    reasons: list[str] = []
    if matched:
        # Limit matches to keep the reason string compact in CLI and UI.
        reasons.append("Matches: " + ", ".join(matched[:6]))
    else:
        # Provide a clear "no match" message rather than an empty reason list.
        reasons.append("No strong tag match")

    # Return structured details for debugging and for UI panels (e.g., show which tags contributed).
    details = {
        "matched_tags": matched,
        "tag_weights_used": positive_weights,
    }
    # Return the normalized score plus structured details and human-readable reasons.
    return score, details, reasons
