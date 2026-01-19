from __future__ import annotations

import argparse
import json
import random
import time
from dataclasses import dataclass
from pathlib import Path

from tripscore.config.settings import get_settings
from tripscore.ingestion.tdx_bulk import DatasetName, bulk_prefetch_all, read_bulk_progress
from tripscore.ingestion.tdx_client import TdxClient
from tripscore.core.env import resolve_project_path
from tripscore.recommender.recommend import build_cache


ALL_CITIES: list[str] = [
    "Taipei",
    "NewTaipei",
    "Taoyuan",
    "Taichung",
    "Tainan",
    "Kaohsiung",
    "Keelung",
    "Hsinchu",
    "HsinchuCounty",
    "MiaoliCounty",
    "ChanghuaCounty",
    "NantouCounty",
    "YunlinCounty",
    "Chiayi",
    "ChiayiCounty",
    "PingtungCounty",
    "YilanCounty",
    "HualienCounty",
    "TaitungCounty",
    "KinmenCounty",
    "PenghuCounty",
    "LienchiangCounty",
]

STATIC_DATASETS: list[DatasetName] = [
    "bus_stops",
    "bus_routes",
    "bike_stations",
    "parking_lots",
    "metro_stations",
]

DYNAMIC_DATASETS: list[DatasetName] = [
    "bike_availability",
    "parking_availability",
]


def _parse_cities(arg: str) -> list[str]:
    if not arg or arg == "all":
        return list(ALL_CITIES)
    return [s.strip() for s in arg.split(",") if s.strip()]


def _all_static_done(*, cache, city: str, operators: list[str]) -> bool:
    for ds in ["bus_stops", "bus_routes", "bike_stations", "parking_lots"]:
        p = read_bulk_progress(cache, ds, f"city_{city}") or {}
        if not bool(p.get("done", False)):
            return False
    for op in operators:
        p = read_bulk_progress(cache, "metro_stations", f"operator_{op}") or {}
        if not bool(p.get("done", False)):
            return False
    return True


