"""
api.py — stats + control API and the dashboard page.

    uvicorn backend.cloud.api:app --host 0.0.0.0 --port 8000

GET  /                    dashboard (static/index.html)
GET  /api/cloud/stats     the dashboard contract (keys are fixed)
POST /api/cloud/storm     {n, critical, critical_after_s, policy}
POST /api/cloud/critical  {k}
POST /api/cloud/reset     FLUSHDB

Cheap by design: one pipeline of O(1) reads + ONE MGET over the last 500
results (taken from the `recent` list). No SCAN/KEYS.
"""

from __future__ import annotations

import json
import math
import re
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, Field
from redis.exceptions import RedisError

from backend.app.profiles import load_all_tracked_objects
from backend.cloud import timeline as _timeline
from backend.cloud.domain import get_domain
from backend.cloud.events import make_critical_job, pool_summary, random_job
from backend.cloud.jobs import process_job
from backend.cloud.queue import Queue
from backend.cloud.resilience import retry
from backend.cloud.trace import Tracer

WINDOW = 500          # results used for latency stats
ALIVE_BELOW_S = 10.0  # heartbeat age that counts as alive
THROUGHPUT_S = 5      # throughput = completions in the last 5 s / 5
STATIC = Path(__file__).parent / "static"

app = FastAPI(title="Aegis cloud")
# The React app (Vite dev server, :5173 or the next free port) calls this API cross-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"^http://(localhost|127\.0\.0\.1)(:\d+)?$",
    allow_methods=["*"],
    allow_headers=["*"],
)
q = Queue()
tracer = Tracer(q.r, q.ns)
POOL = pool_summary(load_all_tracked_objects())   # loaded once at start-up (read-only, no network)


@app.exception_handler(RedisError)
def _redis_down(request, exc):
    # Any Redis failure (down, restarting, loading) -> a clean 503 the UI turns into "CLUSTER LINK DOWN".
    # The next request reconnects by itself; nothing to restart.
    return JSONResponse({"error": "redis_unavailable", "detail": str(exc)}, status_code=503)


def _iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _latency(results: list[dict]) -> dict:
    ms = sorted((_iso(r["finished_at"]) - _iso(r["enqueued_at"])).total_seconds() * 1000 for r in results)
    if not ms:
        return {"avg": 0, "p95": 0, "p99": 0}
    pct = lambda p: ms[max(0, math.ceil(p / 100 * len(ms)) - 1)]  # nearest-rank
    return {"avg": round(statistics.mean(ms)), "p95": round(pct(95)), "p99": round(pct(99))}


@app.get("/")
def dashboard() -> FileResponse:
    return FileResponse(STATIC / "index.html")


@app.get("/api/cloud/recent")
def recent(limit: int = Query(10, ge=1, le=30)) -> list[dict]:
    """Last `limit` finished jobs with attempts count + up to 5 retried jobs pinned."""
    return q.recent_summaries_ext(limit)


_EVENT_ID = re.compile(r"^[0-9a-f-]{36}$")


@app.get("/api/cloud/resolution/{event_id}")
def resolution(event_id: str):
    """Full transcript + resolution of one finished job; 404 JSON once its 2 h TTL has expired."""
    record = q.get_record(event_id) if _EVENT_ID.match(event_id) else None
    if record is None:
        return JSONResponse({"error": "not_found", "detail": "transcript expired or unknown event id"}, status_code=404)
    return {"event_id": event_id, **record}


@app.get("/api/cloud/timeline")
def cloud_timeline(since_seq: int = Query(0, ge=0), limit: int = Query(200, ge=1, le=1000)) -> dict:
    """Return timeline events in ascending seq order. Use since_seq for incremental polling."""
    events = _timeline.get_events(q.r, since_seq=since_seq, limit=limit)
    last_seq = events[-1]["seq"] if events else since_seq
    return {"events": events, "last_seq": last_seq}


