from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from tripscore.catalog.loader import load_destinations
from tripscore.core.env import resolve_project_path


FIELDS = ["address", "phone", "opening_hours", "url", "description", "city", "district"]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_row(d: dict[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for k in FIELDS:
        v = d.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            out[k] = s
    return out


def import_from_csv(path: Path, *, id_field: str) -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            if not raw:
                continue
            dest_id = str(raw.get(id_field) or "").strip()
            if not dest_id:
                continue
            rows[dest_id] = _normalize_row(raw)
    return rows


def import_from_json(path: Path, *, id_field: str) -> dict[str, dict[str, str]]:
    payload = _read_json(path)
    out: dict[str, dict[str, str]] = {}

    if isinstance(payload, dict):
        for k, v in payload.items():
            if not isinstance(k, str) or not k.strip():
                continue
            if isinstance(v, dict):
                out[k] = _normalize_row(v)
        return out

    if isinstance(payload, list):
        for row in payload:
            if not isinstance(row, dict):
                continue
            dest_id = str(row.get(id_field) or "").strip()
            if not dest_id:
                continue
            out[dest_id] = _normalize_row(row)
        return out

    raise ValueError("Unsupported JSON shape: expected object or array.")


def merge_details(
    *,
    existing: dict[str, dict[str, Any]] | None,
    incoming: dict[str, dict[str, str]],
    mode: str,
) -> dict[str, dict[str, Any]]:
    base: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (existing or {}).items() if isinstance(v, dict)}

    for dest_id, fields in incoming.items():
        cur = base.get(dest_id, {})
        if mode == "overwrite":
            cur = {**cur, **fields}
        else:
            # keep-existing
            for k, v in fields.items():
                if k not in cur or not str(cur.get(k) or "").strip():
                    cur[k] = v
        base[dest_id] = cur

    # Drop empties
    cleaned: dict[str, dict[str, Any]] = {}
    for dest_id, fields in base.items():
        if not isinstance(fields, dict):
            continue
        out = {k: str(v).strip() for k, v in fields.items() if k in FIELDS and str(v).strip()}
        if out:
            cleaned[dest_id] = out
    return cleaned


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build/merge a local POI details file for TripScore.")
    p.add_argument("--catalog", type=str, default="data/catalogs/destinations.json")
    p.add_argument("--out", type=str, default="data/catalogs/destination_details.json")
    p.add_argument("--in-csv", type=str, default=None)
    p.add_argument("--in-json", type=str, default=None)
    p.add_argument("--id-field", type=str, default="id", help="ID column/key for input rows.")
    p.add_argument("--merge", choices=["keep-existing", "overwrite"], default="keep-existing")
    args = p.parse_args(argv)

    catalog_path = resolve_project_path(args.catalog)
    out_path = resolve_project_path(args.out)

    if bool(args.in_csv) == bool(args.in_json):
        raise SystemExit("Provide exactly one of --in-csv or --in-json.")

    destinations = load_destinations(catalog_path)
    known = {d.id for d in destinations}

    if args.in_csv:
        incoming = import_from_csv(resolve_project_path(args.in_csv), id_field=args.id_field)
    else:
        incoming = import_from_json(resolve_project_path(args.in_json), id_field=args.id_field)

    missing = sorted([k for k in incoming.keys() if k not in known])
    if missing:
        print(f"Warning: {len(missing)} ids not found in catalog (ignored). Example:", ", ".join(missing[:8]))
        incoming = {k: v for k, v in incoming.items() if k in known}

    existing = None
    if out_path.exists():
        try:
            existing_payload = _read_json(out_path)
            existing = existing_payload if isinstance(existing_payload, dict) else None
        except Exception:
            existing = None

    merged = merge_details(existing=existing, incoming=incoming, mode=args.merge)
    _write_json(out_path, merged)

    print("Wrote:", out_path)
    print("Entries:", len(merged))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

