"""
Queue-depth autoscaler for the `worker` compose service. Runs on the HOST, stdlib only.

    python scripts/autoscaler.py [--min 2] [--max 8] [--target 25] [--interval 2] [--cooldown 10] [--dry-run]
    python scripts/autoscaler.py --api http://localhost:8010   # post scale_up/scale_down to timeline

Every --interval seconds:
  load    = queue depth + jobs in flight           (read with redis-cli inside the redis container)
  desired = clamp(ceil(load / target), min, max)   ("one worker per `target` queued jobs")
  UP   : desired > current  -> scale straight to `desired`, immediately.
  DOWN : desired < current  -> only after that has held for --cooldown seconds, and then ONE worker
         at a time (the timer restarts after every step). Never below `processing` (each worker
         runs one job, so fewer workers than in-flight jobs would kill running work) or --min.
Ctrl+C exits without touching the worker count. --duration S exits by itself after S seconds.
"""
import argparse
import json
import math
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

ap = argparse.ArgumentParser()
ap.add_argument("--min", type=int, default=2, dest="min_w")
ap.add_argument("--max", type=int, default=8, dest="max_w")
ap.add_argument("--target", type=int, default=25, help="queued jobs per worker")
ap.add_argument("--interval", type=float, default=2.0)
ap.add_argument("--cooldown", type=float, default=10.0, help="seconds load must stay low before scaling down")
ap.add_argument("--dry-run", action="store_true", help="log decisions, change nothing")
ap.add_argument("--duration", type=float, default=0, help="exit after this many seconds (0 = run until Ctrl+C)")
ap.add_argument("--api", default="", help="cloud API URL for timeline events (empty = off)")
a = ap.parse_args()


def post_timeline(type_: str, detail: dict) -> None:
    """Post a scale event to the cloud API timeline. Best-effort: never raises."""
    if not a.api:
        return
    try:
        body = json.dumps({"type": type_, "source": "autoscaler", "detail": detail}).encode()
        req = urllib.request.Request(a.api + "/api/cloud/timeline", data=body,
                                     headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=2)
    except Exception:  # noqa: BLE001
        pass


def run(*args: str) -> str:
    """Run a docker compose command (arg list, no shell) and return stdout."""
    p = subprocess.run(["docker", "compose", *args], cwd=REPO, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"docker compose {' '.join(args)} failed: {p.stderr.strip()[-200:]}")
    return p.stdout


def redis_int(*cmd: str) -> int:
    return int(run("exec", "-T", "redis", "redis-cli", *cmd).strip())


def running_workers() -> int:
    return len(run("ps", "-q", "--status", "running", "worker").split())


def log(msg: str) -> None:
    print(f"{datetime.now():%H:%M:%S} {msg}", flush=True)


def scale(n: int) -> None:
    run("up", "-d", "--no-recreate", "--scale", f"worker={n}", "worker")


log(f"autoscaler: min={a.min_w} max={a.max_w} target={a.target}/worker interval={a.interval}s "
    f"cooldown={a.cooldown}s{' DRY-RUN' if a.dry_run else ''}")
below_since: float | None = None   # when `desired < current` started to hold
t_end = time.time() + a.duration if a.duration else None
try:
    while t_end is None or time.time() < t_end:
        t0 = time.time()
        try:
            depth, processing, current = redis_int("ZCARD", "jobs"), redis_int("HLEN", "processing"), running_workers()
            desired = max(a.min_w, min(a.max_w, math.ceil((depth + processing) / a.target)))
            action = "hold"
            if desired > current:
                below_since = None
                action = f"SCALE UP -> {desired}"
                if not a.dry_run:
                    scale(desired)
                    post_timeline("scale_up", {"from": current, "to": desired, "depth": depth, "processing": processing})
            elif desired < current:
                below_since = below_since or time.time()
                waited = time.time() - below_since
                floor = max(a.min_w, processing)
                if waited >= a.cooldown and current - 1 >= floor:
                    action = f"SCALE DOWN -> {current - 1} (low for {waited:.0f}s)"
                    below_since = None            # next step needs a fresh cooldown
                    if not a.dry_run:
                        scale(current - 1)
                        post_timeline("scale_down", {"from": current, "to": current - 1, "waited_s": round(waited, 1), "depth": depth})
                else:
                    action = f"hold (below for {waited:.0f}/{a.cooldown:.0f}s, floor {floor})"
            else:
                below_since = None
            log(f"depth={depth} processing={processing} workers {current} -> {desired}  {action}")
        except Exception as exc:  # noqa: BLE001 - keep the loop alive through a transient docker error
            log(f"ERROR {exc}")
        time.sleep(max(0.0, a.interval - (time.time() - t0)))
except KeyboardInterrupt:
    log("stopped (worker count left unchanged)")
