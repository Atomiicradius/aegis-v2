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
from datetime import timedelta
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
from backend.cloud.queue import Queue
from backend.cloud.scoring import compute_score
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
# Block 5 — FIFO vs priority race (real workers, namespaced queues)
# ---------------------------------------------------------------------------
# Architecture:
#   worker_fifo containers: QUEUE_NS=fifo → consume from fifo:jobs (fifo policy)
#   worker_prio containers: QUEUE_NS=prio → consume from prio:jobs (priority policy)
#   race:barrier key: lane workers wait for it before consuming (start barrier)
#   Same 303 events enqueued to both lanes in one MULTI/EXEC pipeline.
#   critical_resolution_ms = finished_at − enqueued_at (wall-clock, real workers).
#   Main queue (ns="") is NEVER touched; existing key names byte-for-byte unchanged.
# ---------------------------------------------------------------------------

_RACE_NS_FIFO = "fifo:"
_RACE_NS_PRIO = "prio:"
_RACE_BARRIER_KEY = "race:barrier"
_RACE_META_KEY = "race:meta"
_RACE_STATUS_KEY = "race:status"
_RACE_RESULT_KEY = "race:result:latest"
_race_running = False

_RACE_ALIVE_S = 10.0


def _live_in_ns(ns: str) -> int:
    """Live worker count for a namespaced lane (heartbeat < 10 s)."""
    try:
        now_s, now_us = q.r.time()
        now = now_s + now_us / 1_000_000
        return sum(1 for t in q.r.hgetall(f"{ns}workers").values() if now - float(t) < _RACE_ALIVE_S)
    except Exception:  # noqa: BLE001
        return 0


class RaceReq(BaseModel):
    n: int = Field(300, ge=10, le=500)
    critical: int = Field(3, ge=1, le=20)
    seed: int = Field(42, ge=0)
    critical_offset_s: float = Field(2.0, ge=0.5, le=30.0)


def _build_lane_result(policy: str, results: list[dict], critical_ids: set[str],
                       obj_map: dict | None = None) -> dict:
    """Build lane statistics from worker-completed results.
    critical_resolution_ms is wall-clock: finished_at - enqueued_at (includes queue wait).
    critical_latency_ms is processing only: finished_at - started_at.
    obj_map: {norad_id: TrackedObject} used to resolve names from NORAD IDs.
    """
    crit = [r for r in results if r.get("event_id") in critical_ids]
    crit_res_ms: list[float] = []
    crit_lat_ms: list[float] = []
    crit_pairs: list[dict] = []

    def _name(norad: str) -> str:
        if obj_map and norad in obj_map:
            return obj_map[norad].name
        return f"NORAD {norad}" if norad else ""

    for r in sorted(crit, key=lambda x: x.get("finished_at", "")):
        if not r.get("finished_at") or not r.get("enqueued_at"):
            continue
        enq_dt = datetime.fromisoformat(r["enqueued_at"])
        fin_dt = datetime.fromisoformat(r["finished_at"])
        start_dt = datetime.fromisoformat(r["started_at"]) if r.get("started_at") else fin_dt
        res_ms = (fin_dt - enq_dt).total_seconds() * 1000
        lat_ms = (fin_dt - start_dt).total_seconds() * 1000
        crit_res_ms.append(res_ms)
        crit_lat_ms.append(lat_ms)
        pid = r.get("primary_id", "")
        sid = r.get("secondary_id", "")
        crit_pairs.append({
            "event_id": r.get("event_id", ""),
            "primary_name": r.get("primary_name") or _name(pid),
            "secondary_name": r.get("secondary_name") or _name(sid),
            "risk": round(r.get("risk", 0), 3),
            "lead_h": r.get("lead_h"),
            "resolution_ms": round(res_ms),
            "latency_ms": round(lat_ms),
            "lane": policy,
        })

    # Drain time: wall-clock from first enqueued_at to last finished_at across ALL jobs
    drain_ms = 0
    finished = [r for r in results if r.get("finished_at")]
    if finished:
        first_enq = min(datetime.fromisoformat(r["enqueued_at"]) for r in results if r.get("enqueued_at"))
        last_fin = max(datetime.fromisoformat(r["finished_at"]) for r in finished)
        drain_ms = round((last_fin - first_enq).total_seconds() * 1000)

    return {
        "policy": policy,
        "total_jobs": len(results),
        "critical_jobs": len(crit_pairs),
        "failed_jobs": sum(1 for r in results if r.get("resolution_status") == "failed"),
        "critical_resolution_ms": {
            "avg": round(statistics.mean(crit_res_ms)) if crit_res_ms else 0,
            "max": round(max(crit_res_ms)) if crit_res_ms else 0,
        },
        "critical_latency_ms": {
            "avg": round(statistics.mean(crit_lat_ms)) if crit_lat_ms else 0,
            "max": round(max(crit_lat_ms)) if crit_lat_ms else 0,
        },
        "critical_pairs": crit_pairs,
        "drain_ms": drain_ms,
    }


