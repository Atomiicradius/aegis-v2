"""Wait for N completed jobs, then print jobs/s = N / (last finished - first started).

    python scripts/throughput.py --n 200
"""
import argparse
import json
import time
from datetime import datetime

import _common  # noqa: F401
from backend.cloud.queue import Queue

ap = argparse.ArgumentParser()
ap.add_argument("--n", type=int, required=True)
ap.add_argument("--timeout", type=float, default=300)
n = ap.parse_args().n

q = Queue()
deadline = time.time() + ap.parse_args().timeout
while q.stats_snapshot()["completed"] < n and time.time() < deadline:
    time.sleep(0.5)

results = [json.loads(q.r.get(k)) for k in q.r.scan_iter("result:*")]
ts = datetime.fromisoformat
span = (max(ts(r["finished_at"]) for r in results) - min(ts(r["started_at"]) for r in results)).total_seconds()
workers = sorted({r["worker_id"] for r in results})
print(f"jobs={len(results)} workers_used={len(workers)} wall={span:.1f}s throughput={len(results) / span:.2f} jobs/s "
      f"retries={sum(r['retries'] for r in results)}")
