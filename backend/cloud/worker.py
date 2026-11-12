"""
worker.py — single-threaded queue worker. ONE job at a time per process: the
LLM fallback counter used for `fallbacks_used` is per-process, so concurrent
jobs in one process would blend each other's counts.

    python -m backend.cloud.worker
"""

from __future__ import annotations

import json
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

    # D1: compact outcome — extract residual_km before building result
    residual_km = None
    if record is not None:
        try:
            residual_km = record["resolution"].get("residual_risk")
        except Exception:  # noqa: BLE001
            pass
    # maneuvering_norad: operator_A/OPERATOR_A -> primary, operator_B/OPERATOR_B -> secondary
    _ma = outcome.get("maneuvering_agent", "").upper()
    maneuvering_norad = (
        job.primary_id if _ma == "OPERATOR_A"
        else job.secondary_id if _ma == "OPERATOR_B"
        else None
    )

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
        # D1: domain / compact-outcome fields (embedded in result:<id>, no new Redis key)
        "primary_id": job.primary_id,
        "secondary_id": job.secondary_id,
        "miss_km": job.miss_distance_km,
        "tca_utc": job.tca_utc,
        "lead_h": job.lead_h,  # hours_to_tca at enqueue time, matches risk.py urgency definition
        "maneuvering_norad": maneuvering_norad,
        "residual_km": residual_km,
        "validation_clear": outcome.get("resolution_status") == "approved",
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
    # D1: tca_h = hours_to_tca stored at job creation (matches risk.py urgency definition)
    tca_h = job.lead_h
    summary = {
        "event_id": job.event_id, "primary_id": job.primary_id, "secondary_id": job.secondary_id,
        "primary_name": record["primary_name"], "secondary_name": record["secondary_name"],
        "miss_km": job.miss_distance_km, "risk": round(job.risk, 3), "is_critical": job.is_critical,
        "rounds": result.get("rounds", 0), "resolution_status": result["resolution_status"],
        "maneuvering_agent": result.get("maneuvering_agent", ""), "delta_v_mps": result.get("delta_v_mps", 0.0),
        "latency_ms": round((finished - enq).total_seconds() * 1000), "finished_at": result["finished_at"],
        "retries": result.get("retries", 0),   # Block 1: needed for recent_retried and attempts field
        "attempts": result.get("retries", 0) + 1,
        "tca_h": tca_h,  # D1: hours to TCA at enqueue time
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
    # QUEUE_NS: set to "fifo" or "prio" for race lane workers; empty = main queue (default).
    # Empty default leaves all existing key names byte-for-byte unchanged.
    _ns_env = os.environ.get("QUEUE_NS", "")
    _lane = _ns_env  # used for timeline event details
    q = Queue(ns=(_ns_env + ":") if _ns_env else "")
    tracer = trace_mod.Tracer(q.r, q.ns)
    last_reap = 0.0
    done = 0
    print(f"[worker {worker_id}] up; lease={q.lease_seconds}s llm_mode={os.environ.get('LLM_MODE')}", flush=True)

    # Emit worker_started (best-effort).
    try:
        _lane_detail = {"lane": _lane} if _lane else {}
        timeline.emit(q.r, "worker_started", worker_id,
                      detail={"lease_s": q.lease_seconds, **_lane_detail})
    except Exception:  # noqa: BLE001
        pass

    # Heartbeat runs in its own daemon thread so it keeps beating during a long job.
    # Job execution below is still strictly one job at a time.
    beating = threading.Event()

    _hb_down_since: float | None = None  # heartbeat tracks outage for timeline

    def _beat() -> None:
        nonlocal _hb_down_since
        backoff = Backoff(f"heartbeat {worker_id}")
        while not beating.is_set():
            wait = HEARTBEAT_EVERY_S
            try:
                q.heartbeat(worker_id)
                backoff.ok()
                if _hb_down_since is not None:
                    try:
                        timeline.emit_redis_up(q.r, _hb_down_since)
                    except Exception:  # noqa: BLE001
                        pass
                    _hb_down_since = None
            except RETRYABLE as exc:
                if _hb_down_since is None:
                    _hb_down_since = time.time()
                    try:
                        timeline.emit_redis_down(q.r, _hb_down_since)
                    except Exception:  # noqa: BLE001
                        pass
                wait = backoff.failed(exc)
            except Exception as exc:      # noqa: BLE001 - this thread must never die
                print(f"[heartbeat {worker_id}] unexpected {exc!r}", flush=True)
            beating.wait(wait)

    beat_thread = threading.Thread(target=_beat, daemon=True)
    beat_thread.start()

    backoff = Backoff(f"worker {worker_id}")

    while not _stop:
        # Race lane workers: pause until race:barrier is set by the orchestrator.
        # Main queue workers (QUEUE_NS="") skip this check entirely.
        if _lane:
            try:
                if not q.r.exists("race:barrier"):
                    time.sleep(0.05)
                    continue
            except Exception:  # noqa: BLE001  — Redis down: also wait
                time.sleep(0.05)
                continue

        try:
            t = time.monotonic()
            if t - last_reap >= REAP_EVERY_S:
                requeued, failed, reaped = q.reap_detail()
                if requeued or failed:
                    print(f"[worker {worker_id}] reaped: requeued={requeued} failed={failed}", flush=True)
                # Emit timeline events and mark expired attempts (best-effort).
                # D1: batch-fetch pair info for detail enrichment (one pipeline, non-blocking)
                _pair_data: list[dict] = [{} for _ in reaped]
                if reaped:
                    try:
                        _pipe = q.r.pipeline()
                        for _ji in reaped:
                            _pipe.get(q.k(f"job:{_ji['id']}"))
                        _job_raws = _pipe.execute()
                        from backend.app.profiles import load_all_tracked_objects as _lato  # noqa: PLC0415
                        _obj_map = {_o.norad_id: _o for _o in _lato()}
                        for _idx, _raw in enumerate(_job_raws):
                            if not _raw:
                                continue
                            _jd = json.loads(_raw)
                            _pid, _sid = _jd.get("primary_id", ""), _jd.get("secondary_id", "")
                            _pname = _obj_map[_pid].name if _pid in _obj_map else f"NORAD {_pid}"
                            _sname = _obj_map[_sid].name if _sid in _obj_map else f"NORAD {_sid}"
                            _pair_data[_idx] = {
                                "pair": f"{_pname} / {_sname}",
                                "risk": round(_jd.get("risk", 0), 3),
                                "is_critical": bool(_jd.get("is_critical", False)),
                            }
                    except Exception:  # noqa: BLE001
                        pass

                _ld = {"lane": _lane} if _lane else {}
                for job_info, _pd in zip(reaped, _pair_data):
                    jid, outcome, prev_worker, attempt_n = (
                        job_info["id"], job_info["outcome"],
                        job_info["worker_id"], job_info["attempt_n"])
                    try:
                        if outcome == "requeued":
                            timeline.emit(q.r, "lease_expired", worker_id,
                                          detail={"prev_worker": prev_worker, **_pd, **_ld}, event_id=jid)
                            timeline.emit(q.r, "job_requeued", worker_id, event_id=jid,
                                          detail={**_pd, **_ld})
                        else:
                            timeline.emit(q.r, "job_failed", worker_id, event_id=jid,
                                          detail=_ld if _ld else None)
                    except Exception:  # noqa: BLE001
                        pass
                    tracer.finish_attempt(jid, attempt_n, "lease_expired")
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
        run_one(q, worker_id, stored, tracer)
        done += 1

    beating.set()
    beat_thread.join()      # stop beating BEFORE deregistering, or it could re-register
    try:
        q.deregister(worker_id)
        timeline.emit(q.r, "worker_stopped", worker_id,
                      detail={"jobs_done": done, **(_lane_detail)})
    except RETRYABLE:
        pass                              # Redis down at shutdown: the heartbeat just goes stale
    except Exception:  # noqa: BLE001
        pass
    print(f"[worker {worker_id}] stopped cleanly after {done} jobs", flush=True)


if __name__ == "__main__":
    main()
