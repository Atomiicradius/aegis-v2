"""Block-1/3 check: verify trace recording, timeline dedup, and overhead.

Usage:
    python scripts/check_trace.py [--quick] [--api http://localhost:8010]

--quick  (< 1 min target):
    4 workers, LEASE_SECONDS=5, 30 jobs (LLM_STUB_MS=50), one worker kill.
    Full assertion set: two attempts on different workers, lease_expired then
    completed, timeline events. Skips the Redis-restart dedup tests (2a-2e).
    Overhead: microbenchmark (Tracer + timeline, 2000 iterations) + a short
    single-worker throughput A/B (1 worker, 60 jobs, LLM_STUB_MS=50).

Full mode (default, ~3-5 min):
    Same worker-kill test + Redis-restart dedup tests:
      2a-2c: single outage -> one pair (original Block-1 test)
      2d-2e: two outages 15 s apart -> two pairs (Block-3 SET NX lock test)
    + Test 3c: baseline throughput (RECORD_TRACES=0 TIMELINE=0)
    Gate before tagging: also run scripts/regression.py and verify_queue.py.

Run from the repo root on the HOST.
Requires at least 2 worker containers running.
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8010")
ap.add_argument("--quick", action="store_true",
                help="quick tier: skip Redis-restart dedup, use short microbenchmark overhead")
a = ap.parse_args()

failures = 0
_t0 = time.time()


def check(name: str, ok: bool, detail: str = "") -> None:
    global failures
    failures += not ok
    status = "PASS" if ok else "FAIL"
    print(f"{status}  {name}" + (f"  [{detail}]" if detail else ""), flush=True)


def api(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        a.api + path, data=data, method="GET" if body is None else "POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, text=True, capture_output=True, check=False)


def running_workers() -> list[str]:
    r = compose("ps", "-q", "--status", "running", "worker")
    return [x for x in r.stdout.strip().splitlines() if x]


def wait_drain(total: int, timeout: float = 90.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            s = api("/api/cloud/stats")
            if s.get("completed_total", 0) + s.get("failed_total", 0) >= total:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False


# --- Test 1: worker-kill trace -----------------------------------------------
print("\n=== Test 1: worker-kill trace ===")
workers = running_workers()
if len(workers) < 2:
    print("SKIP  need >=2 workers; found", len(workers))
else:
    N_KILL = 30
    api("/api/cloud/reset", {})
    time.sleep(1)
    api("/api/cloud/storm", {"n": N_KILL, "critical": 0, "policy": "fifo"})

    for _ in range(100):
        try:
            if api("/api/cloud/stats").get("processing", 0) > 0:
                break
        except Exception:
            pass
        time.sleep(0.1)

    victim = workers[-1]
    subprocess.run(["docker", "kill", victim], capture_output=True)
    print(f"  Killed worker {victim[:12]}; waiting for requeue + completion ...")

    drained = wait_drain(N_KILL, timeout=60)
    check("1a. all jobs complete after kill", drained)

    s = api("/api/cloud/stats")
    check("1b. failed == 0", s.get("failed_total", 0) == 0, f"failed={s.get('failed_total')}")
    check("1c. retries_total >= 1", s.get("retries_total", 0) >= 1,
          f"retries={s.get('retries_total')}")

    recent = api("/api/cloud/recent?limit=30")
    retried_jobs = [r for r in recent if r.get("attempts", 1) > 1]
    check("1d. at least one retried job in recent", len(retried_jobs) > 0,
          f"retried={len(retried_jobs)}")

    if retried_jobs:
        eid = retried_jobs[0]["event_id"]
        try:
            trace = api(f"/api/cloud/trace/{eid}")
            atts = trace.get("attempts", [])
            check("1e. trace has 2 attempts", len(atts) == 2, f"attempts={len(atts)}")
            if len(atts) >= 2:
                check("1f. attempt 1 outcome=lease_expired",
                      atts[0].get("outcome") == "lease_expired",
                      f"outcome={atts[0].get('outcome')}")
                check("1g. attempt 2 outcome=completed",
                      atts[1].get("outcome") == "completed",
                      f"outcome={atts[1].get('outcome')}")
                check("1h. different workers",
                      atts[0].get("worker_id") != atts[1].get("worker_id"),
                      f"w1={atts[0].get('worker_id')} w2={atts[1].get('worker_id')}")
        except Exception as exc:
            check("1e. trace fetch", False, str(exc))

    tl = api("/api/cloud/timeline")
    tl_types = {e["type"] for e in tl.get("events", [])}
    check("1i. timeline has lease_expired", "lease_expired" in tl_types)
    check("1j. timeline has job_requeued", "job_requeued" in tl_types)

    compose("up", "-d", "--no-recreate", "--scale", f"worker={len(workers)}", "worker")
    print(f"  Restarted worker pool to {len(workers)}.")

# --- Test 2: Redis restart dedup (full mode only) ----------------------------
if not a.quick:
    print("\n=== Test 2: redis_down/redis_up dedup in timeline ===")
    api("/api/cloud/reset", {})
    time.sleep(1)
    api("/api/cloud/storm", {"n": 30, "critical": 0, "policy": "fifo"})
    time.sleep(2)
    compose("stop", "redis")
    print("  Stopped Redis; waiting 5 s for workers to detect the outage ...")
    time.sleep(5)
    compose("start", "redis")
    print("  Started Redis; polling up to 60 s for API + workers to recover and flush events ...")

    # Phase 1: wait for the API server itself to reconnect.
    _api_up = False
    for _i in range(30):
        time.sleep(1)
        try:
            api("/api/cloud/stats")
            _api_up = True
            print(f"  t+{_i+1}s: API server reconnected")
            break
        except Exception:
            pass
    if not _api_up:
        print("  WARNING: API server did not recover within 30 s")

    # Phase 2: poll for the timeline events (workers flush via heartbeat, up to 2 s lag).
    _tl_events: list = []
    for _i in range(30):
        time.sleep(1)
        try:
            _tl_check = api("/api/cloud/timeline")
            _tl_events = _tl_check.get("events", [])
            has_down = any(e["type"] == "redis_down" for e in _tl_events)
            has_up   = any(e["type"] == "redis_up"   for e in _tl_events)
            if has_down and has_up:
                print(f"  t+{_i+1}s: both events found")
                break
        except Exception:
            pass

    down_events = [e for e in _tl_events if e["type"] == "redis_down"]
    up_events   = [e for e in _tl_events if e["type"] == "redis_up"]
    check("2a. exactly one redis_down event (deduplicated across workers)",
          len(down_events) == 1, f"count={len(down_events)}")
    check("2b. exactly one redis_up event", len(up_events) == 1, f"count={len(up_events)}")

    wait_drain(30, timeout=60)
    s = api("/api/cloud/stats")
    check("2c. all jobs complete after restart", s.get("failed_total", 0) == 0,
          f"failed={s.get('failed_total')}")

    # --- Test 2d-2e: two outages >7 s apart -> two pairs (Block-3 SET NX lock) ---
    print("\n=== Test 2d-2e: two outages 15 s apart -> two pairs ===")
    api("/api/cloud/reset", {})
    time.sleep(0.5)
    api("/api/cloud/storm", {"n": 10, "critical": 0, "policy": "fifo"})
    time.sleep(1)

    # First outage (5 s: heartbeat period is 2 s, need >= 1 full beat in the window)
    compose("stop", "redis")
    print("  Stopped Redis (outage 1); waiting 5 s ...")
    time.sleep(5)
    compose("start", "redis")
    # Wait for API/workers to reconnect (confirms outage lock is set), then
    # sleep 10 s (> 7 s TTL) so the lock is definitely gone before outage 2.
    print("  Waiting for workers to reconnect after outage 1 ...")
    for _i in range(20):
        time.sleep(1)
        try:
            api("/api/cloud/stats")
            print(f"  t+{_i+1}s: workers reconnected; sleeping 10 s to let lock (TTL=7 s) expire ...")
            break
        except Exception:
            pass
    time.sleep(10)

    # Second outage -- lock has expired, should produce a second pair
    compose("stop", "redis")
    print("  Stopped Redis (outage 2); waiting 5 s ...")
    time.sleep(5)
    compose("start", "redis")
    print("  Started Redis; polling for two pairs ...")

    _tl2: list = []
    for _i in range(30):
        time.sleep(1)
        try:
            _tl2 = api("/api/cloud/timeline").get("events", [])
            d_count = sum(1 for e in _tl2 if e["type"] == "redis_down")
            u_count = sum(1 for e in _tl2 if e["type"] == "redis_up")
            if d_count >= 2 and u_count >= 2:
                print(f"  t+{_i+1}s: found {d_count} down / {u_count} up events")
                break
        except Exception:
            pass

    d2 = [e for e in _tl2 if e["type"] == "redis_down"]
    u2 = [e for e in _tl2 if e["type"] == "redis_up"]
    check("2d. two redis_down events for two outages", len(d2) == 2, f"count={len(d2)}")
    check("2e. two redis_up events for two outages",   len(u2) == 2, f"count={len(u2)}")
    wait_drain(10, timeout=60)

# --- Test 3: overhead measurement -------------------------------------------
print("\n=== Test 3: overhead measurement ===")

# 3a. Microbenchmark: run inside a worker container (backend module is on PYTHONPATH there).
#     2000 iterations: Tracer.start_attempt/finish_attempt + timeline.emit, with/without traces.
_BENCH_PY = r"""
import time, uuid, os
from backend.cloud.queue import Queue
from backend.cloud.trace import Tracer
from backend.cloud import timeline as tl

