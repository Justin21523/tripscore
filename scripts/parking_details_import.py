from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Any

from tripscore.core.env import resolve_project_path


FIELDS = ["address", "service_time", "fare_description", "total_spaces"]


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _normalize_row(d: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k in FIELDS:
        v = d.get(k)
        if v is None:
            continue
        if k == "total_spaces":
            try:
                out[k] = int(v)
            except Exception:
                continue
            continue
        s = str(v).strip()
        if s:
            out[k] = s
    return out


def import_from_csv(path: Path, *, id_field: str) -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for raw in reader:
            if not raw:
                continue
            uid = str(raw.get(id_field) or "").strip()
            if not uid:
                continue
            rows[uid] = _normalize_row(raw)
    return rows


def import_from_json(path: Path, *, id_field: str) -> dict[str, dict[str, Any]]:
    payload = _read_json(path)
    out: dict[str, dict[str, Any]] = {}

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
            uid = str(row.get(id_field) or "").strip()
            if not uid:
                continue
            out[uid] = _normalize_row(row)
        return out

    raise ValueError("Unsupported JSON shape: expected object or array.")


def merge_details(
    *,
    existing: dict[str, dict[str, Any]] | None,
    incoming: dict[str, dict[str, Any]],
    mode: str,
) -> dict[str, dict[str, Any]]:
    base: dict[str, dict[str, Any]] = {k: dict(v) for k, v in (existing or {}).items() if isinstance(v, dict)}

    for uid, fields in incoming.items():
        cur = base.get(uid, {})
        if mode == "overwrite":
            cur = {**cur, **fields}
        else:
            for k, v in fields.items():
                if k not in cur or cur.get(k) in (None, "", 0):
                    cur[k] = v
        base[uid] = cur

    cleaned: dict[str, dict[str, Any]] = {}
    for uid, fields in base.items():
        if not isinstance(fields, dict):
            continue
        out: dict[str, Any] = {}
        for k in FIELDS:
            v = fields.get(k)
            if k == "total_spaces" and isinstance(v, int):
                out[k] = v
            elif isinstance(v, str) and v.strip():
                out[k] = v.strip()
        if out:
            cleaned[uid] = out
    return cleaned


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Build/merge a local parking details file for TripScore (offline).")
    p.add_argument("--out", type=str, default="data/catalogs/parking_details.json")
    p.add_argument("--in-csv", type=str, default=None)
    p.add_argument("--in-json", type=str, default=None)
    p.add_argument("--id-field", type=str, default="parking_lot_uid", help="ParkingLotUID column/key for input rows.")
    p.add_argument("--merge", choices=["keep-existing", "overwrite"], default="keep-existing")
    args = p.parse_args(argv)

    out_path = resolve_project_path(args.out)

    if bool(args.in_csv) == bool(args.in_json):
        raise SystemExit("Provide exactly one of --in-csv or --in-json.")

    if args.in_csv:
        incoming = import_from_csv(resolve_project_path(args.in_csv), id_field=args.id_field)
    else:
        incoming = import_from_json(resolve_project_path(args.in_json), id_field=args.id_field)

    existing = None
    if out_path.exists():
        try:
            payload = _read_json(out_path)
            existing = payload if isinstance(payload, dict) else None
        except Exception:
            existing = None

    merged = merge_details(existing=existing, incoming=incoming, mode=args.merge)
    _write_json(out_path, merged)

    print("Wrote:", out_path)
    print("Entries:", len(merged))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

