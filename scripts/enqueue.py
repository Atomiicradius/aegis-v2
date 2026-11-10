"""Enqueue synthetic jobs.

    python scripts/enqueue.py --n 100 --policy priority [--critical 1 --critical-after 50] [--seed 1]

--n is the number of NORMAL jobs; critical jobs are extra and are inserted after
the first --critical-after normal jobs. Scores come from backend/cloud/scoring.py.
"""
import argparse
import os
import random

import _common  # noqa: F401
from backend.app.profiles import load_all_tracked_objects
from backend.cloud.events import make_critical_job, random_job
from backend.cloud.queue import Queue


def enqueue_batch(q: Queue, n: int, policy: str, critical: int = 0,
                  critical_after: int | None = None, seed: int = 1) -> list:
    """Build all jobs first, then enqueue them in one fast burst. Returns the jobs."""
    rng = random.Random(seed)
    objects = load_all_tracked_objects()
    after = n if critical_after is None else critical_after
    jobs = []
    for i in range(n + critical):
        is_crit = after <= i < after + critical
        jobs.append(make_critical_job(rng, objects) if is_crit else random_job(rng, objects))
    q.submit(jobs, policy)
    q.r.set(q.k("meta:policy"), policy)  # shown on the dashboard
    return jobs


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, required=True)
    ap.add_argument("--policy", choices=["fifo", "priority"], default=os.environ.get("POLICY", "fifo"))
    ap.add_argument("--critical", type=int, default=0)
    ap.add_argument("--critical-after", type=int, default=None)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args()
    q = Queue()
    jobs = enqueue_batch(q, a.n, a.policy, a.critical, a.critical_after, a.seed)
    print(f"enqueued {len(jobs)} jobs ({a.critical} critical), policy={a.policy}, "
          f"depth now {q.stats_snapshot()['depth']}")
