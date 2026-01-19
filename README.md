# TripScore

TripScore is a rule-based, explainable destination scoring and recommendation system.

Phase 1 (MVP) includes:
- Destination catalog (sample Taipei POIs)
- TDX-based accessibility proxy (bus + metro stations + YouBike last-mile)
- Origin-aware proximity signal (distance-based, configurable)
- Weather-based suitability (rain probability + temperature)
- Tag-based preference matching
- Context scoring (crowd risk + family-friendly)
- Optional congestion proxy via parking availability (TDX, if supported)
- Explainable composite score
- CLI demo, REST API, and a minimal web UI

## Architecture

Pipeline modules follow: `ingestion → features → scoring → recommender → api → web`.

Key paths:
- `data/catalogs/destinations.json` — sample destination catalog (30+ points)
- `src/tripscore/config/defaults.yaml` — all weights/thresholds/time granularity
- `src/tripscore/ingestion/tdx_client.py` — TDX bus stop ingestion
- `src/tripscore/ingestion/weather_client.py` — Open-Meteo ingestion
- `src/tripscore/recommender/recommend.py` — orchestration + ranking

## Quickstart

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` from `.env.example` and set `TDX_CLIENT_ID` / `TDX_CLIENT_SECRET` to enable TDX ingestion.

Load it into your environment (bash/zsh):

```bash
cp .env.example .env
# edit .env, then:
set -a
source .env
set +a
```

## Notebooks

Notebook-first walkthroughs live in `notebooks/` (offline-friendly with stub clients).

```bash
pip install -r requirements-notebooks.txt
jupyter lab
```

## POI details (opening hours, address, phone)

TripScore can optionally merge extra POI metadata from a local details file:

- Config: `catalog.details_path` (default: `data/catalogs/destination_details.json`)
- Format: JSON object mapping `destination_id -> {address, phone, opening_hours, url, description, city, district}`

Tools:

- Import from CSV/JSON: `PYTHONPATH=src python scripts/poi_details_import.py --in-csv your.csv`
- Open-data enrichment (OSM/Nominatim, network + polite rate limits): `PYTHONPATH=src python scripts/poi_details_enrich_osm.py --only-missing`

## Run (CLI)

```bash
PYTHONPATH=src python -m tripscore.cli recommend \
  --origin-lat 25.0478 --origin-lon 121.5170 \
  --start 2026-01-05T10:00+08:00 --end 2026-01-05T18:00+08:00
```

## Run (API + Web)

```bash
PYTHONPATH=src uvicorn tripscore.api.app:app --reload --port 8000 --env-file .env
```

Then open `http://127.0.0.1:8000/`.

## Docker (always-on ingestion)

This repo supports an always-on, rate-limited ingestion setup via Docker Compose:

```bash
cp .env.example .env
# edit .env: set TDX_CLIENT_ID / TDX_CLIENT_SECRET
docker compose up -d --build
```

Notes:
- Compose uses `restart: unless-stopped`, so services come back after reboot if your Docker daemon starts on login.
- Runtime knobs live in `.env` (see `.env.example`). If you still see frequent `429 Too Many Requests`, increase `TRIPSCORE_TDX_REQUEST_SPACING_SECONDS` and/or `TRIPSCORE_TDX_DAEMON_SLEEP_SECONDS`.
- Compose enables Docker log rotation (`json-file` with `max-size`/`max-file`) to avoid unbounded disk growth.

Services:
- `tripscore-api`: web UI + API on `:${TRIPSCORE_WEB_PORT:-8001}` (host) → `:8000` (container)
- `tripscore-tdx-daemon`: background ingestion loop (bulk static datasets + continuous availability refresh)

Useful checks:

```bash
docker compose ps
docker compose logs -f tdx-daemon
curl -s http://127.0.0.1:${TRIPSCORE_WEB_PORT:-8001}/api/quality/report | jq .
curl -s http://127.0.0.1:${TRIPSCORE_WEB_PORT:-8001}/api/tdx/status | jq .
```

Data persistence:
- cache persists in `./.cache/tripscore/` (mounted into containers)
- catalog/details persist in `./data/` (mounted into containers)

If you see `PermissionError` writing to `./.cache/tripscore`, set:
- `TRIPSCORE_DOCKER_UID` / `TRIPSCORE_DOCKER_GID` in `.env` to match `id -u` / `id -g`

## POI Details Import (local file)

For fields like `opening_hours`, `address`, and `url`, use the local details file at `data/catalogs/destination_details.json`.

```bash
PYTHONPATH=src python scripts/poi_details_import.py --in-csv your_details.csv --id-field id
PYTHONPATH=src python scripts/poi_details_validate.py
```

## Parking Details Import (local file)

If a city’s TDX parking schema is missing `service_time` / `fare_description`, you can enrich locally via:

```bash
PYTHONPATH=src python scripts/parking_details_import.py --in-csv your_parking_details.csv --id-field parking_lot_uid
```

This writes `data/catalogs/parking_details.json` (merged by `ParkingLotUID`).

## Catalog Import (open data)

To add new destinations from local open data (CSV/JSON), use:

```bash
PYTHONPATH=src python scripts/catalog_import.py --in-csv your_pois.csv
```

## Always-On (Linux)

Optional systemd user service example: `docs/systemd/README.md:1`.

## Cache Management

If disk usage grows too much, you can safely remove bulk artifacts and let the daemon rebuild them:

```bash
rm -rf .cache/tripscore/tdx_bulk
rm -rf .cache/tripscore/tdx_daemon
```

## Web Controls (Editor Mode)
- Move origin: drag the origin marker, enable “Pick origin” then click the map, or use the D‑pad / <kbd>W</kbd><kbd>A</kbd><kbd>S</kbd><kbd>D</kbd>.
- Rotate heading: <kbd>Q</kbd>/<kbd>E</kbd> (visual indicator; does not affect scoring yet).
- Run: <kbd>Ctrl</kbd>+<kbd>Enter</kbd> (or click **Run**). Auto-run can re-score after changes (debounced).
- Inspect: click a result (or marker); press <kbd>1</kbd>…<kbd>9</kbd> to select by rank.
- Presets: built-in presets come from config; custom presets are stored in `localStorage`.

## Presets
- List available presets: `GET /api/presets`
- CLI usage: `PYTHONPATH=src python -m tripscore.cli recommend --preset explore_city ...`

## Per-request Tuning Overrides
The API accepts optional `settings_overrides` on the request body to override config for a single recommendation run.
- Web UI: “Advanced Tuning (per request)” and “Expert” panels.
- Allowed keys (server-validated): `features.*`, `scoring.*`, `ingestion.tdx.accessibility.*`, and selected `ingestion.weather.*` scoring knobs.
- Defaults for the UI: `GET /api/settings` (secrets are redacted).

## Configuration
- Update `src/tripscore/config/defaults.yaml` to tune weights, radii, and scoring thresholds.
- Accessibility blends origin proximity with local transit using `ingestion.tdx.accessibility.blend_weights`.
- Local transit can combine bus/metro/bike signals via `ingestion.tdx.accessibility.local_transit_signal_weights`.
- Context scoring uses `features.context.*` (district baselines + time-window heuristics).
- If parking datasets are available for the city, `features.parking.*` and `features.context.crowd.parking_risk_weight` blend parking availability into crowd risk.
- Use cache to reduce API calls (default: `.cache/tripscore/`).

## Dev

```bash
pip install -r requirements-dev.txt
PYTHONPATH=src pytest -q
PYTHONPATH=src ruff check src tests
```
