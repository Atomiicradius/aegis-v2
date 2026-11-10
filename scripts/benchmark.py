"""Benchmark sweep, run on the HOST (stdlib only; all Redis access is inside containers).

    python scripts/benchmark.py            # full: workers 1,2,4,8 x fifo,priority, N=300, K=3
    python scripts/benchmark.py --quick    # N=100, workers 1,4

Writes results/benchmark.csv (overwritten unless --append).
"""
import argparse
import csv
import json
import os
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
COLUMNS = ["workers", "workers_live", "workers_seen", "policy", "events", "critical", "stub_ms", "seed",
           "wall_s", "throughput_jobs_s", "avg_ms", "p95_ms", "p99_ms", "crit_avg_ms", "crit_max_ms",
           "errors", "retries_total", "failed", "mean_rounds"]

ap = argparse.ArgumentParser()
ap.add_argument("--quick", action="store_true")
ap.add_argument("--events", type=int)
ap.add_argument("--critical", type=int, default=3)
ap.add_argument("--seed", type=int, default=42)
ap.add_argument("--stub-ms", type=int, default=150)
ap.add_argument("--workers", type=int, nargs="+")
ap.add_argument("--out", default="results/benchmark.csv")
ap.add_argument("--append", action="store_true")
a = ap.parse_args()
events = a.events or (100 if a.quick else 300)
worker_counts = a.workers or ([1, 4] if a.quick else [1, 2, 4, 8])

env = {**os.environ, "LLM_STUB_MS": str(a.stub_ms)}


def compose(*args: str, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, env=env, text=True,
                          capture_output=capture, check=False)


def one_run(workers: int, policy: str) -> dict | None:
    for attempt in (1, 2):  # retry once if the workers were not all live in time
        p = compose("run", "--rm", "worker", "python", "scripts/bench_run.py",
                    "--events", str(events), "--policy", policy, "--workers-expected", str(workers),
                    "--critical", str(a.critical), "--critical-after-fraction", "0.3",
                    "--seed", str(a.seed), capture=True)
        lines = [l for l in p.stdout.splitlines() if l.startswith("{")]
        if p.returncode == 0 and lines:
            return json.loads(lines[-1])
        print(f"   attempt {attempt} failed (exit {p.returncode}): {p.stderr.strip().splitlines()[-1:]}", flush=True)
        time.sleep(10)
    return None


def flatten(r: dict) -> dict:
    return {"workers": r["workers"], "workers_live": r["workers_live"], "workers_seen": r["workers_seen"],
            "policy": r["policy"], "events": r["events"], "critical": r["critical"], "stub_ms": a.stub_ms,
            "seed": a.seed, "wall_s": r["wall_s"], "throughput_jobs_s": r["throughput_jobs_s"],
            "avg_ms": r["latency_ms"]["avg"], "p95_ms": r["latency_ms"]["p95"], "p99_ms": r["latency_ms"]["p99"],
            "crit_avg_ms": r["critical_latency_ms"]["avg"], "crit_max_ms": r["critical_latency_ms"]["max"],
            "errors": r["errors"], "retries_total": r["retries_total"], "failed": r["failed"],
            "mean_rounds": r["mean_rounds"]}


out = REPO / a.out
out.parent.mkdir(exist_ok=True)
new_file = a.append is False or not out.exists()
t_begin = time.time()
compose("build", "worker")
compose("up", "-d", "redis")
rows = []
with open(out, "w" if not a.append else "a", newline="") as f:
    w = csv.DictWriter(f, fieldnames=COLUMNS)
    if new_file:
        w.writeheader()
    for workers in worker_counts:
        compose("up", "-d", "--scale", f"worker={workers}", "worker")
        for policy in ("fifo", "priority"):
            print(f"== workers={workers} policy={policy} events={events}", flush=True)
            r = one_run(workers, policy)
            if r is None:
                print("   FAILED - no row written", flush=True)
                continue
            row = flatten(r)
            w.writerow(row)
            f.flush()
            rows.append(row)
            print(f"   {row['throughput_jobs_s']} jobs/s, critical avg {row['crit_avg_ms']} ms", flush=True)

print(f"\n{'W':>2} {'policy':<8} {'jobs/s':>7} {'avg ms':>7} {'p95':>7} {'p99':>7} {'crit avg':>9} {'crit max':>9} "
      f"{'err':>3} {'retry':>5} {'fail':>4} {'live':>4}")
for r in rows:
    print(f"{r['workers']:>2} {r['policy']:<8} {r['throughput_jobs_s']:>7} {r['avg_ms']:>7} {r['p95_ms']:>7} "
          f"{r['p99_ms']:>7} {r['crit_avg_ms']:>9} {r['crit_max_ms']:>9} {r['errors']:>3} "
          f"{r['retries_total']:>5} {r['failed']:>4} {r['workers_live']:>4}")
print(f"\n{len(rows)} rows -> {out}; total runtime {time.time() - t_begin:.0f}s")
sys.exit(0 if rows else 1)
