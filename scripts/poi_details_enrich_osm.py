from __future__ import annotations

import argparse
import json
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

import httpx

from tripscore.catalog.loader import load_destinations
from tripscore.config.settings import get_settings
from tripscore.core.cache import FileCache
from tripscore.core.env import resolve_project_path


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _cache_key(*, lat: float, lon: float) -> str:
    return sha256(f"osm:{lat:.6f}:{lon:.6f}".encode("utf-8")).hexdigest()


def _fetch_nominatim(*, client: httpx.Client, base_url: str, user_agent: str, lat: float, lon: float) -> dict:
    # Nominatim usage policy expects a descriptive UA and low rate.
    headers = {"User-Agent": user_agent}
    params = {"format": "jsonv2", "lat": f"{lat:.6f}", "lon": f"{lon:.6f}", "zoom": "18", "addressdetails": "1"}
    resp = client.get(f"{base_url.rstrip('/')}/reverse", params=params, headers=headers)
    resp.raise_for_status()
    return resp.json()


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description="Enrich POI details from OpenStreetMap Nominatim (open data)."
    )
    p.add_argument("--catalog", type=str, default="data/catalogs/destinations.json")
    p.add_argument("--out", type=str, default="data/catalogs/destination_details.json")
    p.add_argument("--base-url", type=str, default="https://nominatim.openstreetmap.org")
    p.add_argument(
        "--user-agent",
        type=str,
        default="TripScore/0.1 (contact: set YOUR_EMAIL in this flag)",
        help="Required by Nominatim usage policy.",
    )
    p.add_argument("--sleep-seconds", type=float, default=1.1, help="Be polite: Nominatim recommends ~1 req/sec.")
    p.add_argument("--max", type=int, default=0, help="If >0, only process this many destinations.")
    p.add_argument("--only-missing", action="store_true", help="Only fill fields missing in existing details file.")
    args = p.parse_args(argv)

    settings = get_settings()
    cache = FileCache(resolve_project_path(settings.cache.dir), enabled=True, default_ttl_seconds=60 * 60 * 24 * 30)

    catalog_path = resolve_project_path(args.catalog)
    out_path = resolve_project_path(args.out)
    existing: dict[str, dict[str, Any]] = _load_json(out_path, default={})
    if not isinstance(existing, dict):
        existing = {}

    destinations = load_destinations(catalog_path)
    if int(args.max) > 0:
        destinations = destinations[: int(args.max)]

    updated = 0
    with httpx.Client(timeout=20) as client:
        for i, d in enumerate(destinations, start=1):
            cur = existing.get(d.id, {}) if isinstance(existing.get(d.id), dict) else {}

            if args.only_missing:
                # If we already have all main fields, skip.
                if cur.get("address") and cur.get("opening_hours") and cur.get("phone"):
                    continue

            lat = float(d.location.lat)
            lon = float(d.location.lon)
            key = _cache_key(lat=lat, lon=lon)

            def builder() -> dict:
                return _fetch_nominatim(
                    client=client, base_url=args.base_url, user_agent=args.user_agent, lat=lat, lon=lon
                )

            try:
                payload = cache.get_or_set("osm", key, builder, ttl_seconds=60 * 60 * 24 * 30, stale_if_error=True)
            except Exception as e:
                print(f"[{i}/{len(destinations)}] {d.id} fetch failed: {type(e).__name__}: {e}")
                time.sleep(max(0.0, float(args.sleep_seconds)))
                continue

            addr = payload.get("display_name")
            cur = dict(cur)
            if addr and (not cur.get("address") or not args.only_missing):
                cur.setdefault("address", str(addr))

            # Nominatim does not reliably provide opening hours/phone. If an instance returns them,
            # they usually live in "extratags"; keep best-effort support.
            extratags = payload.get("extratags") or {}
            if isinstance(extratags, dict):
                oh = extratags.get("opening_hours")
                if oh and (not cur.get("opening_hours") or not args.only_missing):
                    cur.setdefault("opening_hours", str(oh))
                phone = extratags.get("phone")
                if phone and (not cur.get("phone") or not args.only_missing):
                    cur.setdefault("phone", str(phone))
                website = extratags.get("website")
                if website and (not cur.get("url") or not args.only_missing):
                    cur.setdefault("url", str(website))

            if cur:
                existing[d.id] = cur
                updated += 1
                print(f"[{i}/{len(destinations)}] updated {d.id}")

            time.sleep(max(0.0, float(args.sleep_seconds)))

    _write_json(out_path, existing)
    print("Wrote:", out_path)
    print("Updated entries:", updated)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

