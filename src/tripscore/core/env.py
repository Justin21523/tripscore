"""
Environment + project-root helpers.

Problems this module solves:
- Developers often store credentials in a repo-local `.env` file.
- Running via notebooks/uvicorn/CLI from different working directories can cause:
  - env vars not loaded
  - relative paths (e.g., `data/...`) resolving incorrectly

This module provides:
- `load_dotenv_if_present()`: best-effort `.env` loading (does not override existing env vars)
- `get_project_root()`: find the repo root (prefers `.env` / `.git`, falls back to marker dirs)
- `resolve_project_path()`: resolve relative paths against the project root
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


def _iter_parents(start: Path) -> list[Path]:
    start = start.resolve()
    return [start, *list(start.parents)]


def _looks_like_project_root(path: Path) -> bool:
    # Heuristics: prefer explicit markers if available.
    if (path / ".env").is_file():
        return True
    if (path / ".git").exists():
        return True
    # Fallback marker dirs for this repo layout.
    return (path / "src").is_dir() and (path / "data").is_dir()


@lru_cache
def get_project_root() -> Path:
    """Return the best-guess project root directory (cached)."""
    # Explicit override (useful in CI or when running from elsewhere).
    override = os.getenv("TRIPSCORE_PROJECT_ROOT")
    if override:
        return Path(override).expanduser().resolve()

    # If the user points directly to an env file, treat its parent as root.
    env_file = os.getenv("TRIPSCORE_ENV_FILE")
    if env_file:
        p = Path(env_file).expanduser().resolve()
        return p.parent

    # Search from CWD upwards first (common case: running in the repo).
    for candidate in _iter_parents(Path.cwd()):
        if _looks_like_project_root(candidate):
            return candidate

    # If we're running from outside the repo (e.g., notebooks launched elsewhere),
    # also search upwards from this module's location.
    try:
        here = Path(__file__).resolve()
        for candidate in _iter_parents(here.parent):
            if _looks_like_project_root(candidate):
                return candidate
    except Exception:
        pass

    # As a last resort, fall back to CWD.
    return Path.cwd().resolve()


@lru_cache
def load_dotenv_if_present() -> Path | None:
    """Load `.env` once if present; returns the loaded env path (or None).

    This is best-effort:
    - If `python-dotenv` isn't installed, it does nothing.
    - It never overrides env vars already set in the process environment.
    """
    try:
        from dotenv import load_dotenv
    except Exception:
        return None

    # Respect explicit env file path if provided.
    explicit = os.getenv("TRIPSCORE_ENV_FILE")
    if explicit:
        env_path = Path(explicit).expanduser().resolve()
        if env_path.is_file():
            load_dotenv(dotenv_path=env_path, override=False)
            return env_path
        return None

    root = get_project_root()
    env_path = root / ".env"
    if env_path.is_file():
        load_dotenv(dotenv_path=env_path, override=False)
        return env_path
    return None


def resolve_project_path(path: str | Path) -> Path:
    """Resolve a possibly-relative path against the project root."""
    p = Path(path).expanduser()
    if p.is_absolute():
        return p
    return (get_project_root() / p).resolve()
