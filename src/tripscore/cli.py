"""
TripScore CLI entrypoint.

This CLI is intended for quick local demos and debugging without the web UI.
It delegates all recommendation logic to `tripscore.recommender.recommend.recommend`.
"""

from __future__ import annotations

import argparse
import json
from typing import Any

from tripscore.config.settings import get_settings
from tripscore.core.logging import configure_logging
from tripscore.core.time import parse_datetime
from tripscore.domain.models import ComponentWeights, GeoPoint, TimeWindow, UserPreferences
from tripscore.ingestion.tdx_bulk import DatasetName, bulk_prefetch_all
from tripscore.ingestion.tdx_client import TdxClient
from tripscore.quality.report import build_quality_report
from tripscore.recommender.recommend import recommend
from tripscore.recommender.recommend import build_cache
from tripscore.scoring.explain import one_line_summary


def _parse_tag_weight_pairs(pairs: list[str]) -> dict[str, float]:
    """Parse `TAG=VALUE` CLI arguments into a normalized dict (lower-cased tags)."""
    out: dict[str, float] = {}
    for pair in pairs:
        if "=" not in pair:
            raise ValueError(f"Invalid --tag-weight '{pair}', expected TAG=VALUE")
        tag, value = pair.split("=", 1)
        out[tag.strip().lower()] = float(value)
    return out


def _cmd_tdx_prefetch(args: argparse.Namespace) -> int:
    settings = get_settings()
    cache = build_cache(settings)
    client = TdxClient(settings, cache)

    city = str(args.city or settings.ingestion.tdx.city)
    datasets: list[DatasetName]
    if args.dataset:
        datasets = [ds for ds in args.dataset]
    else:
        datasets = [
            "bus_stops",
            "bus_routes",
            "bike_stations",
            "bike_availability",
            "metro_stations",
            "parking_lots",
            "parking_availability",
        ]

    results = bulk_prefetch_all(
        tdx_client=client,
        cache=cache,
        city=city,
        datasets=datasets,
        max_pages_per_dataset=int(args.max_pages),
        max_seconds_total=float(args.max_seconds) if args.max_seconds is not None else None,
        reset=bool(args.reset),
    )

    for r in results:
        status = "done" if r.done else f"next_skip={r.next_skip}"
        print(
            f"{r.dataset}/{r.scope}: pages={r.pages_fetched} added={r.items_added} total={r.total_items} {status}"
        )
        print(f"  data: {r.data_path}")
        print(f"  progress: {r.progress_path}")

    return 0


def _cmd_recommend(args: argparse.Namespace) -> int:
    """Handle the `recommend` subcommand."""
    settings = get_settings()

    start = parse_datetime(args.start, settings.app.timezone)
    end = parse_datetime(args.end, settings.app.timezone)

    tag_weights: dict[str, float] = {}
    for key, tag in [
        ("indoor", "indoor"),
        ("outdoor", "outdoor"),
        ("culture", "culture"),
        ("food", "food"),
        ("family_friendly", "family_friendly"),
        ("crowd_low", "crowd_low"),
    ]:
        v = getattr(args, key)
        if v is not None:
            tag_weights[tag] = float(v)

    if args.tag_weight:
        tag_weights.update(_parse_tag_weight_pairs(args.tag_weight))
    if not tag_weights:
        tag_weights = None

    cw_kwargs: dict[str, float] = {}
    if args.w_accessibility is not None:
        cw_kwargs["accessibility"] = float(args.w_accessibility)
    if args.w_weather is not None:
        cw_kwargs["weather"] = float(args.w_weather)
    if args.w_preference is not None:
        cw_kwargs["preference"] = float(args.w_preference)
    if args.w_context is not None:
        cw_kwargs["context"] = float(args.w_context)
    component_weights = ComponentWeights(**cw_kwargs) if cw_kwargs else None

    prefs = UserPreferences(
        origin=GeoPoint(lat=float(args.origin_lat), lon=float(args.origin_lon)),
        time_window=TimeWindow(start=start, end=end),
        preset=args.preset,
        max_results=int(args.max_results) if args.max_results is not None else None,
        component_weights=component_weights,
        weather_rain_importance=float(args.avoid_rain) if args.avoid_rain is not None else None,
        avoid_crowds_importance=float(args.avoid_crowds) if args.avoid_crowds is not None else None,
        family_friendly_importance=(
            float(args.family_importance) if args.family_importance is not None else None
        ),
        tag_weights=tag_weights,
        required_tags=args.required_tag or [],
        excluded_tags=args.excluded_tag or [],
    )

    result = recommend(prefs, settings=settings)

    if args.json:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0

    print(f"Generated at: {result.generated_at.isoformat()}")
    print("Top results:")
    for i, item in enumerate(result.results, start=1):
        dest = item.destination
        breakdown = item.breakdown
        print(f"{i:>2}. {dest.name} ({dest.district or dest.city})  {one_line_summary(breakdown)}")
        for comp in breakdown.components:
            limit = 4 if comp.name == "accessibility" else 2
            reasons = "; ".join(comp.reasons[:limit]) if comp.reasons else ""
            print(f"    - {comp.name}: score={comp.score:.3f} weight={comp.weight:.2f}  {reasons}")
    return 0


