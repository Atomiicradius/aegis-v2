"""
worker.py — single-threaded queue worker. ONE job at a time per process: the
LLM fallback counter used for `fallbacks_used` is per-process, so concurrent
jobs in one process would blend each other's counts.

    python -m backend.cloud.worker
"""

from __future__ import annotations

import logging
import os
import signal
import socket
import threading
import time
from datetime import datetime, timezone

from backend.cloud import timeline, trace as trace_mod
from backend.cloud.jobs import Job, process_job
from backend.cloud.queue import Queue
from backend.cloud.resilience import RETRYABLE, Backoff, retry

HEARTBEAT_EVERY_S = 2.0
REAP_EVERY_S = 5.0
IDLE_SLEEP_S = 0.02

_stop = False


def _request_stop(signum, frame) -> None:  # SIGTERM / SIGINT: finish current job, then exit
    global _stop
    _stop = True


def _iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def run_one(q: Queue, worker_id: str, stored: dict, tracer: trace_mod.Tracer) -> None:
    job = Job(**{k: v for k, v in stored.items() if k != "score"})
    started_at = _iso()

    # Record this lease attempt in the trace (best-effort, non-blocking).
    t_lease = q.now()
    t_lease_deadline = t_lease + q.lease_seconds
    attempt_n = tracer.start_attempt(
        job.event_id, job.enqueued_at, job.risk, job.is_critical,
        worker_id, t_lease, t_lease_deadline)

    t_process_start = time.perf_counter()
    try:
        outcome = process_job(job)
        # The full record goes to Redis separately (record()); result:<id> must stay small.
        record = {k: outcome.pop(k) for k in ("transcript", "resolution", "primary_name", "secondary_name")}
    except Exception as exc:  # noqa: BLE001 - record the failure, still complete the job
        outcome = {"resolution_status": "error", "error": repr(exc),
                   "llm_mode": os.environ.get("LLM_MODE", "real"), "fallbacks_used": 0,
                   "negotiate_ms": 0.0}
        record = None
    t_process_end = time.perf_counter()

    result = {
        **outcome,
        "event_id": job.event_id,
        "worker_id": worker_id,
        "enqueued_at": job.enqueued_at,
        "started_at": started_at,
        "finished_at": _iso(),
        "retries": retry(lambda: q.retries(job.event_id), f"worker {worker_id}"),
        "risk": job.risk,
        "is_critical": job.is_critical,
    }
    # The job is already computed. If Redis is down NOW we keep `result` in memory and retry until
    # complete() lands (never drop it, never exit). If its lease expired meanwhile, the reaper may
    # requeue the job: complete() still wins (SET NX) and removes the requeued copy; the worst case
    # is one duplicate execution, which `retries_total` shows.
    first_completion = retry(lambda: q.complete(job.event_id, result), f"worker {worker_id}")

    # Finalize trace (best-effort, outside job critical path).
    outcome_str = "completed" if first_completion else "duplicate_discarded"
    tracer.finish_attempt(
        job.event_id, attempt_n, outcome_str,
        timings={
            "process_end_ms": round((t_process_end - t_process_start) * 1000, 1),
            "negotiate_ms": result.get("negotiate_ms", 0.0),
        },
        completed_at=result["finished_at"],
        final_status=result.get("resolution_status", ""),
    )

    # RECORD_TRANSCRIPTS=0 switches the record feature off (kill switch); only the first completion records.
    if first_completion and record and os.environ.get("RECORD_TRANSCRIPTS", "1") != "0":
        _record(q, worker_id, job, result, record)


def _record(q: Queue, worker_id: str, job: Job, result: dict, record: dict) -> None:
    """Best effort: the transcript is a nicety, the result is already safe. Never block the worker for long."""
    finished, enq = datetime.fromisoformat(result["finished_at"]), datetime.fromisoformat(job.enqueued_at)
    summary = {
        "event_id": job.event_id, "primary_id": job.primary_id, "secondary_id": job.secondary_id,
        "primary_name": record["primary_name"], "secondary_name": record["secondary_name"],
        "miss_km": job.miss_distance_km, "risk": round(job.risk, 3), "is_critical": job.is_critical,
        "rounds": result.get("rounds", 0), "resolution_status": result["resolution_status"],
        "maneuvering_agent": result.get("maneuvering_agent", ""), "delta_v_mps": result.get("delta_v_mps", 0.0),
        "latency_ms": round((finished - enq).total_seconds() * 1000), "finished_at": result["finished_at"],
        "retries": result.get("retries", 0),   # Block 1: needed for recent_retried and attempts field
        "attempts": result.get("retries", 0) + 1,
    }
    try:
        retry(lambda: q.record(summary, record["transcript"], record["resolution"]),
              f"worker {worker_id}", max_wait_s=20)
    except RETRYABLE:
        pass


