"""Measure process_job cost vs pool size and validation set (run in the image, LLM_MODE=off).

    docker compose run --rm worker python scripts/measure_pool.py

50 seeded jobs per cell; prints mean / p95 ms per job. Not part of the demo.
"""
import os
import random
import statistics

os.environ["LLM_MODE"] = "off"
os.environ["EVENT_POOL"] = "pool"
import _common  # noqa: F401,E402  (makes backend.* importable)
from backend.app import profiles  # noqa: E402
from backend.cloud.events import random_job  # noqa: E402
from backend.cloud import jobs as jobs_mod  # noqa: E402
from backend.cloud.jobs import process_job  # noqa: E402

full = profiles.load_all_tracked_objects()
curated = profiles.curated_ids()
pool_only = [o for o in full if o.norad_id not in curated]
docked_extra = [o for o in full if o.norad_id in curated]


def subset(n: int) -> list:
    if n <= 15:
        return docked_extra
    take = n - len([o for o in docked_extra])  # curated always present; pool fills the rest
    step = len(pool_only) / max(1, take)
    return docked_extra + [pool_only[int(i * step)] for i in range(take)]


def run(objects: list, mode: str, k: str = "50") -> tuple[float, float, float, float]:
    profiles._tracked_objects = objects
    jobs_mod._NEIGHBORS.clear()          # neighbour lists depend on the object set
    os.environ["VALIDATION_SET"], os.environ["VALIDATION_K"] = mode, k
    rng = random.Random(7)
    jobs = [random_job(rng, objects) for _ in range(50)]
    ms, rounds, ok = [], [], 0
    for j in jobs:
        r = process_job(j)
        ms.append(r["duration_ms"])
        rounds.append(r["rounds"])
        ok += r["resolution_status"].startswith("approved")
    ms.sort()
    return statistics.mean(ms), ms[int(0.95 * len(ms)) - 1], statistics.mean(rounds), ok / len(jobs)


print(f"{'objects':>7} {'validation':<22} {'mean ms':>8} {'p95 ms':>8} {'rounds':>7} {'approved':>9}")
for n in (15, 100, 250, 400):
    objs = subset(n)
    for label, mode in (("(a) full", "full"), ("(b) curated+pair", "curated"), ("(c) bounded K=50", "bounded")):
        if n == 15 and mode != "full":
            continue
        mean, p95, rnd, ok = run(objs, mode)
        print(f"{len(objs):>7} {label:<22} {mean:>8.1f} {p95:>8.1f} {rnd:>7.2f} {ok:>9.0%}")
