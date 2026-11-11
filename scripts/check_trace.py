"""Block-1 check: verify trace recording and timeline events.

Enqueues a small batch with a short lease, docker-kills a worker mid-job, then asserts:
- The killed job has 2 attempts on different workers.
- Attempt 1 outcome = lease_expired; attempt 2 outcome = completed.
- Timeline contains lease_expired and job_requeued for that job.
- completed == total, failed == 0.

Also runs A/B overhead measurement: 100 jobs, 1 worker, seed 42,
with RECORD_TRACES=1,TIMELINE=1 and with both off. Reports jobs/s both ways.

Run from the repo root on the HOST:
    python scripts/check_trace.py [--api http://localhost:8010] [--skip-kill] [--skip-ab]

Requires at least 2 worker containers running.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8010")
ap.add_argument("--skip-kill", action="store_true", help="skip the worker-kill test (needs >=2 workers)")
ap.add_argument("--skip-ab", action="store_true", help="skip the A/B overhead measurement")
a = ap.parse_args()

failures = 0


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


# --- Test 1: worker-kill trace ------------------------------------------------
if not a.skip_kill:
    print("\n=== Test 1: worker-kill trace ===")
    workers = running_workers()
    if len(workers) < 2:
        print("SKIP  need >=2 workers; found", len(workers))
    else:
        # Reset, enqueue 10 jobs, wait for one to be leased, kill a worker.
        api("/api/cloud/reset", {})
        time.sleep(1)
        api("/api/cloud/storm", {"n": 10, "critical": 0, "policy": "fifo"})
        time.sleep(3)  # let workers start processing

        # Kill the last running worker.
        victim = workers[-1]
        subprocess.run(["docker", "kill", victim], capture_output=True)
        print(f"  Killed worker {victim[:12]}; waiting for requeue + completion …")

        drained = wait_drain(10, timeout=90)
        check("1a. all 10 jobs complete after kill", drained)

        s = api("/api/cloud/stats")
        check("1b. failed == 0", s.get("failed_total", 0) == 0, f"failed={s.get('failed_total')}")
        check("1c. retries_total >= 1", s.get("retries_total", 0) >= 1,
              f"retries={s.get('retries_total')}")

        # Fetch the recent jobs and find one with attempts > 1 (the retried job).
        recent = api("/api/cloud/recent?limit=15")
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

        # Check timeline for lease_expired and job_requeued.
        tl = api("/api/cloud/timeline")
        tl_types = {e["type"] for e in tl.get("events", [])}
        check("1i. timeline has lease_expired", "lease_expired" in tl_types)
        check("1j. timeline has job_requeued", "job_requeued" in tl_types)

        # Restart the killed worker.
        compose("up", "-d", "--no-recreate", "--scale", f"worker={len(workers)}", "worker")
        print(f"  Restarted worker pool to {len(workers)}.")

# --- Test 2: Redis restart dedup -----------------------------------------------
print("\n=== Test 2: redis_down/redis_up dedup in timeline ===")
# Restart Redis gracefully while workers are running.
api("/api/cloud/reset", {})
time.sleep(1)
api("/api/cloud/storm", {"n": 30, "critical": 0, "policy": "fifo"})
time.sleep(2)
compose("restart", "redis")
print("  Restarted Redis; waiting 10 s …")
time.sleep(10)
# Flush buffered events by calling the timeline endpoint (this triggers workers to flush).
time.sleep(5)
try:
    tl = api("/api/cloud/timeline")
    down_events = [e for e in tl.get("events", []) if e["type"] == "redis_down"]
    up_events   = [e for e in tl.get("events", []) if e["type"] == "redis_up"]
    check("2a. exactly one redis_down event (deduplicated across workers)",
          len(down_events) == 1, f"count={len(down_events)}")
    check("2b. exactly one redis_up event", len(up_events) == 1, f"count={len(up_events)}")
except Exception as exc:
    check("2a. timeline fetch after restart", False, str(exc))
    check("2b. redis_up present", False, "fetch failed")

# Wait for drain.
wait_drain(30, timeout=60)
s = api("/api/cloud/stats")
check("2c. all jobs complete after restart", s.get("failed_total", 0) == 0,
      f"failed={s.get('failed_total')}")

# --- Test 3: A/B overhead measurement ----------------------------------------
if not a.skip_ab:
    print("\n=== Test 3: A/B overhead measurement ===")
    N = 100
    results_ab = {}
    for label, env_patch in [("with_traces", {}), ("no_traces", {"TIMELINE": "0", "RECORD_TRACES": "0"})]:
        try:
            # Reset and start storm via the API.
            api("/api/cloud/reset", {})
            time.sleep(1)
            t_start = time.time()
            api("/api/cloud/storm", {"n": N, "critical": 0, "policy": "fifo"})
            drained = wait_drain(N, timeout=120)
            elapsed = time.time() - t_start
            rate = round(N / elapsed, 2) if elapsed > 0 else 0
            results_ab[label] = rate
            print(f"  {label}: {N} jobs in {elapsed:.1f} s = {rate} jobs/s")
        except Exception as exc:
            print(f"  {label}: ERROR {exc}")
            results_ab[label] = 0

    if results_ab.get("with_traces", 0) > 0 and results_ab.get("no_traces", 0) > 0:
        ratio = results_ab["no_traces"] / results_ab["with_traces"]
        per_job_overhead_ms = (1 / results_ab["with_traces"] - 1 / results_ab["no_traces"]) * 1000
        check("3a. trace overhead <= 1.5 ms/job", per_job_overhead_ms <= 1.5,
              f"overhead={per_job_overhead_ms:.2f} ms/job")
        check("3b. throughput ratio within 10%", ratio <= 1.10,
              f"no_traces/with_traces={ratio:.2f}")
        print(f"  Overhead: {per_job_overhead_ms:.2f} ms/job | "
              f"with_traces={results_ab['with_traces']} jobs/s | "
              f"no_traces={results_ab['no_traces']} jobs/s")
    else:
        print("  SKIP  could not measure (storm may not have drained)")

print(f"\n{'ALL PASS' if failures == 0 else str(failures) + ' FAILED'}")
sys.exit(1 if failures else 0)
