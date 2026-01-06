from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo


def ensure_tz(dt: datetime, timezone: str) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ZoneInfo(timezone))
    return dt


def parse_datetime(value: str, timezone: str) -> datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    return ensure_tz(dt, timezone)
