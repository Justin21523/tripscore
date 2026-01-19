"""
Offline data quality report utilities.

Goal: provide a deterministic, network-free view of "is our local data complete and sane?"
Used by:
- CLI debugging
- API status endpoint for the web UI
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from tripscore.catalog.loader import load_destinations_with_details
from tripscore.config.settings import Settings
from tripscore.core.env import resolve_project_path
from tripscore.quality.tdx_coverage import build_tdx_bulk_coverage


@dataclass(frozen=True)
class Issue:
    severity: str  # "info" | "warning" | "error"
    code: str
    message: str
    count: int = 1
    sample: list[str] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity,
            "code": self.code,
            "message": self.message,
            "count": int(self.count),
            "sample": list(self.sample or []),
        }


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def catalog_issues(settings: Settings) -> list[Issue]:
    issues: list[Issue] = []
    catalog_path = resolve_project_path(settings.catalog.path)
    details_path = resolve_project_path(settings.catalog.details_path) if settings.catalog.details_path else None

    try:
        destinations = load_destinations_with_details(catalog_path=catalog_path, details_path=details_path)
    except Exception as e:
        return [Issue(severity="error", code="CATALOG_LOAD_FAILED", message=str(e))]

    ids = [d.id for d in destinations]
    dup = {i for i in ids if ids.count(i) > 1}
    if dup:
        issues.append(
            Issue(
                severity="error",
                code="CATALOG_DUPLICATE_ID",
                message="Duplicate destination ids in catalog.",
                count=len(dup),
                sample=sorted(list(dup))[:8],
            )
        )

    missing_city = [d.id for d in destinations if not d.city]
    if missing_city:
        issues.append(
            Issue(
                severity="warning",
                code="CATALOG_MISSING_CITY",
                message="Some destinations are missing `city`.",
                count=len(missing_city),
                sample=missing_city[:8],
            )
        )

    bad_tags = []
    for d in destinations:
        for t in d.tags:
            if t != t.lower():
                bad_tags.append(f"{d.id}:{t}")
    if bad_tags:
        issues.append(
            Issue(
                severity="warning",
                code="CATALOG_TAG_NOT_LOWER",
                message="Some catalog tags are not lower-case.",
                count=len(bad_tags),
                sample=bad_tags[:8],
            )
        )

    out_of_range = [d.id for d in destinations if not (-90 <= d.location.lat <= 90 and -180 <= d.location.lon <= 180)]
    if out_of_range:
        issues.append(
            Issue(
                severity="error",
                code="CATALOG_BAD_COORDS",
                message="Some destinations have out-of-range coordinates.",
                count=len(out_of_range),
                sample=out_of_range[:8],
            )
        )

    return issues


def tdx_bulk_issues(settings: Settings) -> list[Issue]:
    issues: list[Issue] = []
    cache_dir = resolve_project_path(settings.cache.dir)
    base = cache_dir / "tdx_bulk"
    if not base.exists():
        issues.append(Issue(severity="info", code="TDX_BULK_MISSING", message="No tdx_bulk directory found."))
        return issues

    progress_files = list(base.rglob("*.progress.json"))
    if not progress_files:
        issues.append(Issue(severity="info", code="TDX_BULK_EMPTY", message="No bulk progress files found."))
        return issues

    error_files = []
    unsupported_files = []
    incomplete = []
    for p in progress_files:
        payload = _read_json(p) or {}
        done = bool(payload.get("done", False))
        status = payload.get("error_status")
        unsupported = bool(payload.get("unsupported", False)) or status == 404
        if status:
            if unsupported:
                unsupported_files.append(f"{p.relative_to(base)}:{status}")
            else:
                error_files.append(f"{p.relative_to(base)}:{status}")
        if not done:
            incomplete.append(str(p.relative_to(base)))

    if unsupported_files:
        issues.append(
            Issue(
                severity="info",
                code="TDX_BULK_UNSUPPORTED",
                message="Some TDX datasets appear unsupported (HTTP 404).",
                count=len(unsupported_files),
                sample=unsupported_files[:8],
            )
        )
    if error_files:
        issues.append(
            Issue(
                severity="warning",
                code="TDX_BULK_ERRORS",
                message="Some bulk datasets reported an HTTP error status.",
                count=len(error_files),
                sample=error_files[:8],
            )
        )
    if incomplete:
        issues.append(
            Issue(
                severity="info",
                code="TDX_BULK_INCOMPLETE",
                message="Some bulk datasets are not done yet (safe to resume).",
                count=len(incomplete),
                sample=incomplete[:8],
            )
        )

    return issues


def build_quality_report(settings: Settings) -> dict[str, Any]:
    catalog_path = resolve_project_path(settings.catalog.path)
    cache_dir = resolve_project_path(settings.cache.dir)
    base = cache_dir / "tdx_bulk"

    c_issues = catalog_issues(settings)
    t_issues = tdx_bulk_issues(settings)
    issues = [*c_issues, *t_issues]

    severity_rank = {"error": 3, "warning": 2, "info": 1}
    worst = "info"
    for i in issues:
        if severity_rank.get(i.severity, 0) > severity_rank.get(worst, 0):
            worst = i.severity

    return {
        "overall": {"severity": worst, "issue_count": len(issues)},
        "paths": {
            "catalog_path": str(catalog_path),
            "catalog_details_path": str(settings.catalog.details_path) if settings.catalog.details_path else None,
            "cache_dir": str(cache_dir),
            "tdx_bulk_dir": str(base),
        },
        "tdx": {"bulk_coverage": build_tdx_bulk_coverage(settings)},
        "issues": [i.as_dict() for i in issues],
    }
