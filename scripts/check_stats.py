"""Poll the stats API once a second and print the numbers (stdlib only, run on the HOST).

    python scripts/check_stats.py [--storm 400] [--seconds 20] [--url http://localhost:8000]
--storm N first POSTs a priority storm of N jobs (with 3 critical after 5 s).
"""
import argparse
import json
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--url", default="http://localhost:8000")
ap.add_argument("--seconds", type=int, default=20)
ap.add_argument("--storm", type=int, default=0)
a = ap.parse_args()

if a.storm:
    body = json.dumps({"n": a.storm, "critical": 3, "critical_after_s": 5, "policy": "priority"}).encode()
    req = urllib.request.Request(a.url + "/api/cloud/storm", body, {"Content-Type": "application/json"})
    print("POST storm ->", urllib.request.urlopen(req).read().decode())

for t in range(a.seconds):
    s = json.load(urllib.request.urlopen(a.url + "/api/cloud/stats"))
    live = sum(w["alive"] for w in s["workers"])
    print(f"t={t:2d}s depth={s['queue_depth']:4d} processing={s['processing']:2d} completed={s['completed_total']:4d} "
          f"throughput={s['throughput_per_s']:5.2f}/s live_workers={live} "
          f"crit_done={s['critical_latency_ms']['count']} p95={s['latency_ms']['p95']}ms policy={s['policy']}")
    time.sleep(1)
