from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from tripscore.catalog.loader import load_destinations
from tripscore.core.env import resolve_project_path


ALLOWED_FIELDS = {"address", "phone", "opening_hours", "url", "description", "city", "district"}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Validate TripScore POI details file (offline).")
    p.add_argument("--catalog", type=str, default="data/catalogs/destinations.json")
    p.add_argument("--details", type=str, default="data/catalogs/destination_details.json")
    args = p.parse_args(argv)

    catalog_path = resolve_project_path(args.catalog)
    details_path = resolve_project_path(args.details)

    destinations = load_destinations(catalog_path)
    known = {d.id for d in destinations}

    if not details_path.exists():
        print("Details file not found:", details_path)
        return 2

    payload = _read_json(details_path)
    if not isinstance(payload, dict):
        print("Invalid details shape: expected object mapping id -> details dict.")
        return 2

    unknown_ids = []
    bad_rows = []
    unknown_fields = []
    empty_rows = 0

    for dest_id, fields in payload.items():
        if not isinstance(dest_id, str) or not dest_id.strip():
            bad_rows.append(str(dest_id))
            continue
        if dest_id not in known:
            unknown_ids.append(dest_id)
        if not isinstance(fields, dict):
            bad_rows.append(dest_id)
            continue
        present = 0
        for k, v in fields.items():
            if k not in ALLOWED_FIELDS:
                unknown_fields.append(f"{dest_id}:{k}")
                continue
            if isinstance(v, str) and v.strip():
                present += 1
        if present == 0:
            empty_rows += 1

    print("Catalog:", catalog_path)
    print("Destinations:", len(known))
    print("Details:", details_path)
    print("Detail entries:", len(payload))
    print("Empty detail entries:", empty_rows)
    if unknown_ids:
        print("Unknown ids (not in catalog):", len(unknown_ids), "example:", ", ".join(sorted(unknown_ids)[:8]))
    if bad_rows:
        print("Invalid detail rows:", len(bad_rows), "example:", ", ".join(bad_rows[:8]))
    if unknown_fields:
        print("Unknown fields:", len(unknown_fields), "example:", ", ".join(unknown_fields[:8]))

    if bad_rows:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