@app.get("/api/cloud/integrity")
def cloud_integrity() -> dict:
    """Queue integrity check: enqueued_total vs completed+failed+queued+processing."""
    counters, depth, processing = q.r.pipeline().hgetall(q.k("counters")).zcard(q.k("jobs")).hlen(q.k("processing")).execute()
    enqueued = int(counters.get("enqueued", 0))
    completed = int(counters.get("completed", 0))
    failed = int(counters.get("failed", 0))
    accounted = completed + failed + depth + processing
    leaked = enqueued - accounted
    return {
        "enqueued_total": enqueued,
        "completed_total": completed,
        "failed_total": failed,
        "queue_depth": depth,
        "processing": processing,
        "accounted": accounted,
        "leaked": leaked,
        "consistent": leaked == 0,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


_TIMELINE_INJECT_WHITELIST = frozenset({
    "scale_up", "scale_down", "chaos_inject", "chaos_fault", "chaos_recover",
    "chaos_redis_kill", "chaos_worker_kill",
})


class TimelineInjectReq(BaseModel):
    type: str
    source: str = "external"
    detail: dict = {}


@app.post("/api/cloud/timeline")
def timeline_inject(req: TimelineInjectReq) -> dict:
    """Inject a timeline event from an external trusted agent (autoscaler, chaos_agent). Whitelisted types only."""
    if req.type not in _TIMELINE_INJECT_WHITELIST:
        raise HTTPException(status_code=422, detail=f"type '{req.type}' not in whitelist")
    _timeline.emit(q.r, req.type, req.source, detail=req.detail)
    return {"ok": True, "type": req.type}


@app.get("/api/cloud/trace/{event_id}")
def cloud_trace(event_id: str):
    """Return the full trace for a job. 404 JSON when expired or unknown."""
    if not _EVENT_ID.match(event_id):
        return JSONResponse({"error": "not_found", "detail": "invalid event id format"}, status_code=404)
    t = tracer.get(event_id)
    if t is None:
        return JSONResponse({"error": "not_found", "detail": "trace expired or unknown event id"}, status_code=404)
    # D1: attach domain block (best-effort; None when result:<id> not yet written or expired)
    domain = None
    try:
        domain = get_domain(q.r, q.ns, event_id)
    except Exception:  # noqa: BLE001
        pass
    return {"event_id": event_id, **t, **({"domain": domain} if domain is not None else {})}


@app.get("/api/cloud/stats")
def stats() -> dict:
    pipe = q.r.pipeline()
    pipe.zcard(q.k("jobs"))
    pipe.hlen(q.k("processing"))
    pipe.hgetall(q.k("counters"))
    pipe.hgetall(q.k("workers"))
    pipe.hgetall(q.k("worker_done"))
    pipe.lrange(q.k("recent"), 0, WINDOW - 1)
    pipe.get(q.k("meta:policy"))
    pipe.time()
    depth, processing, counters, beats, done, recent_ids, policy, (sec, usec) = pipe.execute()
    now = sec + usec / 1e6  # Redis clock, same clock the heartbeats were written with

    raws = q.r.mget([q.k("result:" + i) for i in recent_ids]) if recent_ids else []
    results = [json.loads(r) for r in raws if r is not None]
    crit = [r for r in results if r.get("is_critical")]
    clat = [(_iso(r["finished_at"]) - _iso(r["enqueued_at"])).total_seconds() * 1000 for r in crit]
    cutoff = datetime.now(timezone.utc).timestamp() - THROUGHPUT_S
    recent_done = sum(_iso(r["finished_at"]).timestamp() >= cutoff for r in results)

    workers = []
    for wid in sorted(beats):
        age = now - float(beats[wid])
        workers.append({"id": wid, "last_heartbeat_age_s": round(age, 1), "alive": age < ALIVE_BELOW_S,
                        "jobs_done": int(done.get(wid, 0))})
    return {
        "queue_depth": depth,
        "processing": processing,
        "enqueued_total": int(counters.get("enqueued", 0)),
        "completed_total": int(counters.get("completed", 0)),
        "failed_total": int(counters.get("failed", 0)),
        "retries_total": int(counters.get("retries_total", 0)),
        "workers": workers,
        "throughput_per_s": round(recent_done / THROUGHPUT_S, 2),
        "latency_ms": _latency(results),
        "critical_latency_ms": {"avg": round(statistics.mean(clat)) if clat else 0,
                                "max": round(max(clat)) if clat else 0, "count": len(clat)},
        "policy": policy or os.environ.get("POLICY", "fifo"),
        "llm_mode": os.environ.get("LLM_MODE", "real"),
        "stub_ms": int(os.environ.get("LLM_STUB_MS", "50")),
        "pool_size": POOL["size"],        # real satellites the storm draws from (added; existing keys unchanged)
        "pool_groups": POOL["groups"],    # {group: count}, e.g. {"starlink": 105, ...}
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


# --- control -------------------------------------------------------------

class StormReq(BaseModel):
    n: int = Field(800, ge=1, le=5000)
    critical: int = Field(0, ge=0, le=50)
    critical_after_s: float = Field(8.0, ge=0, le=300)
    policy: Literal["fifo", "priority"] = "priority"


class CriticalReq(BaseModel):
    k: int = Field(3, ge=1, le=50)


def _current_policy() -> str:
    return q.r.get(q.k("meta:policy")) or os.environ.get("POLICY", "fifo")


def _run_storm(req: StormReq) -> None:
    """Background task: N normal jobs spread over ~2 s (so the queue visibly climbs),
    then the critical jobs `critical_after_s` after the storm started."""
    t0 = time.time()
    rng = random.Random(time.time_ns())
    objects = load_all_tracked_objects()
    jobs, seen = [], set()
    while len(jobs) < req.n:               # re-draw on the rare duplicate event_id
        j = random_job(rng, objects)
        if j.event_id not in seen:
            seen.add(j.event_id)
            jobs.append(j)
    # Background task: an HTTP error handler cannot answer for it, so every Redis call retries
    # (backoff, max 2 min) across an outage. submit() is idempotent per event (finished ids skipped,
    # re-adding a queued id only overwrites its own score), so a retried chunk cannot duplicate work.
    wait = lambda fn: retry(fn, "storm", max_wait_s=120)  # noqa: E731
    wait(lambda: q.r.set(q.k("meta:policy"), req.policy))
    try:
        _timeline.emit(q.r, "storm_started", "api",
                       detail={"n": req.n, "policy": req.policy, "critical": req.critical})
    except Exception:  # noqa: BLE001
        pass
    chunks = 20
    size = math.ceil(req.n / chunks)
    for i in range(chunks):
        wait(lambda i=i: q.submit(jobs[i * size:(i + 1) * size], req.policy))
        time.sleep(0.1)
    if req.critical:
        time.sleep(max(0.0, t0 + req.critical_after_s - time.time()))
        crit = [make_critical_job(rng, objects) for _ in range(req.critical)]
        wait(lambda: q.submit(crit, req.policy))


@app.post("/api/cloud/storm")
def storm(req: StormReq, bg: BackgroundTasks) -> dict:
    bg.add_task(_run_storm, req)
    return {"status": "started", **req.model_dump()}


@app.post("/api/cloud/critical")
def critical(req: CriticalReq) -> dict:
    rng = random.Random(time.time_ns())
    objects = load_all_tracked_objects()
    queued = q.submit([make_critical_job(rng, objects) for _ in range(req.k)], _current_policy())
    return {"status": "queued", "queued": queued, "policy": _current_policy()}


@app.post("/api/cloud/reset")
def reset() -> dict:
    q.r.flushdb()
    try:
        _timeline.emit(q.r, "reset", "api", detail={})
    except Exception:  # noqa: BLE001
        pass
    return {"status": "flushed"}


# ---------------------------------------------------------------------------
# Block 5 — FIFO vs priority race
# ---------------------------------------------------------------------------
# Namespace: results go in race:result:latest (no existing key is touched).
# The race runs jobs directly in-process (two threads, same job set, different
# ordering) so it needs no extra Docker services and never touches the main queue.
# ---------------------------------------------------------------------------

_RACE_RESULT_KEY = "race:result:latest"
_RACE_STATUS_KEY = "race:status"
_race_running = False


class RaceReq(BaseModel):
    n: int = Field(60, ge=10, le=300)
    critical: int = Field(5, ge=1, le=20)
    critical_after_s: float = Field(2.0, ge=0.0, le=60.0)


def _race_lane(policy: str, jobs: list, critical_after_idx: int) -> dict:
    """Process a list of jobs in-process, ordered by policy. Returns lane stats.
    critical_after_idx: index past which the critical jobs were inserted in the FIFO order.
    For FIFO ordering, jobs arriving before that index are processed first.
    """
    if policy == "fifo":
        # arrival order: normal jobs by enqueued_at, then critical at the end
        ordered = sorted(jobs, key=lambda j: j.enqueued_at)
    else:
        # priority order: highest risk first; ties broken by enqueued_at
        ordered = sorted(jobs, key=lambda j: (-j.risk, j.enqueued_at))

    t_lane_start = datetime.now(timezone.utc)
    t_start_perf = time.perf_counter()

    critical_pairs = []
    all_latencies: list[float] = []
    critical_latencies: list[float] = []
    cumulative_ms = 0.0

    for j in ordered:
        t0 = time.perf_counter()
        try:
            outcome = process_job(j)
        except Exception:  # noqa: BLE001
            outcome = {"resolution_status": "error", "primary_name": j.primary_id,
                       "secondary_name": j.secondary_id}
        lat = round((time.perf_counter() - t0) * 1000, 1)
        all_latencies.append(lat)

        if j.is_critical:
            critical_latencies.append(lat)
            critical_pairs.append({
                "event_id": j.event_id,
                "primary_name": outcome.get("primary_name", j.primary_id),
                "secondary_name": outcome.get("secondary_name", j.secondary_id),
                "miss_km": round(j.miss_distance_km, 3),
                "risk": round(j.risk, 3),
                "is_critical": True,
                "lead_h": j.lead_h,
                "resolution_status": outcome.get("resolution_status", ""),
                "latency_ms": lat,
                "cumulative_ms_before": round(cumulative_ms, 1),
                "lane": policy,
            })

        cumulative_ms += lat

    total_ms = round((time.perf_counter() - t_start_perf) * 1000, 1)
    # resolution_ms = cumulative wait + processing time — the true scheduling latency
    critical_resolution = [
        p["cumulative_ms_before"] + p["latency_ms"] for p in critical_pairs
    ]
    return {
        "policy": policy,
        "total_jobs": len(jobs),
        "critical_jobs": len(critical_latencies),
        "critical_latency_ms": {
            "avg": round(statistics.mean(critical_latencies)) if critical_latencies else 0,
            "max": round(max(critical_latencies)) if critical_latencies else 0,
        },
        "critical_resolution_ms": {
            "avg": round(statistics.mean(critical_resolution)) if critical_resolution else 0,
            "max": round(max(critical_resolution)) if critical_resolution else 0,
        },
        "critical_pairs": critical_pairs,
        "total_duration_ms": total_ms,
        "started_at": t_lane_start.isoformat(),
        "finished_at": datetime.now(timezone.utc).isoformat(),
    }


def _run_race(req: RaceReq) -> None:
    global _race_running
    _race_running = True
    try:
        q.r.set(_RACE_STATUS_KEY, "running")
        rng = random.Random(42)  # fixed seed → reproducible pair of lanes
        objects = load_all_tracked_objects()

        normal_jobs, seen = [], set()
        while len(normal_jobs) < req.n:
            j = random_job(rng, objects)
            if j.event_id not in seen:
                seen.add(j.event_id)
                normal_jobs.append(j)

        critical_jobs = []
        while len(critical_jobs) < req.critical:
            j = make_critical_job(rng, objects)
            if j.event_id not in seen:
                seen.add(j.event_id)
                critical_jobs.append(j)

        all_jobs = normal_jobs + critical_jobs
        critical_after_idx = len(normal_jobs)

        try:
            _timeline.emit(q.r, "race_started", "api",
                           detail={"n": req.n, "critical": req.critical})
        except Exception:  # noqa: BLE001
            pass

        with ThreadPoolExecutor(max_workers=2) as ex:
            fifo_fut = ex.submit(_race_lane, "fifo", list(all_jobs), critical_after_idx)
            prio_fut = ex.submit(_race_lane, "priority", list(all_jobs), critical_after_idx)

        fifo_result = fifo_fut.result()
        prio_result = prio_fut.result()

        result = {
            "fifo": fifo_result,
            "priority": prio_result,
            "n": req.n,
            "critical": req.critical,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
        }
        q.r.set(_RACE_RESULT_KEY, json.dumps(result))

        try:
            _timeline.emit(q.r, "race_done", "api",
                           detail={"n": req.n, "critical": req.critical,
                                   "fifo_crit_avg_ms": fifo_result["critical_latency_ms"]["avg"],
                                   "prio_crit_avg_ms": prio_result["critical_latency_ms"]["avg"],
                                   "lane": "both"})
        except Exception:  # noqa: BLE001
            pass
        q.r.set(_RACE_STATUS_KEY, "done")
    except Exception:  # noqa: BLE001
        q.r.set(_RACE_STATUS_KEY, "error")
    finally:
        _race_running = False


@app.post("/api/cloud/race/start")
def race_start(req: RaceReq, bg: BackgroundTasks) -> dict:
    """Start a FIFO vs priority race. Results available at GET /api/cloud/race/result."""
    if _race_running:
        return {"status": "already_running"}
    bg.add_task(_run_race, req)
    return {"status": "started", **req.model_dump()}


@app.get("/api/cloud/race/result")
def race_result() -> dict:
    """Return the most recent race result, or 404 if none has completed yet."""
    status = q.r.get(_RACE_STATUS_KEY)
    if status == "running":
        return JSONResponse({"status": "running"}, status_code=202)
    raw = q.r.get(_RACE_RESULT_KEY)
    if raw is None:
        return JSONResponse({"error": "not_found", "detail": "no race has completed yet"}, status_code=404)
    return json.loads(raw)


@app.post("/api/cloud/race/reset")
def race_reset() -> dict:
    """Clear the stored race result. Does NOT flush the main queue."""
    q.r.delete(_RACE_RESULT_KEY)
    q.r.delete(_RACE_STATUS_KEY)
    return {"status": "cleared"}
