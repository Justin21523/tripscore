"""
Per-request ingestion metadata capture.

This module provides a contextvar-backed recorder used by ingestion clients to report:
- source mode: live/cache/bulk/stale/none
- as_of timestamps
- optional details (scope, TTL, error codes)

The API layer can then attach this to `RecommendationResult.meta` for transparency.
"""

from __future__ import annotations

import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any


@dataclass
class IngestionMeta:
    sources: dict[str, dict[str, Any]] = field(default_factory=dict)

    def record(self, name: str, payload: dict[str, Any]) -> None:
        if not name:
            return
        self.sources[name] = dict(payload)


_ingestion_meta_var: contextvars.ContextVar[IngestionMeta | None] = contextvars.ContextVar(
    "tripscore_ingestion_meta", default=None
)


def record_ingestion_source(name: str, payload: dict[str, Any]) -> None:
    meta = _ingestion_meta_var.get()
    if not meta:
        return
    meta.record(name, payload)


@contextmanager
def capture_ingestion_meta() -> IngestionMeta:
    meta = IngestionMeta()
    token = _ingestion_meta_var.set(meta)
    try:
        yield meta
    finally:
        _ingestion_meta_var.reset(token)

