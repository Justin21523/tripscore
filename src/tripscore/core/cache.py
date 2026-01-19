from __future__ import annotations

import contextvars
import json
import time
from contextlib import contextmanager
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable

"""
Simple on-disk JSON cache.

This cache is intentionally lightweight:
- It stores JSON-serializable values on disk under `.cache/tripscore/` by default.
- Keys are hashed (SHA-256) to avoid filesystem path issues.
- TTL is enforced on read.

It is used primarily by ingestion clients (TDX, weather) to:
- reduce external API calls,
- speed up repeated recommendations,
- make demos usable with limited rate limits.
"""


@dataclass(frozen=True)
class CacheEntry:
    """Serialized cache envelope stored on disk."""

    created_at_unix: int
    ttl_seconds: int
    value: Any


@dataclass
class CacheStats:
    """Per-request cache usage stats (best-effort)."""

    hits: int = 0
    misses: int = 0
    expired: int = 0
    sets: int = 0
    stale_reads: int = 0
    stale_fallbacks: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "hits": int(self.hits),
            "misses": int(self.misses),
            "expired": int(self.expired),
            "sets": int(self.sets),
            "stale_reads": int(self.stale_reads),
            "stale_fallbacks": int(self.stale_fallbacks),
        }


_cache_stats_var: contextvars.ContextVar[CacheStats | None] = contextvars.ContextVar(
    "tripscore_cache_stats", default=None
)


def _stats() -> CacheStats | None:
    return _cache_stats_var.get()


@contextmanager
def record_cache_stats() -> CacheStats:
    """Capture cache stats within the current context (thread/task-safe)."""

    stats = CacheStats()
    token = _cache_stats_var.set(stats)
    try:
        yield stats
    finally:
        _cache_stats_var.reset(token)


class FileCache:
    """A filesystem-backed cache keyed by (namespace, key)."""

    def __init__(
        self, base_dir: Path, enabled: bool = True, default_ttl_seconds: int = 86400
    ):
        self._base_dir = base_dir
        self._enabled = enabled
        self._default_ttl_seconds = default_ttl_seconds

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _key_path(self, namespace: str, key: str) -> Path:
        """Return the file path for a cache entry (hash-based)."""
        digest = sha256(f"{namespace}:{key}".encode("utf-8")).hexdigest()
        return self._base_dir / namespace / f"{digest}.json"

    def get_entry_meta(self, namespace: str, key: str) -> dict[str, int] | None:
        """Return cache envelope metadata (created_at_unix, ttl_seconds) if present."""
        if not self._enabled:
            return None
        path = self._key_path(namespace, key)
        if not path.exists():
            return None
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return {
                "created_at_unix": int(raw["created_at_unix"]),
                "ttl_seconds": int(raw["ttl_seconds"]),
            }
        except Exception:
            return None

    def get(
        self, namespace: str, key: str, ttl_seconds: int | None = None
    ) -> Any | None:
        """Read a cached value if present and not expired; otherwise return None."""
        if not self._enabled:
            return None

        path = self._key_path(namespace, key)
        if not path.exists():
            st = _stats()
            if st:
                st.misses += 1
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entry = CacheEntry(
                created_at_unix=int(raw["created_at_unix"]),
                ttl_seconds=int(raw["ttl_seconds"]),
                value=raw["value"],
            )
        except Exception:
            st = _stats()
            if st:
                st.misses += 1
            return None

        now = int(time.time())
        effective_ttl = ttl_seconds if ttl_seconds is not None else entry.ttl_seconds
        if now - entry.created_at_unix > effective_ttl:
            st = _stats()
            if st:
                st.misses += 1
                st.expired += 1
            return None

        st = _stats()
        if st:
            st.hits += 1
        return entry.value

    def get_stale(self, namespace: str, key: str) -> Any | None:
        """Read a cached value even if expired; otherwise return None.

        This is useful for "stale-if-error" behavior where an upstream API is down
        or rate-limiting and we prefer to serve slightly old data rather than fail.
        """
        if not self._enabled:
            return None

        path = self._key_path(namespace, key)
        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            value = raw.get("value")
            if value is not None:
                st = _stats()
                if st:
                    st.stale_reads += 1
            return value
        except Exception:
            return None

    def set(
        self, namespace: str, key: str, value: Any, ttl_seconds: int | None = None
    ) -> None:
        """Write a JSON-serializable value to disk.

        Notes:
        - Writes via a temporary file + atomic replace to avoid partial/corrupt cache files.
        """
        if not self._enabled:
            return None

        ttl = ttl_seconds if ttl_seconds is not None else self._default_ttl_seconds
        path = self._key_path(namespace, key)
        path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "created_at_unix": int(time.time()),
            "ttl_seconds": int(ttl),
            "value": value,
        }
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        tmp.replace(path)
        st = _stats()
        if st:
            st.sets += 1

    def get_or_set(
        self,
        namespace: str,
        key: str,
        builder: Callable[[], Any],
        ttl_seconds: int | None = None,
        *,
        stale_if_error: bool = False,
        stale_predicate: Callable[[Exception], bool] | None = None,
    ) -> Any:
        """Return cached value, or compute/store it via `builder`.

        If `stale_if_error` is enabled and `builder()` raises, the cache will attempt
        to return a stale (expired) value instead of failing, as long as:
        - a stale value exists on disk, and
        - `stale_predicate(exc)` is True (or predicate is None).
        """
        cached = self.get(namespace, key, ttl_seconds=ttl_seconds)
        if cached is not None:
            return cached
        try:
            value = builder()
        except Exception as exc:
            if stale_if_error and (stale_predicate(exc) if stale_predicate else True):
                stale = self.get_stale(namespace, key)
                if stale is not None:
                    st = _stats()
                    if st:
                        st.stale_fallbacks += 1
                    return stale
            raise
        else:
            self.set(namespace, key, value, ttl_seconds=ttl_seconds)
            return value
