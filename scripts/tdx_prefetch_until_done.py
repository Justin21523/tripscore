from __future__ import annotations

import argparse
import time

from tripscore.config.settings import get_settings
from tripscore.ingestion.tdx_bulk import DatasetName, bulk_prefetch_all, read_bulk_progress
from tripscore.ingestion.tdx_client import TdxClient
from tripscore.recommender.recommend import build_cache


DEFAULT_DATASETS: list[DatasetName] = [
    "bus_stops",
    "bus_routes",
    "bike_stations",
    "bike_availability",
    "metro_stations",
    "parking_lots",
    "parking_availability",
]


def _expected_scopes(city: str, operators: list[str]) -> list[tuple[DatasetName, str]]:
    pairs: list[tuple[DatasetName, str]] = [
        ("bus_stops", f"city_{city}"),
        ("bus_routes", f"city_{city}"),
        ("bike_stations", f"city_{city}"),
        ("bike_availability", f"city_{city}"),
        ("parking_lots", f"city_{city}"),
        ("parking_availability", f"city_{city}"),
    ]
    for op in operators:
        pairs.append(("metro_stations", f"operator_{op}"))
    return pairs


def _overall_done(*, cache, city: str, operators: list[str]) -> tuple[bool, list[tuple[str, bool, int | None]]]:
    rows: list[tuple[str, bool, int | None]] = []
    all_done = True
    for dataset, scope in _expected_scopes(city, operators):
        progress = read_bulk_progress(cache, dataset, scope)
        done = bool(progress.get("done", False))
        next_skip = progress.get("next_skip")
        next_skip_i = int(next_skip) if next_skip is not None else None
        rows.append((f"{dataset}/{scope}", done, next_skip_i))
        all_done = all_done and done
    return all_done, rows


def _print_summary(results) -> None:
    for r in results:
        status = "done" if r.done else f"next_skip={r.next_skip}"
        print(
            f"{r.dataset}/{r.scope}: pages={r.pages_fetched} added={r.items_added} total={r.total_items} {status}"
        )


def _pick_slice(items: list[DatasetName], *, start: int, count: int) -> list[DatasetName]:
    if not items:
        return []
    if count <= 0 or count >= len(items):
        return items
    start = start % len(items)
    end = start + count
    if end <= len(items):
        return items[start:end]
    return items[start:] + items[: end - len(items)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--city", default=None)
    parser.add_argument("--sleep-seconds", type=float, default=2.0)
    parser.add_argument("--max-minutes", type=float, default=60.0)
    parser.add_argument("--max-runs", type=int, default=10_000)
    parser.add_argument(
        "--datasets-per-run",
        type=int,
        default=0,
        help="If >0, only process this many datasets per iteration (helps reduce burstiness).",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    cache = build_cache(settings)
    tdx = TdxClient(settings, cache)

    city = str(args.city or settings.ingestion.tdx.city)
    datasets = list(DEFAULT_DATASETS)

    deadline = time.monotonic() + float(args.max_minutes) * 60.0
    run = 0
    offset = 0

    print("Starting TDX bulk prefetch")
    print("city:", city)
    print("cache:", cache.base_dir)
    print("bulk settings:", settings.ingestion.tdx.bulk.model_dump())
    print("request_spacing_seconds:", settings.ingestion.tdx.request_spacing_seconds)
    print("retry:", settings.ingestion.tdx.retry.model_dump())

    while time.monotonic() < deadline and run < int(args.max_runs):
        run += 1
        print(f"\n--- run {run} ---")
        all_results = []
        try:
            ds_chunk = _pick_slice(
                datasets, start=offset, count=int(args.datasets_per_run) or len(datasets)
            )
            offset += max(1, int(args.datasets_per_run) or len(datasets))

            results = bulk_prefetch_all(
                tdx_client=tdx,
                cache=cache,
                city=city,
                datasets=ds_chunk,
                max_pages_per_dataset=int(settings.ingestion.tdx.bulk.max_pages_per_call),
                max_seconds_total=(
                    float(settings.ingestion.tdx.bulk.max_seconds_per_call)
                    if settings.ingestion.tdx.bulk.max_seconds_per_call is not None
                    else None
                ),
                reset=False,
            )
            all_results.extend(results)
            _print_summary(results)

            done, rows = _overall_done(
                cache=cache,
                city=city,
                operators=list(settings.ingestion.tdx.metro_stations.operators),
            )
            done_count = sum(1 for _, d, _ in rows if d)
            print(f"overall: {done_count}/{len(rows)} done")
            if done:
                print("\nAll datasets done.")
                return 0
        except Exception as exc:
            print("Run failed:", type(exc).__name__, str(exc))

        time.sleep(float(args.sleep_seconds))

    print("\nStopped before completion (time or run limit). Re-run to continue.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
