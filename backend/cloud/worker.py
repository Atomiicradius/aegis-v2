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


def run_one(q: Queue, worker_id: str, stored: dict) -> None:
    job = Job(**{k: v for k, v in stored.items() if k != "score"})
    started_at = _iso()
    try:
        outcome = process_job(job)
    except Exception as exc:  # noqa: BLE001 - record the failure, still complete the job
        outcome = {"resolution_status": "error", "error": repr(exc),
                   "llm_mode": os.environ.get("LLM_MODE", "real"), "fallbacks_used": 0}
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
    retry(lambda: q.complete(job.event_id, result), f"worker {worker_id}")


def main() -> None:
    logging.basicConfig(level=logging.ERROR)  # keep the negotiator's per-round logs quiet
    signal.signal(signal.SIGTERM, _request_stop)
    signal.signal(signal.SIGINT, _request_stop)

    worker_id = socket.gethostname()
    q = Queue()
    last_reap = 0.0
    done = 0
    print(f"[worker {worker_id}] up; lease={q.lease_seconds}s llm_mode={os.environ.get('LLM_MODE')}", flush=True)

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
    while not _stop:
        try:
            t = time.monotonic()
            if t - last_reap >= REAP_EVERY_S:
                requeued, failed = q.reap()
                if requeued or failed:
                    print(f"[worker {worker_id}] reaped: requeued={requeued} failed={failed}", flush=True)
                last_reap = t
            stored = q.pop(worker_id)
        except RETRYABLE as exc:          # Redis down/restarting: wait, then resume where we were
            time.sleep(backoff.failed(exc))
            continue
        backoff.ok()
        # If the connection died AFTER the pop script ran but before we saw the reply, that job is
        # leased to us but unknown to us; its lease simply expires and the reaper requeues it.
        if stored is None:
            time.sleep(IDLE_SLEEP_S)
            continue
        run_one(q, worker_id, stored)
        done += 1

    beating.set()
    beat_thread.join()      # stop beating BEFORE deregistering, or it could re-register
    try:
        q.deregister(worker_id)
    except RETRYABLE:
        pass                              # Redis down at shutdown: the heartbeat just goes stale
    print(f"[worker {worker_id}] stopped cleanly after {done} jobs", flush=True)


if __name__ == "__main__":
    main()
