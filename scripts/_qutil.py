"""Helpers shared by the in-container driver scripts (storm.py, bench_run.py)."""
import json
import math
import time
from datetime import datetime

from backend.cloud.queue import Queue

LIVE_BELOW_S = 10.0


def live_workers(q: Queue) -> int:
    beats = q.stats_snapshot()["worker_heartbeat_age_s"]
    return sum(age < LIVE_BELOW_S for age in beats.values())


def wait_for_workers(q: Queue, n: int, timeout: float = 60.0) -> int:
    """Block until >= n workers are live (or timeout). Returns the live count."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        live = live_workers(q)
        if live >= n:
            return live
        time.sleep(0.5)
    return live_workers(q)


def fetch_results(q: Queue, ids: list[str]) -> list[dict]:
    """Results that exist so far, for the given job ids."""
    raws = q.r.mget([q.k("result:" + i) for i in ids]) if ids else []
    return [json.loads(r) for r in raws if r is not None]


def latency_ms(result: dict) -> float:
    iso = datetime.fromisoformat
    return (iso(result["finished_at"]) - iso(result["enqueued_at"])).total_seconds() * 1000.0


def percentile(sorted_values: list[float], p: float) -> float:
    """Nearest-rank percentile of an already-sorted list."""
    return sorted_values[max(0, math.ceil(p / 100 * len(sorted_values)) - 1)]
