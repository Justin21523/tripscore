# Repository Guidelines

## Overview
TripScore is an explainable, rule-based destination scoring and recommendation system (Python). It ingests transport + weather signals, scores candidate destinations, and returns Top‑N recommendations with a score breakdown.

## Project Structure & Module Organization
- `src/tripscore/` — application package (pipeline: `ingestion → features → scoring → recommender → api → web`).
- `data/catalogs/destinations.json` — destination catalog (sample Taipei POIs with `tags`).
- `tests/` — pytest suite (prefer offline tests; stub external calls).
- `.cache/tripscore/` — local API cache (ignored by Git).

Keep modules small and single-purpose; avoid mixing ingestion with scoring.

## Build, Test, and Development Commands
- Install: `pip install -r requirements.txt` (or `requirements-dev.txt` for dev tooling).
- CLI demo: `PYTHONPATH=src python -m tripscore.cli recommend --origin-lat ... --origin-lon ... --start ... --end ...`
- API + Web: `PYTHONPATH=src uvicorn tripscore.api.app:app --reload --port 8000` then open `http://127.0.0.1:8000/`
- Tests: `PYTHONPATH=src pytest -q`
- Lint: `PYTHONPATH=src ruff check src tests`

## Coding Style & Naming Conventions
- Python: prefer small pure functions; keep business logic in `features/` and `scoring/`.
- Language: keep code, comments, docs, and commit messages in English.
- Configuration: weights/thresholds live in `src/tripscore/config/defaults.yaml` (no hard-coded tuning constants).
- Naming: `snake_case` for Python, `kebab-case` for data file names, and lower-case `tags` (e.g., `family_friendly`).

## Testing Guidelines
- Use pytest. Tests must not rely on network; inject stub clients into `recommend(...)` where needed.
- Prefer smoke tests that validate ranking + explanations over brittle numeric snapshots.

## Commit & Pull Request Guidelines
This repo may not include Git history yet; use Conventional Commits (e.g., `feat(scoring): add weather penalty curve`) and keep PRs small and reviewable.

PRs should include: a clear description, how to test locally, and screenshots/CLI output for user-visible changes. Link related issues when applicable.

## Security & Configuration Tips
Never commit secrets. TDX uses `TDX_CLIENT_ID` / `TDX_CLIENT_SECRET` via `.env` (see `.env.example`).
