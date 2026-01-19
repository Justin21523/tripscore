"""
Destination catalog loader.

The catalog is a local JSON file (default: `data/catalogs/destinations.json`) that
contains POIs with coordinates and tags. We validate it into typed Pydantic models
so downstream feature/scoring code can assume a consistent shape.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import TypeAdapter

from tripscore.core.env import resolve_project_path
from tripscore.domain.models import Destination


_DESTINATIONS_ADAPTER = TypeAdapter(list[Destination])


def load_destinations(path: str | Path) -> list[Destination]:
    """Load and validate a destination catalog JSON file."""
    resolved = resolve_project_path(path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    return _DESTINATIONS_ADAPTER.validate_python(payload)


def load_destination_details(path: str | Path) -> dict[str, dict]:
    """Load an optional POI details file mapping destination_id -> details dict."""
    resolved = resolve_project_path(path)
    payload = json.loads(resolved.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}
    out: dict[str, dict] = {}
    for k, v in payload.items():
        if not isinstance(k, str) or not k.strip():
            continue
        if isinstance(v, dict):
            out[k] = v
    return out


def load_destinations_with_details(*, catalog_path: str | Path, details_path: str | Path | None) -> list[Destination]:
    """Load destinations and best-effort merge extra fields from a details file."""
    destinations = load_destinations(catalog_path)
    if not details_path:
        return destinations
    try:
        details = load_destination_details(details_path)
    except Exception:
        return destinations

    if not details:
        return destinations

    merged: list[Destination] = []
    for d in destinations:
        extra = details.get(d.id) or {}
        if not extra:
            merged.append(d)
            continue
        updates = {}
        for k in ["address", "phone", "opening_hours", "description", "url", "city", "district"]:
            v = extra.get(k)
            if isinstance(v, str) and v.strip():
                updates[k] = v.strip()
        merged.append(d.model_copy(update=updates) if updates else d)
    return merged
