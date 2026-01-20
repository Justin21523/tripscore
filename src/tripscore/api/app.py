# src/tripscore/api/app.py
"""
FastAPI application wiring.

This file creates the `FastAPI` instance, mounts static assets, and serves the web UI.
Business logic lives in `tripscore.api.routes` and `tripscore.recommender`.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.cors import CORSMiddleware

from tripscore.core.logging import configure_logging

from .routes import router
from .tdx_prefetch import router as tdx_prefetch_router

configure_logging()

app = FastAPI(title="TripScore API", version="0.1.0")

# CORS (dev-friendly): allow local frontends (e.g. http://localhost:8003) to call this API.
# Configure via env:
# - TRIPSCORE_CORS_ORIGINS="http://localhost:8003,http://127.0.0.1:8003"
# - TRIPSCORE_CORS_ALLOW_LOCAL=0 to disable the default localhost allowance
cors_origins = [s.strip() for s in os.getenv("TRIPSCORE_CORS_ORIGINS", "").split(",") if s.strip()]
cors_allow_local = os.getenv("TRIPSCORE_CORS_ALLOW_LOCAL", "1").strip().lower() in {"1", "true", "yes", "y"}
cors_origin_regex = os.getenv("TRIPSCORE_CORS_ALLOW_ORIGIN_REGEX", "").strip() or (
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$" if cors_allow_local and not cors_origins else ""
)
if cors_origins or cors_origin_regex:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_origin_regex=cors_origin_regex or None,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

app.include_router(router)
app.include_router(tdx_prefetch_router)

BASE_DIR = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = BASE_DIR / "web" / "templates"
STATIC_DIR = BASE_DIR / "web" / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", response_class=HTMLResponse)
def index(request: Request) -> HTMLResponse:
    """Serve the minimal web UI (single-page app)."""
    return templates.TemplateResponse("index.html", {"request": request})


@app.get("/{path:path}", response_class=HTMLResponse, include_in_schema=False)
def spa_fallback(request: Request, path: str) -> HTMLResponse:
    """Serve SPA shell for deep links (non-API, non-static paths)."""
    if path.startswith("api") or path.startswith("static") or path in {"openapi.json", "docs", "redoc", "config"}:
        return HTMLResponse(status_code=404, content="Not Found")
    return templates.TemplateResponse("index.html", {"request": request})