def main() -> None:
    logging.basicConfig(level=logging.ERROR)  # keep the negotiator's per-round logs quiet
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    worker_id = socket.gethostname()
    q = Queue()
    tracer = trace_mod.Tracer(q.r, q.ns)
    last_reap = 0.0
    done = 0
    print(f"[worker {worker_id}] up; lease={q.lease_seconds}s llm_mode={os.environ.get('LLM_MODE')}", flush=True)

    # Emit worker_started (best-effort).
    try:
        timeline.emit(q.r, "worker_started", worker_id, detail={"lease_s": q.lease_seconds})
    except Exception:  # noqa: BLE001
        pass

    # Heartbeat runs in its own daemon thread so it keeps beating during a long job.
    # Job execution below is still strictly one job at a time.
    beating = threading.Event()

    def _beat() -> None:
        backoff = Backoff(f"heartbeat {worker_id}")
        while not beating.is_set():
            wait = HEARTBEAT_EVERY_S
            try:
                q.heartbeat(worker_id)
                backoff.ok()
            except RETRYABLE as exc:      # Redis down: retry sooner than 2 s, with backoff
                wait = backoff.failed(exc)
            except Exception as exc:      # noqa: BLE001 - this thread must never die
                print(f"[heartbeat {worker_id}] unexpected {exc!r}", flush=True)
            beating.wait(wait)

    beat_thread = threading.Thread(target=_beat, daemon=True)
    beat_thread.start()

    backoff = Backoff(f"worker {worker_id}")
    _redis_down_since: float | None = None

    while not _stop:
        try:
            t = time.monotonic()
            if t - last_reap >= REAP_EVERY_S:
                requeued, failed, reaped = q.reap_detail()
                if requeued or failed:
                    print(f"[worker {worker_id}] reaped: requeued={requeued} failed={failed}", flush=True)
                # Emit timeline events and mark expired attempts (best-effort).
                for job_info in reaped:
                    jid, outcome, prev_worker, attempt_n = (
                        job_info["id"], job_info["outcome"],
                        job_info["worker_id"], job_info["attempt_n"])
                    try:
                        if outcome == "requeued":
                            timeline.emit(q.r, "lease_expired", worker_id,
                                          detail={"prev_worker": prev_worker}, event_id=jid)
                            timeline.emit(q.r, "job_requeued", worker_id, event_id=jid)
                        else:
                            timeline.emit(q.r, "job_failed", worker_id, event_id=jid)
                    except Exception:  # noqa: BLE001
                        pass
                    tracer.finish_attempt(jid, attempt_n, "lease_expired")
                last_reap = t
            stored = q.pop(worker_id)
        except RETRYABLE as exc:          # Redis down/restarting: wait, then resume where we were
            if _redis_down_since is None:
                _redis_down_since = time.time()
                try:
                    timeline.emit_redis_down(q.r, _redis_down_since)
                except Exception:  # noqa: BLE001
                    pass
            time.sleep(backoff.failed(exc))
            continue

        # Recovery: Redis is back.
        if _redis_down_since is not None:
            try:
                timeline.emit_redis_up(q.r, _redis_down_since)
            except Exception:  # noqa: BLE001
                pass
            _redis_down_since = None
        backoff.ok()
        # If the connection died AFTER the pop script ran but before we saw the reply, that job is
        # leased to us but unknown to us; its lease simply expires and the reaper requeues it.
        if stored is None:
            time.sleep(IDLE_SLEEP_S)
            continue
        run_one(q, worker_id, stored, tracer)
        done += 1

    beating.set()
    beat_thread.join()      # stop beating BEFORE deregistering, or it could re-register
    try:
        q.deregister(worker_id)
        timeline.emit(q.r, "worker_stopped", worker_id, detail={"jobs_done": done})
    except RETRYABLE:
        pass                              # Redis down at shutdown: the heartbeat just goes stale
    except Exception:  # noqa: BLE001
        pass
    print(f"[worker {worker_id}] stopped cleanly after {done} jobs", flush=True)


if __name__ == "__main__":
    main()
