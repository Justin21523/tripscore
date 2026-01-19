# src/tripscore/api/app.py
"""
FastAPI application wiring.

This file creates the `FastAPI` instance, mounts static assets, and serves the web UI.
Business logic lives in `tripscore.api.routes` and `tripscore.recommender`.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from tripscore.core.logging import configure_logging

from .routes import router
from .tdx_prefetch import router as tdx_prefetch_router

configure_logging()

app = FastAPI(title="TripScore API", version="0.1.0")
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
