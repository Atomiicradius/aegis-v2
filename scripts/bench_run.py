"""One benchmark run, inside the compose network. Prints ONE JSON line.

    docker compose run --rm worker python scripts/bench_run.py --events 300 --policy priority \
        --workers-expected 4 --critical 3 --critical-after-fraction 0.3 --seed 42

Flow: reset Redis -> wait for W live workers -> enqueue N normal jobs ->
when 30% of them are done, enqueue K critical jobs (queue is deep) -> wait for all.
"""
import argparse
import json
import random
import statistics
import sys
import time
from datetime import datetime

import _common  # noqa: F401
from _qutil import fetch_results, latency_ms, percentile, wait_for_workers
from backend.app.profiles import load_all_tracked_objects
from backend.cloud.events import make_critical_job, random_job
from backend.cloud.queue import Queue

ap = argparse.ArgumentParser()
ap.add_argument("--events", type=int, required=True)
ap.add_argument("--policy", choices=["fifo", "priority"], required=True)
ap.add_argument("--workers-expected", type=int, required=True)
ap.add_argument("--critical", type=int, default=3)
ap.add_argument("--critical-after-fraction", type=float, default=0.3)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--timeout", type=float, default=300)
a = ap.parse_args()

q = Queue()
q.r.flushdb()
q.r.set(q.k("meta:policy"), a.policy)
time.sleep(3)  # heartbeat threads re-register after the flush
live = wait_for_workers(q, a.workers_expected)
if live < a.workers_expected:
    print(f"only {live}/{a.workers_expected} workers live", file=sys.stderr)
    sys.exit(2)

rng = random.Random(a.seed)
objects = load_all_tracked_objects()
normals = [random_job(rng, objects) for _ in range(a.events)]
crits = [make_critical_job(rng, objects) for _ in range(a.critical)]
normal_ids = [j.event_id for j in normals]
crit_ids = [j.event_id for j in crits]

t_start = time.time()
q.submit(normals, a.policy)
trigger = int(a.critical_after_fraction * a.events)
deadline = t_start + a.timeout
while len(fetch_results(q, normal_ids)) < trigger and time.time() < deadline:
    time.sleep(0.1)
q.submit(crits, a.policy)
all_ids = normal_ids + crit_ids
while len(fetch_results(q, all_ids)) < len(all_ids) and time.time() < deadline:
    time.sleep(0.25)

results = fetch_results(q, all_ids)
if len(results) < len(all_ids):
    print(f"timeout: {len(results)}/{len(all_ids)} done", file=sys.stderr)
    sys.exit(3)

iso = datetime.fromisoformat
wall = (max(iso(r["finished_at"]) for r in results) - iso(min(r["enqueued_at"] for r in results))).total_seconds()
lat = sorted(latency_ms(r) for r in results)
crit_lat = [latency_ms(r) for r in results if r["event_id"] in set(crit_ids)]
s = q.stats_snapshot()
print(json.dumps({
    "workers": a.workers_expected,
    "workers_live": live,
    "workers_seen": len({r["worker_id"] for r in results}),
    "policy": a.policy,
    "events": a.events,
    "critical": a.critical,
    "wall_s": round(wall, 2),
    "throughput_jobs_s": round(len(results) / wall, 2),
    "latency_ms": {"avg": round(statistics.mean(lat)), "p95": round(percentile(lat, 95)),
                   "p99": round(percentile(lat, 99))},
    "critical_latency_ms": {"avg": round(statistics.mean(crit_lat)), "max": round(max(crit_lat))},
    "errors": sum(r["resolution_status"] == "error" for r in results),
    "retries_total": s["retries_total"],
    "failed": s["failed"],
    "mean_rounds": round(statistics.mean(r["rounds"] for r in results if "rounds" in r), 2),
}))