def _run_real_race(req: RaceReq) -> None:
    global _race_running
    _race_running = True
    r = q.r
    try:
        r.set(_RACE_STATUS_KEY, "running")

        # 1. Generate the same event set for both lanes (fixed seed)
        rng = random.Random(req.seed)
        objects = load_all_tracked_objects()
        normal_jobs: list = []
        seen: set = set()
        while len(normal_jobs) < req.n:
            j = random_job(rng, objects)
            if j.event_id not in seen:
                seen.add(j.event_id)
                normal_jobs.append(j)
        critical_jobs: list = []
        while len(critical_jobs) < req.critical:
            j = make_critical_job(rng, objects)
            if j.event_id not in seen:
                seen.add(j.event_id)
                critical_jobs.append(j)

        all_jobs = normal_jobs + critical_jobs
        critical_ids = {j.event_id for j in critical_jobs}
        event_ids = [j.event_id for j in all_jobs]

        # 2. Close barrier so lane workers wait; clear previous lane data
        r.delete(_RACE_BARRIER_KEY)
        for ns_prefix in (_RACE_NS_FIFO, _RACE_NS_PRIO):
            pipe_del = r.pipeline(transaction=False)
            for k in r.scan_iter(f"{ns_prefix}result:*"):
                pipe_del.delete(k)
            for k in r.scan_iter(f"{ns_prefix}job:*"):
                pipe_del.delete(k)
            pipe_del.delete(
                f"{ns_prefix}jobs", f"{ns_prefix}processing",
                f"{ns_prefix}owner", f"{ns_prefix}counters", f"{ns_prefix}recent",
            )
            pipe_del.execute()

        # 3. Stamp enqueued_at NOW (after cleanup, right before enqueue).
        # All jobs receive the actual enqueue time so resolution_ms is always positive.
        # For FIFO ordering, critical jobs get a "virtual later arrival" encoded ONLY in
        # the FIFO sort score (not in enqueued_at): normal jobs score = t0, critical score
        # = t0 + offset_s. This pushes criticals to the END of the FIFO queue while keeping
        # enqueued_at real. Priority score ignores arrival order and uses -risk directly.
        t0 = datetime.now(timezone.utc)
        t_crit_virtual = t0 + timedelta(seconds=req.critical_offset_s)
        for i, job in enumerate(normal_jobs):
            job.enqueued_at = (t0 + timedelta(microseconds=i)).isoformat()
        for j_idx, job in enumerate(critical_jobs):
            # enqueued_at = real time (not future); only FIFO sort score uses t_crit_virtual
            job.enqueued_at = (t0 + timedelta(microseconds=len(normal_jobs) + j_idx)).isoformat()

        # 4. Save manifest (needed for partial GET /race/result while running)
        manifest = {
            "n": req.n, "critical": req.critical, "seed": req.seed,
            "event_ids": event_ids, "critical_ids": list(critical_ids),
            "started_at": t0.isoformat(),
        }
        r.set(_RACE_META_KEY, json.dumps(manifest))

        # 5. Enqueue identical jobs to both lanes in one MULTI/EXEC (atomic).
        # FIFO score for critical jobs uses t_crit_virtual (pushing them to the end).
        # Priority score uses the actual enqueued_at + risk (high risk → front of queue).
        def _fifo_score(job) -> float:
            if job.event_id in critical_ids:
                return t_crit_virtual.timestamp()
            return datetime.fromisoformat(job.enqueued_at).timestamp()

        fifo_scores = {j.event_id: _fifo_score(j) for j in all_jobs}
        prio_scores = {j.event_id: compute_score(j, "priority") for j in all_jobs}
        pipe = r.pipeline(transaction=True)
        for job in all_jobs:
            fp = {**job.model_dump(), "score": fifo_scores[job.event_id]}
            pp = {**job.model_dump(), "score": prio_scores[job.event_id]}
            pipe.set(f"{_RACE_NS_FIFO}job:{job.event_id}", json.dumps(fp))
            pipe.set(f"{_RACE_NS_PRIO}job:{job.event_id}", json.dumps(pp))
        pipe.zadd(f"{_RACE_NS_FIFO}jobs", fifo_scores)
        pipe.zadd(f"{_RACE_NS_PRIO}jobs", prio_scores)
        pipe.hincrby(f"{_RACE_NS_FIFO}counters", "enqueued", len(all_jobs))
        pipe.hincrby(f"{_RACE_NS_PRIO}counters", "enqueued", len(all_jobs))
        pipe.execute()  # EXEC — both queues loaded atomically

        # 7. Open barrier — both lane worker sets begin consuming simultaneously
        r.set(_RACE_BARRIER_KEY, "1")
        fifo_live = _live_in_ns(_RACE_NS_FIFO)
        prio_live = _live_in_ns(_RACE_NS_PRIO)

        try:
            _timeline.emit(r, "race_started", "api",
                           detail={"n": req.n, "critical": req.critical, "seed": req.seed,
                                   "fifo_workers": fifo_live, "prio_workers": prio_live, "lane": "both"})
        except Exception:  # noqa: BLE001
            pass

        # 8. Poll until both lanes are fully drained (all events have results)
        deadline = time.time() + 300
        while time.time() < deadline:
            time.sleep(2)
            fifo_done = sum(1 for x in r.mget([f"{_RACE_NS_FIFO}result:{id}" for id in event_ids]) if x)
            prio_done = sum(1 for x in r.mget([f"{_RACE_NS_PRIO}result:{id}" for id in event_ids]) if x)
            if fifo_done == len(event_ids) and prio_done == len(event_ids):
                break

        # 9. Collect results
        fifo_raws = r.mget([f"{_RACE_NS_FIFO}result:{id}" for id in event_ids])
        prio_raws = r.mget([f"{_RACE_NS_PRIO}result:{id}" for id in event_ids])
        fifo_results = [json.loads(x) for x in fifo_raws if x]
        prio_results = [json.loads(x) for x in prio_raws if x]

        # Measure start skew: difference between earliest started_at across lanes
        def _first_started(res_list: list[dict]) -> datetime | None:
            ts = [datetime.fromisoformat(r["started_at"]) for r in res_list if r.get("started_at")]
            return min(ts) if ts else None

        ff, pf = _first_started(fifo_results), _first_started(prio_results)
        start_skew_ms = round(abs((ff - pf).total_seconds() * 1000), 1) if ff and pf else None

        obj_map = {o.norad_id: o for o in objects}
        fifo_lane = _build_lane_result("fifo", fifo_results, critical_ids, obj_map)
        prio_lane = _build_lane_result("prio", prio_results, critical_ids, obj_map)

        result = {
            "fifo": fifo_lane,
            "priority": prio_lane,
            "n": req.n,
            "critical": req.critical,
            "seed": req.seed,
            "enqueued_at": t0.isoformat(),
            "start_skew_ms": start_skew_ms,
            "total": len(event_ids),
        }
        r.set(_RACE_RESULT_KEY, json.dumps(result))
        r.set(_RACE_STATUS_KEY, "done")

        try:
            _timeline.emit(r, "race_done", "api",
                           detail={"n": req.n, "critical": req.critical,
                                   "fifo_crit_res_ms": fifo_lane["critical_resolution_ms"]["avg"],
                                   "prio_crit_res_ms": prio_lane["critical_resolution_ms"]["avg"],
                                   "fifo_drain_ms": fifo_lane["drain_ms"],
                                   "prio_drain_ms": prio_lane["drain_ms"],
                                   "start_skew_ms": start_skew_ms, "lane": "both"})
        except Exception:  # noqa: BLE001
            pass

    except Exception:  # noqa: BLE001
        r.set(_RACE_STATUS_KEY, "error")
        raise
    finally:
        _race_running = False


