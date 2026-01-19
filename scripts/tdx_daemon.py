from __future__ import annotations

import argparse
import time

from tripscore.config.settings import get_settings
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


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Long-running TDX ingestion daemon (rate-limited, resumable).")
    p.add_argument("--cities", type=str, default="all", help="Comma-separated TDX city names or 'all'.")
    p.add_argument("--sleep-seconds", type=float, default=2.0, help="Sleep between city iterations.")
    p.add_argument("--static-pages-per-run", type=int, default=1, help="Bulk pages per dataset per city per cycle.")
    p.add_argument("--static-seconds-per-run", type=float, default=20.0, help="Bulk time budget per city per cycle.")
    p.add_argument("--dynamic-refresh", action="store_true", help="Continuously refresh dynamic datasets.")
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

    print("TDX daemon starting")
    print("cache:", cache.base_dir)
    print("cities:", ", ".join(cities))
    print("operators:", ", ".join(operators))
    print("request_spacing_seconds:", settings.ingestion.tdx.request_spacing_seconds)
    print("bulk:", settings.ingestion.tdx.bulk.model_dump())
    print("dynamic_refresh:", bool(args.dynamic_refresh))

    while True:
        for city in cities:
            try:
                if not _all_static_done(cache=cache, city=city, operators=operators):
                    # Stageable bulk prefetch (resumable).
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

                if args.dynamic_refresh:
                    # Dynamic datasets: prefer short-TTL live cache; safe to call repeatedly.
                    _ = tdx.get_youbike_station_statuses(city=city)
                    _ = tdx.get_parking_lot_statuses(city=city)
                    print(f"[dynamic] city={city} refreshed")
            except Exception as e:
                print(f"[error] city={city}: {type(e).__name__}: {e}")
            time.sleep(max(0.0, float(args.sleep_seconds)))

        # Yield between full passes.
        time.sleep(max(0.0, float(args.sleep_seconds)))


if __name__ == "__main__":
    raise SystemExit(main())
