"""
Time parsing and timezone normalization.

TripScore treats all input/output timestamps as timezone-aware datetimes to avoid
subtle bugs when mixing naive and aware datetimes (especially across API/CLI/UI).
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def ensure_tz(dt: datetime, timezone: str) -> datetime:
    """Ensure `dt` has tzinfo; attach `timezone` if naive."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(timezone))
    return dt


def parse_datetime(value: str, timezone: str) -> datetime:
    """Parse ISO-8601 datetime string and ensure tzinfo is present.

    Notes:
    - Accepts a trailing `Z` (UTC) and converts it to `+00:00` for `fromisoformat`.
    - If the parsed value is naive, the provided `timezone` is attached.
    """
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    return ensure_tz(dt, timezone)
