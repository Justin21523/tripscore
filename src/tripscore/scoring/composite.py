"""
Shared scoring utilities.

This module contains small, reusable helpers used across feature scorers:
- `clamp01`: keep values within 0..1 for stable UI/output
- `normalize_weights`: convert arbitrary non-negative weights into a 1.0-summing distribution
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


def clamp01(x: float) -> float:
    """Clamp a number into the [0.0, 1.0] range."""
    return max(0.0, min(1.0, float(x)))


@dataclass(frozen=True)
class ComponentResult:
    """A normalized feature score plus explainability payload."""

    score: float
    details: dict[str, Any]
    reasons: list[str]


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    """Normalize a dict of weights so they sum to 1.0 (non-negative)."""
    cleaned = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return {k: 1.0 / len(weights) for k in weights}
    return {k: v / total for k, v in cleaned.items()}


ComponentName = Literal["accessibility", "weather", "preference", "context"]
