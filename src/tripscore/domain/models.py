"""
Domain models (Pydantic).

These types represent the stable "contract" between layers:
- API/CLI inputs (`UserPreferences`)
- catalog entities (`Destination`)
- explainable scoring output (`RecommendationResult`)

Keeping these models in one place helps:
- validation (reject bad inputs early),
- typed refactors,
- consistent JSON output across CLI/API/Web.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


class GeoPoint(BaseModel):
    """A geographic point in decimal degrees."""

    lat: float = Field(..., ge=-90, le=90)
    lon: float = Field(..., ge=-180, le=180)


class TimeWindow(BaseModel):
    """A start/end time interval for the recommendation query."""

    start: datetime
    end: datetime

    @model_validator(mode="after")
    def _validate_order(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError("time_window.end must be after time_window.start")
        return self


class ComponentWeights(BaseModel):
    """Optional overrides for composite component weights (each 0..1)."""

    accessibility: float | None = Field(default=None, ge=0, le=1)
    weather: float | None = Field(default=None, ge=0, le=1)
    preference: float | None = Field(default=None, ge=0, le=1)
    context: float | None = Field(default=None, ge=0, le=1)


class UserPreferences(BaseModel):
    """End-user request payload for a recommendation run."""

    origin: GeoPoint
    time_window: TimeWindow

    preset: str | None = None
    max_results: int | None = Field(default=None, ge=1, le=50)

    component_weights: ComponentWeights | None = None
    weather_rain_importance: float | None = Field(default=None, ge=0, le=1)
    avoid_crowds_importance: float | None = Field(default=None, ge=0, le=1)
    family_friendly_importance: float | None = Field(default=None, ge=0, le=1)

    tag_weights: dict[str, float] | None = None
    required_tags: list[str] = Field(default_factory=list)
    excluded_tags: list[str] = Field(default_factory=list)
    settings_overrides: dict[str, Any] | None = None


class Destination(BaseModel):
    """A place/POI candidate to score and rank."""

    id: str
    name: str
    location: GeoPoint
    tags: list[str] = Field(default_factory=list)

    city: str | None = None
    district: str | None = None
    url: str | None = None
    description: str | None = None
    address: str | None = None
    phone: str | None = None
    opening_hours: str | None = None

    @field_validator("tags")
    @classmethod
    def _normalize_tags(cls, tags: list[str]) -> list[str]:
        return sorted({t.strip().lower() for t in tags if t and t.strip()})


class ScoreComponent(BaseModel):
    """One explainable component score (accessibility/weather/preference/context)."""

    name: Literal["accessibility", "weather", "preference", "context"]
    score: float = Field(..., ge=0, le=1)
    weight: float = Field(..., ge=0, le=1)
    contribution: float = Field(..., ge=0, le=1)
    details: dict[str, Any] = Field(default_factory=dict)
    reasons: list[str] = Field(default_factory=list)


class ScoreBreakdown(BaseModel):
    """Explainable breakdown for a destination, including per-component details."""

    destination_id: str
    destination_name: str
    total_score: float = Field(..., ge=0, le=1)
    components: list[ScoreComponent]


class RecommendationItem(BaseModel):
    """One ranked output item: destination + its score breakdown."""

    destination: Destination
    breakdown: ScoreBreakdown
    meta: dict[str, Any] = Field(default_factory=dict)


class RecommendationResult(BaseModel):
    """Top-N recommendations plus the original query."""

    generated_at: datetime
    query: UserPreferences
    results: list[RecommendationItem]
    meta: dict[str, Any] = Field(default_factory=dict)