@app.post("/api/cloud/race/start")
def race_start(req: RaceReq, bg: BackgroundTasks) -> dict:
    """Start a real FIFO vs priority race using namespaced worker lanes.
    Requires docker compose --profile race up -d (worker_fifo × 2, worker_prio × 2).
    """
    if _race_running:
        return {"status": "already_running"}
    fifo_live = _live_in_ns(_RACE_NS_FIFO)
    prio_live = _live_in_ns(_RACE_NS_PRIO)
    if fifo_live < 1 or prio_live < 1:
        return JSONResponse(
            {"status": "error",
             "detail": f"race lane workers not ready (fifo={fifo_live} prio={prio_live}); "
                       "run: docker compose --profile race up -d "
                       "--scale worker_fifo=2 --scale worker_prio=2"},
            status_code=503,
        )
    bg.add_task(_run_real_race, req)
    return {"status": "started", "fifo_workers": fifo_live, "prio_workers": prio_live,
            **req.model_dump()}


@app.get("/api/cloud/race/result")
def race_result():
    """Return race result. While running, returns partial lane data for live UI update."""
    status = q.r.get(_RACE_STATUS_KEY)
    if status is None:
        return JSONResponse({"error": "not_found", "detail": "no race has been started"}, status_code=404)

    if status == "running":
        meta_raw = q.r.get(_RACE_META_KEY)
        if not meta_raw:
            return {"status": "running", "fifo": None, "priority": None}
        meta = json.loads(meta_raw)
        event_ids = meta["event_ids"]
        critical_ids = set(meta["critical_ids"])
        fifo_raws = q.r.mget([f"{_RACE_NS_FIFO}result:{id}" for id in event_ids])
        prio_raws = q.r.mget([f"{_RACE_NS_PRIO}result:{id}" for id in event_ids])
        fifo_res = [json.loads(x) for x in fifo_raws if x]
        prio_res = [json.loads(x) for x in prio_raws if x]
        _obj_map = {o.norad_id: o for o in load_all_tracked_objects()} if (fifo_res or prio_res) else None
        return {
            "status": "running",
            "total": len(event_ids),
            "fifo_done": len(fifo_res),
            "prio_done": len(prio_res),
            "fifo": _build_lane_result("fifo", fifo_res, critical_ids, _obj_map) if fifo_res else None,
            "priority": _build_lane_result("prio", prio_res, critical_ids, _obj_map) if prio_res else None,
            "n": meta["n"], "critical": meta["critical"], "enqueued_at": meta["started_at"],
        }

    if status == "done":
        raw = q.r.get(_RACE_RESULT_KEY)
        if not raw:
            return JSONResponse({"error": "not_found"}, status_code=404)
        return json.loads(raw)

    return JSONResponse({"error": status}, status_code=500)


@app.post("/api/cloud/race/reset")
def race_reset() -> dict:
    """Clear race results, meta, and barrier. Does NOT stop lane workers or touch main queue."""
    q.r.delete(_RACE_RESULT_KEY, _RACE_STATUS_KEY, _RACE_META_KEY, _RACE_BARRIER_KEY)
    return {"status": "cleared"}
