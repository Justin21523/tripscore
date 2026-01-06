from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


@dataclass(frozen=True)
class ComponentResult:
    score: float
    details: dict[str, Any]
    reasons: list[str]


def normalize_weights(weights: dict[str, float]) -> dict[str, float]:
    cleaned = {k: max(0.0, float(v)) for k, v in weights.items()}
    total = sum(cleaned.values())
    if total <= 0:
        return {k: 1.0 / len(weights) for k in weights}
    return {k: v / total for k, v in cleaned.items()}


ComponentName = Literal["accessibility", "weather", "preference", "context"]
