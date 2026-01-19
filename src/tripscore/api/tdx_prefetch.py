"""
TDX bulk-prefetch job API.

This exposes a small "control plane" for gradual, resumable ingestion:
- start a background prefetch job
- poll job status + bulk progress
- cancel a running job

Jobs persist state on disk under the cache directory so the UI can survive reloads.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from tripscore.config.settings import get_settings
from tripscore.core.cache import FileCache
from tripscore.core.env import resolve_project_path
from tripscore.ingestion.tdx_bulk import DatasetName, bulk_prefetch_all, read_bulk_progress
from tripscore.ingestion.tdx_client import TdxClient


router = APIRouter()


DEFAULT_DATASETS: list[DatasetName] = [
    "bus_stops",
    "bus_routes",
    "bike_stations",
    "bike_availability",
    "metro_stations",
    "parking_lots",
    "parking_availability",
]


class TdxPrefetchRequest(BaseModel):
    city: str | None = None
    datasets: list[DatasetName] = Field(default_factory=lambda: list(DEFAULT_DATASETS))
    reset: bool = False
    sleep_seconds: float = 2.0
    datasets_per_run: int = 0


class TdxPrefetchResponse(BaseModel):
    job_id: str
    status: str


@dataclass(frozen=True)
class _JobPaths:
    job_path: Path
    cancel_path: Path


def _cache() -> FileCache:
    settings = get_settings()
    return FileCache(
        resolve_project_path(settings.cache.dir),
        enabled=settings.cache.enabled,
        default_ttl_seconds=settings.cache.default_ttl_seconds,
    )


def _jobs_dir(cache: FileCache) -> Path:
    return cache.base_dir / "tdx_jobs"


def _global_lock_path(cache: FileCache) -> Path:
    return _jobs_dir(cache) / "global.lock"


def _try_acquire_global_lock(cache: FileCache, *, job_id: str, stale_after_seconds: int = 6 * 60 * 60) -> bool:
    """Best-effort global lock to avoid concurrent TDX prefetch bursts."""
    lock = _global_lock_path(cache)
    lock.parent.mkdir(parents=True, exist_ok=True)
    if lock.exists():
        try:
            age = int(time.time() - int(lock.stat().st_mtime))
        except Exception:
            age = 0
        if age > int(stale_after_seconds):
            try:
                lock.unlink()
            except Exception:
                return False
        else:
            return False
    try:
        lock.write_text(job_id, encoding="utf-8")
        return True
    except Exception:
        return False


def _release_global_lock(cache: FileCache, *, job_id: str) -> None:
    lock = _global_lock_path(cache)
    if not lock.exists():
        return
    try:
        owner = lock.read_text(encoding="utf-8").strip()
    except Exception:
        owner = ""
    if owner and owner != job_id:
        return
    try:
        lock.unlink()
    except Exception:
        return

def _job_paths(cache: FileCache, job_id: str) -> _JobPaths:
    base = _jobs_dir(cache)
    return _JobPaths(job_path=base / f"{job_id}.job.json", cancel_path=base / f"{job_id}.cancel")


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _expected_scopes(*, city: str, operators: list[str]) -> list[tuple[DatasetName, str]]:
    pairs: list[tuple[DatasetName, str]] = [
        ("bus_stops", f"city_{city}"),
        ("bus_routes", f"city_{city}"),
        ("bike_stations", f"city_{city}"),
        ("bike_availability", f"city_{city}"),
        ("parking_lots", f"city_{city}"),
        ("parking_availability", f"city_{city}"),
    ]
    for op in operators:
        pairs.append(("metro_stations", f"operator_{op}"))
    return pairs


def _overall_progress(*, cache: FileCache, city: str, operators: list[str]) -> dict[str, Any]:
    rows = []
    done = True
    for dataset, scope in _expected_scopes(city=city, operators=operators):
        p = read_bulk_progress(cache, dataset, scope) or {}
        is_done = bool(p.get("done", False))
        done = done and is_done
        rows.append(
            {
                "dataset": dataset,
                "scope": scope,
                "done": is_done,
                "next_skip": p.get("next_skip"),
                "top": p.get("top"),
                "updated_at_unix": p.get("updated_at_unix"),
                "error_status": p.get("error_status"),
            }
        )
    return {
        "done": bool(done),
        "done_count": sum(1 for r in rows if r["done"]),
        "total_count": len(rows),
        "items": rows,
    }


def _summarize_results(results) -> list[dict[str, Any]]:
    out = []
    for r in results:
        out.append(
            {
                "dataset": r.dataset,
                "scope": r.scope,
                "pages_fetched": r.pages_fetched,
                "items_added": r.items_added,
                "total_items": r.total_items,
                "next_skip": r.next_skip,
                "done": bool(r.done),
            }
        )
    return out


_threads: dict[str, threading.Thread] = {}
_threads_lock = threading.Lock()


def _run_job(job_id: str) -> None:
    settings = get_settings()
    cache = _cache()
    paths = _job_paths(cache, job_id)
    job = _read_json(paths.job_path)
    if not job:
        return

    if not _try_acquire_global_lock(cache, job_id=job_id):
        job.update(
            {
                "status": "blocked",
                "updated_at_unix": int(time.time()),
                "last_error": {"type": "JOB_LOCKED", "message": "Another TDX prefetch job is running."},
            }
        )
        _write_json(paths.job_path, job)
        return

    city = str(job.get("city") or settings.ingestion.tdx.city)
    datasets = job.get("datasets") or list(DEFAULT_DATASETS)
    datasets_per_run = int(job.get("datasets_per_run") or 0)
    sleep_seconds = float(job.get("sleep_seconds") or 2.0)
    reset = bool(job.get("reset") or False)

    tdx = TdxClient(settings, cache)
    operators = list(settings.ingestion.tdx.metro_stations.operators)

    job.update(
        {
            "status": "running",
            "started_at_unix": job.get("started_at_unix") or int(time.time()),
            "updated_at_unix": int(time.time()),
            "city": city,
        }
    )
    _write_json(paths.job_path, job)

    run = int(job.get("runs") or 0)
    offset = int(job.get("dataset_offset") or 0)

    while True:
        if paths.cancel_path.exists() or bool(_read_json(paths.job_path).get("cancel_requested", False)):
            job = _read_json(paths.job_path)
            job.update({"status": "canceled", "ended_at_unix": int(time.time()), "updated_at_unix": int(time.time())})
            _write_json(paths.job_path, job)
            _release_global_lock(cache, job_id=job_id)
            return

        prog = _overall_progress(cache=cache, city=city, operators=operators)
        if prog["done"]:
            job = _read_json(paths.job_path)
            job.update(
                {
                    "status": "completed",
                    "ended_at_unix": int(time.time()),
                    "updated_at_unix": int(time.time()),
                    "progress": prog,
                }
            )
            _write_json(paths.job_path, job)
            _release_global_lock(cache, job_id=job_id)
            return

        run += 1
        try:
            if datasets_per_run > 0 and datasets:
                start = offset % len(datasets)
                ds_chunk = datasets[start : start + datasets_per_run]
                if len(ds_chunk) < datasets_per_run:
                    ds_chunk = ds_chunk + datasets[0 : datasets_per_run - len(ds_chunk)]
                offset += max(1, datasets_per_run)
            else:
                ds_chunk = datasets

            results = bulk_prefetch_all(
                tdx_client=tdx,
                cache=cache,
                city=city,
                datasets=list(ds_chunk),
                max_pages_per_dataset=int(settings.ingestion.tdx.bulk.max_pages_per_call),
                max_seconds_total=(
                    float(settings.ingestion.tdx.bulk.max_seconds_per_call)
                    if settings.ingestion.tdx.bulk.max_seconds_per_call is not None
                    else None
                ),
                reset=reset if run == 1 else False,
            )
            prog = _overall_progress(cache=cache, city=city, operators=operators)
            job = _read_json(paths.job_path)
            job.update(
                {
                    "status": "running",
                    "runs": int(run),
                    "dataset_offset": int(offset),
                    "last_run_at_unix": int(time.time()),
                    "updated_at_unix": int(time.time()),
                    "last_results": _summarize_results(results),
                    "progress": prog,
                    "last_error": None,
                }
            )
            _write_json(paths.job_path, job)
        except Exception as exc:
            job = _read_json(paths.job_path)
            job.update(
                {
                    "status": "running",
                    "runs": int(run),
                    "dataset_offset": int(offset),
                    "last_run_at_unix": int(time.time()),
                    "updated_at_unix": int(time.time()),
                    "last_error": {"type": type(exc).__name__, "message": str(exc)},
                }
            )
            _write_json(paths.job_path, job)

        time.sleep(max(0.0, sleep_seconds))


def _job_id_for(req: TdxPrefetchRequest, *, city: str) -> str:
    payload = {
        "city": city,
        "datasets": req.datasets,
        "reset": bool(req.reset),
        "sleep_seconds": float(req.sleep_seconds),
        "datasets_per_run": int(req.datasets_per_run),
    }
    digest = sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()[:12]
    # Make it unique so multiple runs can coexist even with identical configs.
    return f"{digest}-{uuid.uuid4().hex[:8]}"


@router.post("/api/tdx/prefetch", response_model=TdxPrefetchResponse)
def start_tdx_prefetch(req: TdxPrefetchRequest) -> TdxPrefetchResponse:
    settings = get_settings()
    cache = _cache()
    city = str(req.city or settings.ingestion.tdx.city)

    job_id = _job_id_for(req, city=city)
    paths = _job_paths(cache, job_id)
    if paths.job_path.exists():
        raise HTTPException(status_code=409, detail={"code": "JOB_EXISTS", "message": "Job already exists."})

    if _global_lock_path(cache).exists():
        raise HTTPException(
            status_code=409,
            detail={"code": "JOB_LOCKED", "message": "Another TDX prefetch job is running. Cancel it or wait."},
        )

    job = {
        "job_id": job_id,
        "status": "queued",
        "created_at_unix": int(time.time()),
        "updated_at_unix": int(time.time()),
        "city": city,
        "datasets": list(req.datasets or DEFAULT_DATASETS),
        "reset": bool(req.reset),
        "sleep_seconds": float(req.sleep_seconds),
        "datasets_per_run": int(req.datasets_per_run),
        "runs": 0,
        "dataset_offset": 0,
        "cancel_requested": False,
        "last_results": [],
        "progress": None,
        "last_error": None,
    }
    _write_json(paths.job_path, job)
    if paths.cancel_path.exists():
        paths.cancel_path.unlink()

    t = threading.Thread(target=_run_job, args=(job_id,), daemon=True)
    with _threads_lock:
        _threads[job_id] = t
    t.start()
    return TdxPrefetchResponse(job_id=job_id, status="queued")


@router.get("/api/tdx/prefetch")
def list_tdx_prefetch_jobs() -> dict:
    cache = _cache()
    base = _jobs_dir(cache)
    if not base.exists():
        return {"jobs": []}
    jobs = []
    for p in sorted(base.glob("*.job.json")):
        job = _read_json(p)
        if not job:
            continue
        jobs.append(
            {
                "job_id": job.get("job_id"),
                "status": job.get("status"),
                "city": job.get("city"),
                "created_at_unix": job.get("created_at_unix"),
                "updated_at_unix": job.get("updated_at_unix"),
                "runs": job.get("runs"),
            }
        )
    jobs.sort(key=lambda j: int(j.get("created_at_unix") or 0), reverse=True)
    return {"jobs": jobs}


@router.get("/api/tdx/prefetch/{job_id}")
def get_tdx_prefetch_job(job_id: str) -> dict:
    cache = _cache()
    paths = _job_paths(cache, job_id)
    job = _read_json(paths.job_path)
    if not job:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found."})
    job["cancel_file_present"] = bool(paths.cancel_path.exists())
    return job


@router.post("/api/tdx/prefetch/{job_id}/cancel")
def cancel_tdx_prefetch_job(job_id: str) -> dict:
    cache = _cache()
    paths = _job_paths(cache, job_id)
    job = _read_json(paths.job_path)
    if not job:
        raise HTTPException(status_code=404, detail={"code": "JOB_NOT_FOUND", "message": "Job not found."})
    job["cancel_requested"] = True
    job["updated_at_unix"] = int(time.time())
    _write_json(paths.job_path, job)
    paths.cancel_path.parent.mkdir(parents=True, exist_ok=True)
    paths.cancel_path.write_text("cancel", encoding="utf-8")
    return {"job_id": job_id, "status": "cancel_requested"}
