"""Queue verification. Needs redis + >=1 running worker (tests a, b); c and d use an
isolated key namespace so they never race with real workers.

    docker compose run --rm worker python scripts/verify_queue.py

WARNING: flushes the Redis db (tests a, b).
"""
import sys
import time

import _common  # noqa: F401
from enqueue import enqueue_batch
from backend.cloud.jobs import Job
from backend.cloud.queue import Queue

failures = 0


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += (not ok)
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def live_workers(q: Queue) -> int:
    beats = q.stats_snapshot()["worker_heartbeat_age_s"]
    return sum(age < 10 for age in beats.values())


def reset(q: Queue) -> int:
    q.r.flushdb()
    time.sleep(3)  # workers re-register their heartbeat (every 2 s) after the flush
    return live_workers(q)


def wait_done(q: Queue, jobs, timeout: float = 180.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if q.r.mget([q.k("result:" + j.event_id) for j in jobs]).count(None) == 0:
            return True
        time.sleep(0.25)
    return False


def start_rank(q: Queue, jobs, critical_id: str) -> int:
    results = [q.get_result(j.event_id) for j in jobs]
    order = sorted(results, key=lambda r: r["started_at"])
    return 1 + next(i for i, r in enumerate(order) if r["event_id"] == critical_id)


def test_drain(q: Queue) -> None:
    workers = reset(q)
    jobs = enqueue_batch(q, 100, "fifo", seed=11)
    done = wait_done(q, jobs)
    s = q.stats_snapshot()
    check("a. drain: 100 jobs all have results", done, f"workers={workers}, completed={s['completed']}")
    check("a. drain: nothing left processing/queued", s["processing"] == 0 and s["depth"] == 0,
          f"processing={s['processing']} depth={s['depth']}")


def test_priority(q: Queue) -> None:
    ranks = {}
    for policy in ("priority", "fifo"):
        workers = reset(q)
        jobs = enqueue_batch(q, 100, policy, critical=1, critical_after=100, seed=22)
        crit = next(j for j in jobs if j.is_critical)
        ok = wait_done(q, jobs)
        ranks[policy] = start_rank(q, jobs, crit.event_id) if ok else -1
        print(f"      {policy}: critical job (enqueued last) started #{ranks[policy]} of 101 "
              f"with {workers} workers", flush=True)
    check("b. priority policy: critical starts within workers*2", 0 < ranks["priority"] <= workers * 2,
          f"rank={ranks['priority']} limit={workers * 2}")
    check("b. fifo policy: critical starts near the end", ranks["fifo"] >= 101 - workers * 2,
          f"rank={ranks['fifo']} of 101")


def _tiny_job(tag: str) -> Job:
    return Job(primary_id="1", secondary_id="2", miss_distance_km=1.0,
               tca_utc="2026-01-01T00:00:00Z" + tag, risk=0.5)


def test_idempotency() -> None:
    q = Queue(ns="verify:c:")
    job = _tiny_job("c")
    q.enqueue(job, -0.5)
    popped = q.pop("w-test")
    first = q.complete(popped["event_id"], {"resolution_status": "approved", "worker_id": "first"})
    second = q.complete(popped["event_id"], {"resolution_status": "approved", "worker_id": "second"})
    kept = q.get_result(job.event_id)["worker_id"]
    check("c. idempotency: duplicate complete() keeps first result",
          first and not second and kept == "first" and q.stats_snapshot()["completed"] == 1,
          f"kept={kept}")
    check("c. idempotency: enqueue() of a finished job is skipped", q.enqueue(job, -0.5) is False)


def test_reaper() -> None:
    q = Queue(ns="verify:d:", lease_seconds=1)
    job = _tiny_job("d")
    q.enqueue(job, -0.7)
    q.pop("dead-worker")  # "crash": lease taken, never completed
    time.sleep(1.5)
    first, second = q.reap(), q.reap()
    score = q.r.zscore(q.k("jobs"), job.event_id)
    check("d. reaper: expired lease requeued once, second reap does nothing",
          first == (1, 0) and second == (0, 0) and q.r.zcard(q.k("jobs")) == 1,
          f"reap1={first} reap2={second}")
    check("d. reaper: requeued with ORIGINAL score, retries=1", score == -0.7 and q.retries(job.event_id) == 1,
          f"score={score}")
    for _ in range(q.max_retries):  # keep crashing until the retry cap is hit
        q.pop("dead-worker")
        time.sleep(1.2)
        q.reap()
    res = q.get_result(job.event_id)
    check("d. reaper: after MAX_RETRIES the job is written as failed",
          res is not None and res["resolution_status"] == "failed" and q.stats_snapshot()["failed"] == 1,
          f"retries={q.retries(job.event_id)} status={res and res['resolution_status']}")


if __name__ == "__main__":
    main_q = Queue()
    test_idempotency()
    test_reaper()
    test_drain(main_q)
    test_priority(main_q)
    for ns in ("verify:c:", "verify:d:"):
        for key in main_q.r.scan_iter(ns + "*"):
            main_q.r.delete(key)
    print("\nALL PASS" if failures == 0 else f"\n{failures} FAILED")
    sys.exit(1 if failures else 0)
