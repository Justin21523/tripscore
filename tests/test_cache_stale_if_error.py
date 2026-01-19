import pytest

from tripscore.core.cache import FileCache


def test_file_cache_stale_if_error_returns_expired_value(monkeypatch, tmp_path):
    cache = FileCache(tmp_path, enabled=True, default_ttl_seconds=1)

    monkeypatch.setattr("tripscore.core.cache.time.time", lambda: 0)
    cache.set("ns", "k", {"v": 1}, ttl_seconds=1)

    monkeypatch.setattr("tripscore.core.cache.time.time", lambda: 100)

    def builder():
        raise RuntimeError("upstream down")

    val = cache.get_or_set(
        "ns",
        "k",
        builder,
        ttl_seconds=1,
        stale_if_error=True,
        stale_predicate=lambda exc: isinstance(exc, RuntimeError),
    )
    assert val == {"v": 1}


def test_file_cache_stale_if_error_respects_predicate(monkeypatch, tmp_path):
    cache = FileCache(tmp_path, enabled=True, default_ttl_seconds=1)

    monkeypatch.setattr("tripscore.core.cache.time.time", lambda: 0)
    cache.set("ns", "k", {"v": 1}, ttl_seconds=1)

    monkeypatch.setattr("tripscore.core.cache.time.time", lambda: 100)

    def builder():
        raise RuntimeError("upstream down")

    with pytest.raises(RuntimeError):
        cache.get_or_set(
            "ns",
            "k",
            builder,
            ttl_seconds=1,
            stale_if_error=True,
            stale_predicate=lambda exc: isinstance(exc, ValueError),
        )

