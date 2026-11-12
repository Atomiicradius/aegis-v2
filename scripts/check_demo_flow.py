"""5-step merge flow: reset, storm 800, autoscaler 2->8, inject 3 critical,
kill 1 worker with autoscaler stopped. Asserts completed==total and failed==0.

Usage (HOST):
    python scripts/check_demo_flow.py [--api http://localhost:8010]
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--api", default="http://localhost:8010")
a = ap.parse_args()
API = a.api

_checks: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    suffix = f" [{detail}]" if detail else ""
    print(f"  {tag} {label}{suffix}", flush=True)
    _checks.append((label, ok, detail))


def _get(path: str, timeout: int = 20) -> dict:
    req = urllib.request.Request(API + path)
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def _post(path: str, body: dict | None = None, timeout: int = 20) -> dict:
    data = json.dumps(body).encode() if body is not None else b""
    req = urllib.request.Request(
        API + path, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def stats() -> dict:
    return _get("/api/cloud/stats")


def live_workers(s: dict) -> int:
    w = s.get("workers", [])
    if isinstance(w, list):
        return sum(1 for x in w if x.get("alive", False))
    return 0


# ---------------------------------------------------------------------------
# Step 1: reset
# ---------------------------------------------------------------------------
print("\n=== STEP 1: reset ===")
_post("/api/cloud/reset")
time.sleep(4)  # let workers re-register heartbeats
s = stats()
lw = live_workers(s)
check("1a. reset -> completed_total==0", s["completed_total"] == 0, f"completed={s['completed_total']}")
check("1b. reset -> failed_total==0", s["failed_total"] == 0, f"failed={s['failed_total']}")
check("1c. reset -> queue_depth==0", s["queue_depth"] == 0, f"depth={s['queue_depth']}")
check("1d. workers still live after reset", lw >= 2, f"live={lw}")

# ---------------------------------------------------------------------------
# Step 2: storm 800
# ---------------------------------------------------------------------------
print("\n=== STEP 2: storm 800 (priority) ===")
_post("/api/cloud/storm", {"n": 800, "critical": 0, "policy": "priority"})
time.sleep(2)
s = stats()
lw = live_workers(s)
enq = s.get("enqueued_total", 0)
print(f"  t+2s: queue={s['queue_depth']}  enqueued={enq}  workers={lw}", flush=True)
check("2a. queue non-empty after storm", s["queue_depth"] > 0, f"depth={s['queue_depth']}")
# enqueued_total grows as the storm batches land; check after 8 s to let all batches arrive
time.sleep(6)
s = stats()
enq = s.get("enqueued_total", 0)
check("2b. enqueued_total >= 800 (all storm batches landed)", enq >= 800, f"enqueued={enq}")
lw = live_workers(s)
check("2c. 2 workers alive", lw >= 2, f"live={lw}")

# ---------------------------------------------------------------------------
# Step 3: autoscaler 2->8
# ---------------------------------------------------------------------------
print("\n=== STEP 3: autoscaler 2->8 ===")
proc = subprocess.Popen(
    [sys.executable, "scripts/autoscaler.py"],
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
)
time.sleep(15)
proc.terminate()
out, _ = proc.communicate(timeout=5)
scale_lines = [l.strip() for l in out.splitlines() if "SCALE" in l.upper()]
for l in scale_lines[:5]:
    print(f"  autoscaler: {l}", flush=True)
s = stats()
lw = live_workers(s)
print(f"  t+15s: workers={lw}  queue={s['queue_depth']}", flush=True)
check("3a. autoscaler scaled up >= 4 workers", lw >= 4, f"live={lw}")
check("3b. queue draining (depth < 800)", s["queue_depth"] < 800, f"depth={s['queue_depth']}")

# ---------------------------------------------------------------------------
# Step 4: inject 3 critical (refill queue if nearly empty)
# ---------------------------------------------------------------------------
print("\n=== STEP 4: inject 3 critical conjunctions ===")
if s["queue_depth"] < 50:
    print("  queue nearly empty; adding 400 more jobs before inject", flush=True)
    _post("/api/cloud/storm", {"n": 400, "critical": 0, "policy": "priority"})
    time.sleep(1)
    s = stats()
_post("/api/cloud/critical", {"k": 3})
time.sleep(5)
s = stats()
crit_lat = s.get("critical_latency_ms", {})
overall_lat = s.get("latency_ms", {})
print(f"  t+5s: critical_latency_ms={crit_lat}  overall={overall_lat}", flush=True)
# critical avg should be significantly lower than overall avg
crit_avg = crit_lat.get("avg", 0)
overall_avg = overall_lat.get("avg", 0)
crit_count = crit_lat.get("count", crit_lat.get("n", 0))
check("4a. critical injected (critical_latency_ms.count > 0)", crit_count > 0,
      f"count={crit_count}")
if overall_avg > 0 and crit_avg > 0:
    check("4b. critical avg latency < overall avg", crit_avg < overall_avg,
          f"critical={crit_avg}ms overall={overall_avg}ms")

# ---------------------------------------------------------------------------
# Step 5: kill 1 worker (autoscaler stopped), wait for full drain
# ---------------------------------------------------------------------------
print("\n=== STEP 5: kill 1 worker (autoscaler stopped), wait for drain ===")
subprocess.run(
    ["docker", "compose", "up", "-d", "--scale", "worker=4", "worker"],
    capture_output=True,
)
time.sleep(2)
result = subprocess.run(
    ["docker", "compose", "ps", "-q", "--status", "running", "worker"],
    capture_output=True, text=True,
)
ids = [x.strip() for x in result.stdout.strip().splitlines() if x.strip()]
killed = ids[-1] if ids else None
print(f"  Killing container {killed[:12] if killed else 'none'}", flush=True)
if killed:
    subprocess.run(["docker", "kill", killed], capture_output=True)
t_kill = time.time()
for i in range(120):
    time.sleep(2)
    s = stats()
    if s["queue_depth"] == 0 and s.get("processing", 0) == 0:
        elapsed = round(time.time() - t_kill)
        print(f"  t+{elapsed}s: queue drained", flush=True)
        break
    if i % 10 == 9:
        print(f"  t+{(i+1)*2}s: queue={s['queue_depth']} completed={s['completed_total']} "
              f"failed={s['failed_total']}", flush=True)

s = stats()
enqueued = s.get("enqueued_total", 0)
completed = s["completed_total"]
failed = s["failed_total"]
retries = s.get("retries_total", 0)
lw = live_workers(s)
print(f"  FINAL: enqueued={enqueued} completed={completed} failed={failed} "
      f"retries={retries} live_workers={lw}", flush=True)
check("5a. failed == 0", failed == 0, f"failed={failed}")
check("5b. completed == enqueued (no jobs lost)", completed == enqueued,
      f"completed={completed} enqueued={enqueued}")
check("5c. retries >= 1 (killed worker's in-flight job was requeued)", retries >= 1,
      f"retries={retries}")

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
passed = sum(1 for _, ok, _ in _checks if ok)
failed_n = sum(1 for _, ok, _ in _checks if not ok)
total = passed + failed_n
print(f"\nDemo flow gate: {passed}/{total} passed", flush=True)
if failed_n == 0:
    print("ALL PASS", flush=True)
    sys.exit(0)
else:
    print(f"{failed_n} FAILED", flush=True)
    sys.exit(1)
