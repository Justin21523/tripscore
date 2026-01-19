from __future__ import annotations

import argparse
import time

from tripscore.config.settings import get_settings
from tripscore.core.env import resolve_project_path
from tripscore.ingestion.tdx_bulk import DatasetName, bulk_prefetch_all, read_bulk_progress
from tripscore.ingestion.tdx_client import TdxClient
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

CITY_DATASETS: list[DatasetName] = [
    "bus_stops",
    "bus_routes",
    "bike_stations",
    "bike_availability",
    "parking_lots",
    "parking_availability",
]


def _overall_done_city(*, cache, city: str, metro_operators: list[str]) -> tuple[bool, int, int]:
    expected = [(ds, f"city_{city}") for ds in CITY_DATASETS]
    done_count = 0
    for dataset, scope in expected:
        progress = read_bulk_progress(cache, dataset, scope)
        if bool(progress.get("done", False)):
            done_count += 1
    return done_count == len(expected), done_count, len(expected)

def _overall_done_metro(*, cache, metro_operators: list[str]) -> tuple[bool, int, int]:
    expected = [("metro_stations", f"operator_{op}") for op in metro_operators]
    done_count = 0
    for dataset, scope in expected:
        progress = read_bulk_progress(cache, dataset, scope)
        if bool(progress.get("done", False)):
            done_count += 1
    return done_count == len(expected), done_count, len(expected)


def _pending_city_datasets(*, cache, city: str) -> list[DatasetName]:
    pending: list[DatasetName] = []
    for ds in CITY_DATASETS:
        scope = f"city_{city}"
        progress = read_bulk_progress(cache, ds, scope)
        if not bool(progress.get("done", False)):
            pending.append(ds)
    return pending


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cities", type=str, default="all", help="Comma-separated TDX city names or 'all'.")
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument("--datasets-per-run", type=int, default=1)
    parser.add_argument("--metro-operators", type=str, default="TRTC,KRTC,TYMC")
    parser.add_argument("--request-spacing-seconds", type=float, default=0.2)
    parser.add_argument("--max-pages-per-call", type=int, default=1)
    parser.add_argument("--max-seconds-per-call", type=float, default=20.0)
    parser.add_argument("--retry-max-attempts", type=int, default=3)
    parser.add_argument("--log-path", type=str, default=".cache/tripscore/tdx_bulk/prefetch_all.log")
    args = parser.parse_args(argv)

    settings = get_settings()
    cache_dir = resolve_project_path(settings.cache.dir)

    # Override runtime knobs for stability (do not persist to YAML).
    tdx_settings = settings.ingestion.tdx.model_copy(
        update={
            "request_spacing_seconds": float(args.request_spacing_seconds),
            "retry": settings.ingestion.tdx.retry.model_copy(update={"max_attempts": int(args.retry_max_attempts)}),
            "bulk": settings.ingestion.tdx.bulk.model_copy(
                update={
                    "enabled": True,
                    "max_pages_per_call": int(args.max_pages_per_call),
                    "max_seconds_per_call": float(args.max_seconds_per_call),
                }
            ),
            "metro_stations": settings.ingestion.tdx.metro_stations.model_copy(
                update={
                    "operators": [s.strip() for s in str(args.metro_operators).split(",") if s.strip()],
                }
            ),
        }
    )
    ingestion = settings.ingestion.model_copy(update={"tdx": tdx_settings})
    settings = settings.model_copy(update={"ingestion": ingestion})

    cache = build_cache(settings)
    tdx = TdxClient(settings, cache)

    log_path = resolve_project_path(str(args.log_path))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    metro_ops = list(settings.ingestion.tdx.metro_stations.operators)
    cities = (
        ALL_CITIES
        if str(args.cities).strip().lower() == "all"
        else [s.strip() for s in str(args.cities).split(",") if s.strip()]
    )

    def log(msg: str) -> None:
        line = f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
        print(line)
        with log_path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    log("Starting TDX all-cities bulk prefetch")
    log(f"cache_dir={cache_dir}")
    log(f"request_spacing_seconds={settings.ingestion.tdx.request_spacing_seconds}")
    log(f"retry_max_attempts={settings.ingestion.tdx.retry.max_attempts}")
    log(f"bulk={settings.ingestion.tdx.bulk.model_dump()}")
    log(f"metro_operators={metro_ops}")
    log(f"cities={cities}")

    # Prefetch metro operators once (not city-scoped).
    log("=== metro (operators) ===")
    while True:
        done, done_count, total = _overall_done_metro(cache=cache, metro_operators=metro_ops)
        log(f"metro overall: {done_count}/{total} done")
        if done:
            break
        try:
            results = bulk_prefetch_all(
                tdx_client=tdx,
                cache=cache,
                city=settings.ingestion.tdx.city,
                datasets=["metro_stations"],
                max_pages_per_dataset=int(settings.ingestion.tdx.bulk.max_pages_per_call),
                max_seconds_total=float(settings.ingestion.tdx.bulk.max_seconds_per_call)
                if settings.ingestion.tdx.bulk.max_seconds_per_call is not None
                else None,
                reset=False,
            )
            for r in results:
                if r.pages_fetched or r.items_added or r.done:
                    status = "done" if r.done else f"next_skip={r.next_skip}"
                    log(
                        f"{r.dataset}/{r.scope} pages={r.pages_fetched} added={r.items_added} total={r.total_items} {status}"
                    )
        except Exception as exc:
            log(f"error metro type={type(exc).__name__} msg={exc}")
        time.sleep(float(args.sleep_seconds))

    for city in cities:
        log(f"=== city={city} ===")
        offset = 0
        while True:
            done, done_count, total = _overall_done_city(cache=cache, city=city, metro_operators=metro_ops)
            if done:
                log(f"city={city} done ({done_count}/{total})")
                break

            pending = _pending_city_datasets(cache=cache, city=city)
            if not pending:
                log(f"city={city} done ({done_count}/{total})")
                break

            ds_count = max(1, int(args.datasets_per_run))
            start = offset % len(pending)
            ds_chunk = pending[start : start + ds_count]
            if len(ds_chunk) < ds_count:
                ds_chunk += pending[: ds_count - len(ds_chunk)]
            offset += ds_count

            try:
                results = bulk_prefetch_all(
                    tdx_client=tdx,
                    cache=cache,
                    city=city,
                    datasets=ds_chunk,
                    max_pages_per_dataset=int(settings.ingestion.tdx.bulk.max_pages_per_call),
                    max_seconds_total=float(settings.ingestion.tdx.bulk.max_seconds_per_call)
                    if settings.ingestion.tdx.bulk.max_seconds_per_call is not None
                    else None,
                    reset=False,
                )
                for r in results:
                    if r.pages_fetched or r.items_added or r.done:
                        status = "done" if r.done else f"next_skip={r.next_skip}"
                        log(
                            f"{r.dataset}/{r.scope} pages={r.pages_fetched} added={r.items_added} total={r.total_items} {status}"
                        )
            except Exception as exc:
                log(f"error city={city} type={type(exc).__name__} msg={exc}")

            time.sleep(float(args.sleep_seconds))

    log("All cities done.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