def _cmd_quality_report(_: argparse.Namespace) -> int:
    settings = get_settings()
    report = build_quality_report(settings)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the argparse parser for the TripScore CLI."""
    parser = argparse.ArgumentParser(prog="tripscore")
    sub = parser.add_subparsers(dest="command", required=True)

    pre = sub.add_parser(
        "tdx-prefetch",
        help="Gradually prefetch full TDX datasets into the local cache (safe to run repeatedly).",
    )
    pre.add_argument("--city", type=str, default=None)
    pre.add_argument(
        "--dataset",
        action="append",
        default=[],
        choices=[
            "bus_stops",
            "bus_routes",
            "bike_stations",
            "bike_availability",
            "metro_stations",
            "parking_lots",
            "parking_availability",
        ],
        help="Repeatable. Omit to fetch all datasets.",
    )
    pre.add_argument("--max-pages", type=int, default=1, help="Pages per dataset per run.")
    pre.add_argument("--max-seconds", type=float, default=None, help="Total time budget for this run.")
    pre.add_argument("--reset", action="store_true", help="Reset progress/data for selected datasets first.")
    pre.set_defaults(func=_cmd_tdx_prefetch)

    rec = sub.add_parser("recommend", help="Recommend destinations for a time window and preferences.")
    rec.add_argument("--origin-lat", required=True, type=float)
    rec.add_argument("--origin-lon", required=True, type=float)
    rec.add_argument("--start", required=True, help="ISO datetime (e.g. 2026-01-05T10:00+08:00)")
    rec.add_argument("--end", required=True, help="ISO datetime (e.g. 2026-01-05T18:00+08:00)")
    rec.add_argument("--max-results", type=int, default=None)
    rec.add_argument("--preset", type=str, default=None, help="Preset name from config (see /api/presets)")

    rec.add_argument("--w-accessibility", type=float, default=None)
    rec.add_argument("--w-weather", type=float, default=None)
    rec.add_argument("--w-preference", type=float, default=None)
    rec.add_argument("--w-context", type=float, default=None)

    rec.add_argument("--avoid-rain", type=float, default=None, help="0..1; higher means rain matters more")
    rec.add_argument(
        "--avoid-crowds", type=float, default=None, help="0..1; higher means crowd risk matters more"
    )
    rec.add_argument(
        "--family-importance",
        dest="family_importance",
        type=float,
        default=None,
        help="0..1; higher means family-friendliness matters more",
    )

    rec.add_argument("--indoor", type=float, default=None)
    rec.add_argument("--outdoor", type=float, default=None)
    rec.add_argument("--culture", type=float, default=None)
    rec.add_argument("--food", type=float, default=None)
    rec.add_argument("--family-friendly", dest="family_friendly", type=float, default=None)
    rec.add_argument("--crowd-low", dest="crowd_low", type=float, default=None)
    rec.add_argument("--tag-weight", action="append", default=[], help="Override tag weights: TAG=VALUE")

    rec.add_argument("--required-tag", action="append", default=[])
    rec.add_argument("--excluded-tag", action="append", default=[])
    rec.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    rec.set_defaults(func=_cmd_recommend)

    q = sub.add_parser("quality-report", help="Offline data quality report (catalog + local bulk cache).")
    q.set_defaults(func=_cmd_quality_report)
    return parser


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint callable used by `python -m tripscore.cli`."""
    configure_logging()
    parser = build_parser()
    args = parser.parse_args(argv)
    func: Any = getattr(args, "func")
    return int(func(args))


if __name__ == "__main__":
    raise SystemExit(main())