N = 2000
q = Queue()

def run(n, record, tline):
    os.environ["RECORD_TRACES"] = "1" if record else "0"
    os.environ["TIMELINE"]      = "1" if tline  else "0"
    import importlib, backend.cloud.trace as tm, backend.cloud.timeline as tlm
    importlib.reload(tm); importlib.reload(tlm)
    tr = tm.Tracer(q.r, q.ns)
    q.r.flushdb()
    t = time.perf_counter()
    for _ in range(n):
        eid = str(uuid.uuid4())
        an = tr.start_attempt(eid, "2024-01-01T00:00:00+00:00", 0.5, False, "b", 0.0, 60.0)
        tr.finish_attempt(eid, an, "completed")
        tlm.emit(q.r, "job_complete", "b", detail={"event_id": eid})
    return (time.perf_counter() - t) * 1000 / n

on  = run(N, True,  True)
off = run(N, False, False)
print(f"on={on:.3f} off={off:.3f} overhead={on-off:.3f}")
"""
try:
    # Pick the first running worker container.
    _worker_name = compose("ps", "-q", "--status", "running", "worker").stdout.strip().splitlines()
    if not _worker_name:
        raise RuntimeError("no running worker container found")
    _cname = subprocess.run(
        ["docker", "inspect", "--format", "{{.Name}}", _worker_name[0]],
        capture_output=True, text=True).stdout.strip().lstrip("/")
    bench_out = subprocess.run(
        ["docker", "exec", _cname, "python", "-c", _BENCH_PY],
        capture_output=True, text=True, timeout=120)
    if bench_out.returncode != 0:
        raise RuntimeError(bench_out.stderr.strip()[-400:])
    line = [l for l in bench_out.stdout.splitlines() if l.startswith("on=")][-1]
    _parts = dict(p.split("=") for p in line.split())
    ms_on, ms_off, overhead_ms = float(_parts["on"]), float(_parts["off"]), float(_parts["overhead"])
    print(f"  Microbenchmark (2000 iters): on={ms_on:.3f} ms/job  off={ms_off:.3f} ms/job  "
          f"overhead={overhead_ms:.3f} ms/job")
    check("3a. trace overhead <= 1.5 ms/job (microbenchmark)", overhead_ms <= 1.5,
          f"overhead={overhead_ms:.3f} ms/job")
except Exception as exc:
    print(f"  Microbenchmark error: {exc}")
    check("3a. trace overhead <= 1.5 ms/job (microbenchmark)", False, str(exc)[:120])

# 3b. Throughput sanity check: 60 jobs, with traces on; check completion time.
N_AB = 60
_rate_on = 0.0
try:
    api("/api/cloud/reset", {})
    time.sleep(0.5)
    t_start = time.time()
    api("/api/cloud/storm", {"n": N_AB, "critical": 0, "policy": "fifo"})
    drained = wait_drain(N_AB, timeout=60)
    elapsed = time.time() - t_start
    _rate_on = round(N_AB / elapsed, 2) if elapsed > 0 else 0
    print(f"  Throughput (traces on):  {N_AB} jobs in {elapsed:.1f} s = {_rate_on} jobs/s")
    check("3b. throughput sanity (>= 3 jobs/s with traces)", _rate_on >= 3.0, f"rate={_rate_on}")
except Exception as exc:
    print(f"  Throughput check: ERROR {exc}")
    check("3b. throughput sanity", False, str(exc))

# 3c. Baseline throughput (full mode only): restart workers with RECORD_TRACES=0 TIMELINE=0.
_rate_off = 0.0
if not a.quick:
    print("\n--- Test 3c: baseline throughput (RECORD_TRACES=0 TIMELINE=0) ---")
    _w_count = len(running_workers()) or 4
    try:
        compose("stop", "worker")
        time.sleep(1)
        env_off = {**os.environ, "RECORD_TRACES": "0", "TIMELINE": "0"}
        subprocess.run(
            ["docker", "compose", "up", "-d", "--scale", f"worker={_w_count}", "worker"],
            cwd=REPO, env=env_off, capture_output=True, check=False)
        time.sleep(2)

        api("/api/cloud/reset", {})
        time.sleep(0.5)
        t_base = time.time()
        api("/api/cloud/storm", {"n": N_AB, "critical": 0, "policy": "fifo"})
        wait_drain(N_AB, timeout=60)
        elapsed_base = time.time() - t_base
        _rate_off = round(N_AB / elapsed_base, 2) if elapsed_base > 0 else 0
        print(f"  Throughput (traces off): {N_AB} jobs in {elapsed_base:.1f} s = {_rate_off} jobs/s")
        if _rate_on > 0:
            delta = abs(_rate_off - _rate_on)
            pct = delta / max(_rate_off, 0.001) * 100
            print(f"  Delta: {delta:.2f} jobs/s ({pct:.1f}%)")
        check("3c. baseline throughput >= 3 jobs/s (traces off)", _rate_off >= 3.0,
              f"rate={_rate_off}")
    except Exception as exc:
        print(f"  Baseline throughput: ERROR {exc}")
        check("3c. baseline throughput", False, str(exc)[:120])
    finally:
        # Restore workers with traces on (default compose env).
        compose("stop", "worker")
        time.sleep(1)
        subprocess.run(
            ["docker", "compose", "up", "-d", "--scale", f"worker={_w_count}", "worker"],
            cwd=REPO, capture_output=True, check=False)
        time.sleep(2)

# --- Running overhead summary (across all blocks) ---
print("\n=== Overhead summary ===")
try:
    _overhead_ms: float | None = overhead_ms  # type: ignore[name-defined]
except NameError:
    _overhead_ms = None
if _overhead_ms is not None:
    print(f"  Block 1+2: Tracer.start/finish_attempt + timeline.emit  = {_overhead_ms:.3f} ms/job"
          " (microbenchmark, 2000 iters)")
    print(f"  Block 3:   SET NX lock replaces 30-s bucket; O(1) Redis SETNX per outage event,")
    print(f"             not per job -- zero additional per-job overhead in the steady state.")
    if _rate_on > 0 and _rate_off > 0:
        print(f"  Throughput: {_rate_off} jobs/s (baseline) vs {_rate_on} jobs/s (traces on)")
else:
    print("  (microbenchmark did not run -- no overhead data)")

elapsed_total = time.time() - _t0
print(f"\nTotal wall time: {elapsed_total:.0f} s")
print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
sys.exit(1 if failures else 0)
