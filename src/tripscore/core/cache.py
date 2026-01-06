from __future__ import annotations

import json
import time
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class CacheEntry:
    created_at_unix: int
    ttl_seconds: int
    value: Any


class FileCache:
    def __init__(self, base_dir: Path, enabled: bool = True, default_ttl_seconds: int = 86400):
        self._base_dir = base_dir
        self._enabled = enabled
        self._default_ttl_seconds = default_ttl_seconds

    def _key_path(self, namespace: str, key: str) -> Path:
        digest = sha256(f"{namespace}:{key}".encode("utf-8")).hexdigest()
        return self._base_dir / namespace / f"{digest}.json"

    def get(self, namespace: str, key: str, ttl_seconds: int | None = None) -> Any | None:
        if not self._enabled:
            return None

        path = self._key_path(namespace, key)
        if not path.exists():
            return None

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            entry = CacheEntry(
                created_at_unix=int(raw["created_at_unix"]),
                ttl_seconds=int(raw["ttl_seconds"]),
                value=raw["value"],
            )
        except Exception:
            return None

        now = int(time.time())
        effective_ttl = ttl_seconds if ttl_seconds is not None else entry.ttl_seconds
        if now - entry.created_at_unix > effective_ttl:
            return None

        return entry.value

    def set(self, namespace: str, key: str, value: Any, ttl_seconds: int | None = None) -> None:
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

    def get_or_set(
        self,
        namespace: str,
        key: str,
        builder: Callable[[], Any],
        ttl_seconds: int | None = None,
    ) -> Any:
        cached = self.get(namespace, key, ttl_seconds=ttl_seconds)
        if cached is not None:
            return cached
        value = builder()
        self.set(namespace, key, value, ttl_seconds=ttl_seconds)
        return value
