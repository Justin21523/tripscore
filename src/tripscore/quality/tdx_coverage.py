"""
TDX bulk coverage summary (offline).

This is intentionally network-free and only inspects local `tdx_bulk/*.progress.json` files.
It is designed to answer:
- which datasets are done vs incomplete,
- which errors are due to unsupported datasets (404),
- where we are hitting rate limits (429),
- where progress files are missing entirely.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from tripscore.config.settings import Settings
from tripscore.core.env import resolve_project_path
from tripscore.ingestion.tdx_cities import ALL_CITIES


DatasetName = Literal[
    "bus_stops",
    "bus_routes",
    "bike_stations",
    "parking_lots",
    "metro_stations",
]


@dataclass(frozen=True)
class CoverageRow:
    dataset: str
    scope: str
    done: bool
    missing: bool
    unsupported: bool
    error_status: int | None
    updated_at_unix: int | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset": self.dataset,
            "scope": self.scope,
            "done": bool(self.done),
            "missing": bool(self.missing),
            "unsupported": bool(self.unsupported),
            "error_status": self.error_status,
            "updated_at_unix": self.updated_at_unix,
        }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _progress_path(base: Path, dataset: str, scope: str) -> Path:
    return base / dataset / f"{scope}.progress.json"


def _row_for_progress(base: Path, *, dataset: str, scope: str) -> CoverageRow:
    p = _progress_path(base, dataset, scope)
    if not p.exists():
        return CoverageRow(
            dataset=dataset,
            scope=scope,
            done=False,
            missing=True,
            unsupported=False,
            error_status=None,
            updated_at_unix=None,
        )
    payload = _read_json(p) or {}
    error_status = payload.get("error_status")
    error_status_i = int(error_status) if isinstance(error_status, (int, float)) else None
    unsupported = bool(payload.get("unsupported", False)) or error_status_i == 404
    updated_at = payload.get("updated_at_unix")
    updated_at_i = int(updated_at) if isinstance(updated_at, (int, float)) else None
    return CoverageRow(
        dataset=dataset,
        scope=scope,
        done=bool(payload.get("done", False)),
        missing=False,
        unsupported=unsupported,
        error_status=error_status_i,
        updated_at_unix=updated_at_i,
    )


def build_tdx_bulk_coverage(settings: Settings) -> dict[str, Any]:
    cache_dir = resolve_project_path(settings.cache.dir)
    base = cache_dir / "tdx_bulk"

    datasets: list[str] = ["bus_stops", "bus_routes", "bike_stations", "parking_lots"]
    operators = list(settings.ingestion.tdx.metro_stations.operators)

    rows: list[CoverageRow] = []
    for city in ALL_CITIES:
        for ds in datasets:
            rows.append(_row_for_progress(base, dataset=ds, scope=f"city_{city}"))
    for op in operators:
        rows.append(_row_for_progress(base, dataset="metro_stations", scope=f"operator_{op}"))

    # Aggregates
    by_dataset: dict[str, dict[str, int]] = {}
    by_city: dict[str, dict[str, int]] = {}

    def bump(d: dict[str, int], k: str) -> None:
        d[k] = int(d.get(k, 0)) + 1

    def classify(r: CoverageRow) -> str:
        if r.missing:
            return "missing"
        if r.unsupported:
            return "unsupported"
        if r.error_status == 429:
            return "error_429"
        if r.error_status is not None:
            return "error_other"
        if r.done:
            return "done"
        return "incomplete"

    for r in rows:
        cls = classify(r)
        bump(by_dataset.setdefault(r.dataset, {}), cls)
        if r.scope.startswith("city_"):
            city = r.scope.removeprefix("city_")
            bump(by_city.setdefault(city, {}), cls)

    # Useful samples for UI
    incomplete = [r for r in rows if classify(r) == "incomplete"]
    rate_limited = [r for r in rows if classify(r) == "error_429"]
    other_errors = [r for r in rows if classify(r) == "error_other"]
    missing = [r for r in rows if classify(r) == "missing"]

    last_updated = [r.updated_at_unix for r in rows if r.updated_at_unix]
    last_updated_at_unix = max(last_updated) if last_updated else None

    return {
        "last_updated_at_unix": last_updated_at_unix,
        "expected": {
            "cities": list(ALL_CITIES),
            "datasets": list(datasets),
            "metro_operators": list(operators),
        },
        "summary": {
            "by_dataset": {k: dict(v) for k, v in sorted(by_dataset.items())},
            "by_city": {k: dict(v) for k, v in sorted(by_city.items())},
            "kpi": {
                "total_rows": len(rows),
                "done_rows": sum(1 for r in rows if classify(r) == "done"),
                "unsupported_rows": sum(1 for r in rows if classify(r) == "unsupported"),
                "incomplete_rows": sum(1 for r in rows if classify(r) == "incomplete"),
                "missing_rows": sum(1 for r in rows if classify(r) == "missing"),
                "error_429_rows": sum(1 for r in rows if classify(r) == "error_429"),
                "error_other_rows": sum(1 for r in rows if classify(r) == "error_other"),
            },
        },
        "samples": {
            "incomplete": [r.as_dict() for r in incomplete[:30]],
            "error_429": [r.as_dict() for r in rate_limited[:30]],
            "error_other": [r.as_dict() for r in other_errors[:30]],
            "missing": [r.as_dict() for r in missing[:30]],
        },
        "rows": [r.as_dict() for r in rows],
    }