@dataclass
class _DaemonState:
    cursor: int = 0
    city_last_dynamic_unix: dict[str, int] | None = None
    city_last_static_refresh_unix: dict[str, int] | None = None
    city_cooldown_until_unix: dict[str, int] | None = None
    consecutive_429: int = 0

    @classmethod
    def load(cls, path: Path) -> "_DaemonState":
        if not path.exists():
            return cls(
                city_last_dynamic_unix={},
                city_last_static_refresh_unix={},
                city_cooldown_until_unix={},
            )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("bad state")
        except Exception:
            return cls(
                city_last_dynamic_unix={},
                city_last_static_refresh_unix={},
                city_cooldown_until_unix={},
            )
        return cls(
            cursor=int(payload.get("cursor") or 0),
            city_last_dynamic_unix=dict(payload.get("city_last_dynamic_unix") or {}),
            city_last_static_refresh_unix=dict(payload.get("city_last_static_refresh_unix") or {}),
            city_cooldown_until_unix=dict(payload.get("city_cooldown_until_unix") or {}),
            consecutive_429=int(payload.get("consecutive_429") or 0),
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        payload = {
            "cursor": int(self.cursor),
            "city_last_dynamic_unix": self.city_last_dynamic_unix or {},
            "city_last_static_refresh_unix": self.city_last_static_refresh_unix or {},
            "city_cooldown_until_unix": self.city_cooldown_until_unix or {},
            "consecutive_429": int(self.consecutive_429),
        }
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(path)


def _looks_like_429(exc: Exception) -> bool:
    msg = str(exc).lower()
    return " 429" in msg or "too many requests" in msg or "status=429" in msg


def _reset_bulk_for_city(cache_dir: Path, *, city: str) -> None:
    base = cache_dir / "tdx_bulk"
    if not base.exists():
        return
    for ds in ["bus_stops", "bus_routes", "bike_stations", "parking_lots", "parking_availability", "bike_availability"]:
        d = base / ds
        if not d.exists():
            continue
        for p in d.glob(f"city_{city}.*"):
            try:
                p.unlink()
            except Exception:
                continue


def _reset_bulk_for_metro(cache_dir: Path, *, operators: list[str]) -> None:
    base = cache_dir / "tdx_bulk" / "metro_stations"
    if not base.exists():
        return
    for op in operators:
        for p in base.glob(f"operator_{op}.*"):
            try:
                p.unlink()
            except Exception:
                continue


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Long-running TDX ingestion daemon (rate-limited, resumable).")
    p.add_argument("--cities", type=str, default="all", help="Comma-separated TDX city names or 'all'.")
    p.add_argument("--sleep-seconds", type=float, default=3.0, help="Base sleep between iterations.")
    p.add_argument("--static-pages-per-run", type=int, default=1, help="Bulk pages per dataset per city per cycle.")
    p.add_argument("--static-seconds-per-run", type=float, default=20.0, help="Bulk time budget per city per cycle.")
    p.add_argument("--dynamic-refresh", action="store_true", help="Continuously refresh dynamic datasets.")
    p.add_argument("--dynamic-interval-seconds", type=float, default=900.0, help="Per-city dynamic refresh interval.")
    p.add_argument(
        "--cooldown-seconds",
        type=float,
        default=900.0,
        help="Per-city cooldown after repeated 429 (skip city until cooldown expires).",
    )
    p.add_argument(
        "--static-refresh-days",
        type=float,
        default=30.0,
        help="Periodically reset and re-download static bulk datasets (days).",
    )
    p.add_argument(
        "--state-path",
        type=str,
        default=".cache/tripscore/tdx_daemon/state.json",
        help="Local state file path (mounted volume).",
    )
    p.add_argument("--request-spacing-seconds", type=float, default=None, help="Override TDX request spacing.")
    p.add_argument("--retry-max-attempts", type=int, default=None, help="Override retry max attempts.")
    p.add_argument("--bulk-max-pages-per-call", type=int, default=None, help="Override bulk pages per call.")
    p.add_argument("--bulk-max-seconds-per-call", type=float, default=None, help="Override bulk seconds per call.")
    args = p.parse_args(argv)

    settings = get_settings()
    # Optional runtime overrides (do not persist to YAML).
    tdx_settings = settings.ingestion.tdx
    updates: dict = {}
    if args.request_spacing_seconds is not None:
        updates["request_spacing_seconds"] = float(args.request_spacing_seconds)
    if args.retry_max_attempts is not None:
        updates["retry"] = tdx_settings.retry.model_copy(update={"max_attempts": int(args.retry_max_attempts)})
    if args.bulk_max_pages_per_call is not None or args.bulk_max_seconds_per_call is not None:
        b = tdx_settings.bulk
        b_updates = {}
        if args.bulk_max_pages_per_call is not None:
            b_updates["max_pages_per_call"] = int(args.bulk_max_pages_per_call)
        if args.bulk_max_seconds_per_call is not None:
            b_updates["max_seconds_per_call"] = float(args.bulk_max_seconds_per_call)
        updates["bulk"] = b.model_copy(update=b_updates)
    if updates:
        tdx_settings = tdx_settings.model_copy(update=updates)
        ingestion = settings.ingestion.model_copy(update={"tdx": tdx_settings})
        settings = settings.model_copy(update={"ingestion": ingestion})
    cache = build_cache(settings)
    tdx = TdxClient(settings, cache)
    operators = list(settings.ingestion.tdx.metro_stations.operators)
    cities = _parse_cities(str(args.cities))
    cache_dir = resolve_project_path(settings.cache.dir)
    state_path = resolve_project_path(str(args.state_path))
    state = _DaemonState.load(state_path)

    # Ensure dicts exist.
    state.city_last_dynamic_unix = state.city_last_dynamic_unix or {}
    state.city_last_static_refresh_unix = state.city_last_static_refresh_unix or {}
    state.city_cooldown_until_unix = state.city_cooldown_until_unix or {}

    print("TDX daemon starting")
    print("cache:", cache.base_dir)
    print("cities:", ", ".join(cities))
    print("operators:", ", ".join(operators))
    print("request_spacing_seconds:", settings.ingestion.tdx.request_spacing_seconds)
    print("bulk:", settings.ingestion.tdx.bulk.model_dump())
    print("dynamic_refresh:", bool(args.dynamic_refresh))
    print("dynamic_interval_seconds:", float(args.dynamic_interval_seconds))
    print("cooldown_seconds:", float(args.cooldown_seconds))
    print("static_refresh_days:", float(args.static_refresh_days))
    print("state_path:", state_path)

    while True:
        now = int(time.time())

        # Periodic static refresh: reset bulk files to re-download slowly (e.g. monthly).
        refresh_days = float(args.static_refresh_days)
        if refresh_days > 0:
            refresh_interval = int(refresh_days * 86400)
            last = int(state.city_last_static_refresh_unix.get("_global", 0) or 0)
            if now - last >= refresh_interval:
                print("[static] refreshing: resetting bulk progress/data (global)")
                _reset_bulk_for_metro(cache_dir, operators=operators)
                for c in cities:
                    _reset_bulk_for_city(cache_dir, city=c)
                state.city_last_static_refresh_unix["_global"] = now
                state.save(state_path)

        # One-city per iteration (round-robin) to avoid bursts.
        if not cities:
            time.sleep(max(1.0, float(args.sleep_seconds)))
            continue
        city = cities[state.cursor % len(cities)]
        state.cursor = (state.cursor + 1) % len(cities)

        # Per-city cooldown after repeated 429.
        cooldown_until = int(state.city_cooldown_until_unix.get(city, 0) or 0)
        if cooldown_until and now < cooldown_until:
            time.sleep(max(1.0, float(args.sleep_seconds)))
            continue

        # Static prefetch (resumable). Only do work if not done.
        try:
            if not _all_static_done(cache=cache, city=city, operators=operators):
                bulk_prefetch_all(
                    tdx_client=tdx,
                    cache=cache,
                    city=city,
                    datasets=list(STATIC_DATASETS),
                    max_pages_per_dataset=int(args.static_pages_per_run),
                    max_seconds_total=float(args.static_seconds_per_run),
                    reset=False,
                )
                print(f"[static] city={city} progressed")
        except Exception as e:
            if _looks_like_429(e):
                state.consecutive_429 += 1
                # Escalate cooldown for this city when 429 keeps happening.
                if state.consecutive_429 >= 2:
                    cd = int(float(args.cooldown_seconds))
                    jitter = random.randint(0, max(30, cd // 10))
                    state.city_cooldown_until_unix[city] = int(time.time()) + cd + jitter
                    print(f"[cooldown] city={city} until={state.city_cooldown_until_unix[city]}")
            else:
                state.consecutive_429 = 0
            print(f"[error] static city={city}: {type(e).__name__}: {e}")
            state.save(state_path)
            time.sleep(max(1.0, float(args.sleep_seconds)))
            continue

        # Dynamic refresh on a per-city interval.
        if args.dynamic_refresh:
            last_dyn = int(state.city_last_dynamic_unix.get(city, 0) or 0)
            if now - last_dyn >= int(float(args.dynamic_interval_seconds)):
                try:
                    _ = tdx.get_youbike_station_statuses(city=city)
                    _ = tdx.get_parking_lot_statuses(city=city)
                    state.city_last_dynamic_unix[city] = int(time.time())
                    state.consecutive_429 = 0
                    print(f"[dynamic] city={city} refreshed")
                except Exception as e:
                    if _looks_like_429(e):
                        state.consecutive_429 += 1
                        cd = int(float(args.cooldown_seconds))
                        jitter = random.randint(0, max(30, cd // 10))
                        state.city_cooldown_until_unix[city] = int(time.time()) + cd + jitter
                        print(f"[cooldown] city={city} until={state.city_cooldown_until_unix[city]}")
                    print(f"[error] dynamic city={city}: {type(e).__name__}: {e}")
                finally:
                    state.save(state_path)

        # Persist state periodically.
        if random.random() < 0.1:
            state.save(state_path)

        time.sleep(max(1.0, float(args.sleep_seconds)))

        # Yield between passes.
        time.sleep(max(0.5, float(args.sleep_seconds) * 0.2))


if __name__ == "__main__":
    raise SystemExit(main())
