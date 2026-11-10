"""Live-demo driver: a conjunction storm with critical events injected mid-storm.

    docker compose run --rm worker python scripts/storm.py --n 600 --policy priority \
        --critical 3 --critical-after 8 --watch [--reset] [--burst-seconds 2] [--seed 1]

N normal jobs are enqueued over --burst-seconds (pipelined chunks); K critical jobs
are enqueued --critical-after seconds after the storm starts. Runs until every job
has a result. Without --reset the existing Redis state is left alone.
"""
import argparse
import math
import os
import random
import threading
import time

import _common  # noqa: F401
from _qutil import fetch_results, latency_ms, live_workers
from backend.app.profiles import load_all_tracked_objects
from backend.cloud.events import make_critical_job, random_job
from backend.cloud.queue import Queue

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, default=1000)
ap.add_argument("--policy", choices=["fifo", "priority"], default=os.environ.get("POLICY", "fifo"))
ap.add_argument("--seed", type=int, default=1)
ap.add_argument("--burst-seconds", type=float, default=2.0)
ap.add_argument("--critical", type=int, default=0)
ap.add_argument("--critical-after", type=float, default=8.0)
ap.add_argument("--watch", action="store_true")
ap.add_argument("--reset", action="store_true")
ap.add_argument("--timeout", type=float, default=900)
a = ap.parse_args()

q = Queue()
if a.reset:
    q.r.flushdb()
    time.sleep(3)  # worker heartbeat threads re-register within 2 s of the flush

rng = random.Random(a.seed)
objects = load_all_tracked_objects()
normals = [random_job(rng, objects) for _ in range(a.n)]
crits = [make_critical_job(rng, objects) for _ in range(a.critical)]
all_ids = [j.event_id for j in normals + crits]
crit_ids = [j.event_id for j in crits]
t0 = time.time()


def produce() -> None:
    chunks = max(1, int(a.burst_seconds * 10))           # ~10 pipelined chunks per second
    size = math.ceil(a.n / chunks)
    for i in range(chunks):
        q.submit(normals[i * size:(i + 1) * size], a.policy)
        time.sleep(max(0.0, t0 + (i + 1) * a.burst_seconds / chunks - time.time()))
    if crits:
        time.sleep(max(0.0, t0 + a.critical_after - time.time()))
        q.submit(crits, a.policy)
        print(f"*** t={time.time() - t0:.0f}s: {len(crits)} CRITICAL jobs enqueued ({a.policy})", flush=True)


threading.Thread(target=produce, daemon=True).start()

prev_done, tick = 0, 0
while time.time() - t0 < a.timeout:
    time.sleep(max(0.0, t0 + (tick + 1) - time.time()))
    tick += 1
    results = fetch_results(q, all_ids)
    done = len(results)
    if a.watch:
        s = q.stats_snapshot()
        crit_done = sum(r["event_id"] in set(crit_ids) for r in results)
        print(f"t={tick:3d}s depth={s['depth']:4d} processing={s['processing']:2d} completed={done:4d} "
              f"workers={live_workers(q)} rate={done - prev_done:3d}/s critical={crit_done}/{len(crits)}", flush=True)
    prev_done = done
    if done >= len(all_ids):
        break

results = fetch_results(q, all_ids)
by_id = {r["event_id"]: r for r in results}
print(f"\ndone: {len(results)}/{len(all_ids)} jobs in {time.time() - t0:.1f}s, policy={a.policy}")
crit_lat = [latency_ms(by_id[i]) for i in crit_ids if i in by_id]
norm_lat = [latency_ms(by_id[j.event_id]) for j in normals if j.event_id in by_id]
if crit_lat:
    print(f"critical latency ms: avg={sum(crit_lat) / len(crit_lat):.0f} max={max(crit_lat):.0f} "
          f"each={[round(x) for x in crit_lat]}")
print(f"normal   latency ms: avg={sum(norm_lat) / max(1, len(norm_lat)):.0f} max={max(norm_lat, default=0):.0f}")
