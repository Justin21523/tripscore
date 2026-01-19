from __future__ import annotations

import argparse
import csv
import json
import math
import re
from pathlib import Path
from typing import Any

from tripscore.catalog.loader import load_destinations
from tripscore.core.env import resolve_project_path


DETAIL_FIELDS = ["url", "description", "address", "phone", "opening_hours", "city", "district"]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _norm_name(s: str) -> str:
    t = str(s or "").strip().lower()
    t = re.sub(r"[\\s\\-_/·•・,，。．.()（）\\[\\]{}「」『』《》<>]+", " ", t)
    t = re.sub(r"\\s+", " ", t).strip()
    return t


def _haversine_m(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    r = 6371000.0
    to_rad = math.radians
    d_lat = to_rad(b_lat - a_lat)
    d_lon = to_rad(b_lon - a_lon)
    lat1 = to_rad(a_lat)
    lat2 = to_rad(b_lat)
    s = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lon / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(s)))


def _split_tags(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        raw = value
    else:
        raw = str(value).split(",")
    tags = []
    for t in raw:
        s = str(t).strip().lower()
        if s:
            tags.append(s)
    return sorted(set(tags))


def import_rows_from_csv(path: Path) -> list[dict[str, Any]]:
    out = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if isinstance(row, dict):
                out.append(row)
    return out


def import_rows_from_json(path: Path) -> list[dict[str, Any]]:
    payload = _read_json(path)
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        # Allow {id: {...}} shape.
        out = []
        for k, v in payload.items():
            if isinstance(k, str) and k.strip() and isinstance(v, dict):
                out.append({"id": k, **v})
        return out
    raise ValueError("Unsupported JSON shape: expected array or object.")


def _as_float(v: Any) -> float | None:
    try:
        x = float(v)
        if not (-90 <= x <= 90) and not (-180 <= x <= 180):
            # Still allow; range checked later.
            pass
        return x
    except Exception:
        return None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Import/merge destinations from a local CSV/JSON file (offline).")
    p.add_argument("--catalog", type=str, default="data/catalogs/destinations.json")
    p.add_argument("--details", type=str, default="data/catalogs/destination_details.json")
    p.add_argument("--in-csv", type=str, default=None)
    p.add_argument("--in-json", type=str, default=None)
    p.add_argument("--merge", choices=["keep-existing", "overwrite"], default="keep-existing")
    p.add_argument("--id-field", type=str, default="id")
    p.add_argument("--name-field", type=str, default="name")
    p.add_argument("--lat-field", type=str, default="lat")
    p.add_argument("--lon-field", type=str, default="lon")
    p.add_argument("--tags-field", type=str, default="tags")
    p.add_argument(
        "--dedupe-radius-m",
        type=float,
        default=40.0,
        help="Treat incoming rows as duplicates if within this radius and name matches (0 disables).",
    )
    args = p.parse_args(argv)

    if bool(args.in_csv) == bool(args.in_json):
        raise SystemExit("Provide exactly one of --in-csv or --in-json.")

    catalog_path = resolve_project_path(args.catalog)
    details_path = resolve_project_path(args.details)

    existing = load_destinations(catalog_path)
    by_id = {d.id: d.model_dump(mode="json") for d in existing}
    by_coord: dict[tuple[int, int], list[str]] = {}
    by_name: dict[str, list[str]] = {}

    def coord_key(lat: float, lon: float) -> tuple[int, int]:
        # ~11m grid (1e-4 deg lat) - used only as a candidate bucket, not a truth match.
        return (int(round(lat * 10000)), int(round(lon * 10000)))

    for d in existing:
        try:
            lat = float(d.location.lat)
            lon = float(d.location.lon)
        except Exception:
            continue
        by_coord.setdefault(coord_key(lat, lon), []).append(d.id)
        by_name.setdefault(_norm_name(d.name), []).append(d.id)

    existing_details: dict[str, dict[str, Any]] = {}
    if details_path.exists():
        try:
            payload = _read_json(details_path)
            if isinstance(payload, dict):
                existing_details = {k: v for k, v in payload.items() if isinstance(k, str) and isinstance(v, dict)}
        except Exception:
            existing_details = {}

    rows = (
        import_rows_from_csv(resolve_project_path(args.in_csv))
        if args.in_csv
        else import_rows_from_json(resolve_project_path(args.in_json))
    )

    added = 0
    updated = 0
    skipped = 0
    bad = 0

    for r in rows:
        dest_id = str(r.get(args.id_field) or "").strip()
        name = str(r.get(args.name_field) or "").strip()
        lat = _as_float(r.get(args.lat_field))
        lon = _as_float(r.get(args.lon_field))
        if not dest_id or not name or lat is None or lon is None:
            bad += 1
            continue
        if not (-90 <= float(lat) <= 90 and -180 <= float(lon) <= 180):
            bad += 1
            continue

        incoming = {
            "id": dest_id,
            "name": name,
            "location": {"lat": float(lat), "lon": float(lon)},
            "tags": _split_tags(r.get(args.tags_field)),
        }

        # Dedupe: if incoming row is close to an existing POI with the same normalized name,
        # treat it as an update to the existing id (do not create a new destination id).
        target_id = dest_id
        if float(args.dedupe_radius_m) > 0:
            nkey = _norm_name(name)
            candidates_ids = []
            candidates_ids.extend(by_name.get(nkey, []))
            candidates_ids.extend(by_coord.get(coord_key(float(lat), float(lon)), []))
            candidates_ids = list(dict.fromkeys(candidates_ids))
            for cid in candidates_ids:
                cur = by_id.get(cid)
                if not isinstance(cur, dict):
                    continue
                try:
                    clat = float((cur.get("location") or {}).get("lat"))
                    clon = float((cur.get("location") or {}).get("lon"))
                except Exception:
                    continue
                if _norm_name(str(cur.get("name") or "")) != nkey:
                    continue
                d_m = _haversine_m(float(lat), float(lon), clat, clon)
                if d_m <= float(args.dedupe_radius_m):
                    target_id = str(cur.get("id") or cid)
                    break

        incoming["id"] = target_id

        if target_id not in by_id:
            by_id[target_id] = incoming
            added += 1
        else:
            if args.merge == "overwrite":
                by_id[target_id] = {**by_id[target_id], **incoming}
                updated += 1
            else:
                # Merge tags even in keep-existing mode (tags are additive).
                cur = by_id[target_id]
                cur_tags = set((cur.get("tags") or []) if isinstance(cur, dict) else [])
                inc_tags = set(incoming.get("tags") or [])
                if isinstance(cur, dict):
                    cur["tags"] = sorted({str(t).strip().lower() for t in (cur_tags | inc_tags) if str(t).strip()})
                skipped += 1

        # Merge details fields into destination_details.json (kept separate).
        det_in = {}
        for k in DETAIL_FIELDS:
            v = r.get(k)
            if isinstance(v, str) and v.strip():
                det_in[k] = v.strip()
        if det_in:
            cur = dict(existing_details.get(target_id) or {})
            if args.merge == "overwrite":
                cur.update(det_in)
            else:
                for k, v in det_in.items():
                    if k not in cur or not str(cur.get(k) or "").strip():
                        cur[k] = v
            existing_details[target_id] = cur

    out_list = list(by_id.values())
    out_list.sort(key=lambda d: str(d.get("id") or ""))
    _write_json(catalog_path, out_list)
    _write_json(details_path, existing_details)

    print("Wrote catalog:", catalog_path)
    print("Wrote details:", details_path)
    print("Imported rows:", len(rows))
    print("Added:", added, "Updated:", updated, "Skipped:", skipped, "Bad:", bad)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
