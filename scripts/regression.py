"""Regression gate, run on the HOST (stdlib only). Needs Docker Desktop running.

    python scripts/regression.py [--api http://localhost:8010] [--events 300] [--critical 3]

Resets the cloud stack through its API, runs a 300-event priority storm with 3 critical
events injected ~5 s in, waits for the system to drain (timeout 3 min) and prints PASS/FAIL
for each check. Exit code 1 if any check fails. Do not run it while demoing: it FLUSHES Redis.
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
ap.add_argument("--events", type=int, default=300)
ap.add_argument("--critical", type=int, default=3)
ap.add_argument("--critical-after", type=float, default=5.0)
ap.add_argument("--timeout", type=float, default=180.0)
a = ap.parse_args()


def call(path: str, body: dict | None = None) -> dict:
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(a.api + path, data=data, method="GET" if body is None else "POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, text=True, capture_output=True, check=False)


def running_workers() -> int:
    return len(compose("ps", "-q", "--status", "running", "worker").stdout.split())


def live_workers() -> int:
    return sum(w["alive"] for w in call("/api/cloud/stats")["workers"])


# --- make sure the stack is up (never scale an already bigger worker pool down) ---------------
p = compose("up", "-d", "redis", "api")
if p.returncode != 0:
    sys.exit(f"docker compose up failed:\n{p.stderr}")
if running_workers() < 2:
    compose("up", "-d", "--scale", "worker=2", "worker")
for _ in range(30):
    try:
        call("/api/cloud/stats")
        break
    except Exception:  # noqa: BLE001 - API still starting
        time.sleep(1)
else:
    sys.exit("API never answered on " + a.api)

call("/api/cloud/reset", {})
# Jobs that were in flight at the first flush finish AFTER it and add stray completions; let them land, flush again.
time.sleep(2)
call("/api/cloud/reset", {})
for _ in range(20):          # heartbeats re-register within ~2 s after the FLUSHDB
    if live_workers() >= 2:
        break
    time.sleep(1)

# --- storm -------------------------------------------------------------------------------------
expected = a.events + a.critical
t0 = time.time()
call("/api/cloud/storm", {"n": a.events, "critical": a.critical,
                          "critical_after_s": a.critical_after, "policy": "priority"})
print(f"storm started: {a.events} events + {a.critical} critical @ +{a.critical_after:.0f}s, "
      f"workers running={running_workers()}", flush=True)

s = call("/api/cloud/stats")
while time.time() - t0 < a.timeout:
    time.sleep(1)
    s = call("/api/cloud/stats")
    done = s["completed_total"] + s["failed_total"]
    if time.time() - t0 > a.critical_after + 1 and done >= expected and s["queue_depth"] == 0 and s["processing"] == 0:
        break
wall = time.time() - t0
live = sum(w["alive"] for w in s["workers"])

crit_avg, avg = s["critical_latency_ms"]["avg"], s["latency_ms"]["avg"]
checks = [
    (f"completed == {expected}", s["completed_total"] == expected, f"completed={s['completed_total']}"),
    ("failed == 0", s["failed_total"] == 0, f"failed={s['failed_total']}"),
    ("queue == 0 and processing == 0", s["queue_depth"] == 0 and s["processing"] == 0,
     f"queue={s['queue_depth']} processing={s['processing']}"),
    ("critical avg latency < overall avg / 5", s["critical_latency_ms"]["count"] > 0 and crit_avg < avg / 5,
     f"critical avg={crit_avg} ms (n={s['critical_latency_ms']['count']}), overall avg={avg} ms, limit={avg / 5:.0f} ms"),
    ("live workers >= 2", live >= 2, f"live={live}"),
]
print(f"\ndrained in {wall:.1f}s -> {s['completed_total'] / wall:.2f} jobs/s overall; retries={s['retries_total']}, "
      f"p95={s['latency_ms']['p95']} ms\n")
for name, ok, detail in checks:
    print(f"{'PASS' if ok else 'FAIL'}  {name}   ({detail})")
failed = [c for c in checks if not c[1]]
print(f"\nREGRESSION {'PASS' if not failed else 'FAIL (' + str(len(failed)) + ' check(s))'}")
sys.exit(1 if failed else 0)
