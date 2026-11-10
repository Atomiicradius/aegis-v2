"""Chaos benchmark, run on the HOST (stdlib only). Needs Docker Desktop. FLUSHES Redis; stop the autoscaler first.

    python scripts/chaos.py --quick          # 1 run per scenario, 200 events (~6 min)
    python scripts/chaos.py                  # 3 runs per scenario, 400 events (~35 min)
    python scripts/chaos.py --scenarios kill1 redis_kill --runs 1

Every run: reset, 4 workers (stub 150 ms, lease 10 s, priority), storm, fault ~6 s later, then watch the
stats API once a second until the queue is drained. Killed workers are NOT restarted: we measure how
fast the REMAINING capacity recovers the work, not how fast capacity comes back.
A run passes only if jobs lost == 0 and failed == 0. One row per run is appended to results/chaos.csv.
"""
import argparse
import csv
import json
import os
import statistics
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCENARIOS = {  # name -> (label, workers to kill or None for a redis fault)
    "kill1": ("kill 1 of 4 workers", 1),
    "kill2": ("kill 2 of 4 workers", 2),
    "kill3": ("kill 3 of 4 workers", 3),
    "redis_restart": ("graceful redis restart", None),
    "redis_kill": ("hard redis kill", None),
}
COLUMNS = ["timestamp", "scenario", "run", "events", "expected", "completed", "failed", "jobs_lost", "retries_total",
           "first_retry_s", "reconnect_s", "drain_s", "max_depth", "processing_left", "result_keys", "error_results",
           "fault_at_s", "passed", "anomalies"]

ap = argparse.ArgumentParser()
ap.add_argument("--quick", action="store_true", help="1 run per scenario, 200 events")
ap.add_argument("--runs", type=int, default=3)
ap.add_argument("--events", type=int, default=400)
ap.add_argument("--fault-after", type=float, default=6.0, help="seconds after the storm starts")
ap.add_argument("--timeout", type=float, default=360.0, help="per run, seconds")
ap.add_argument("--scenarios", nargs="+", choices=list(SCENARIOS), default=list(SCENARIOS))
ap.add_argument("--api", default="http://localhost:8010")
ap.add_argument("--out", default="results/chaos.csv")
a = ap.parse_args()
if a.quick:
    a.runs, a.events = 1, 200

# Compose reads these at (re)creation time; shell env beats .env.
ENV = {**os.environ, "LLM_MODE": "stub", "LLM_STUB_MS": "150", "LEASE_SECONDS": "10", "POLICY": "priority"}


# ---- small helpers ------------------------------------------------------------------------------
def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, env=ENV, text=True, encoding="utf-8",
                          errors="replace", capture_output=True, check=False)


def docker(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", *args], cwd=REPO, text=True, encoding="utf-8", errors="replace",
                          capture_output=True, check=False)


def http(path: str, body: dict | None = None) -> tuple[int, dict | None]:
    """(status, json) - status 0 if the API did not answer at all. Never raises."""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(a.api + path, data=data, headers={"Content-Type": "application/json"},
                                 method="GET" if body is None else "POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read())
        except Exception:  # noqa: BLE001
            return e.code, None
    except Exception:  # noqa: BLE001
        return 0, None


def stats() -> tuple[int, dict | None]:
    return http("/api/cloud/stats")


def running_workers() -> list[str]:
    return compose("ps", "-q", "--status", "running", "worker").stdout.split()


def live(s: dict) -> int:
    return sum(w["alive"] for w in s["workers"])


def wait_for(cond, timeout: float, what: str) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if cond():
            return
        time.sleep(1)
    raise RuntimeError(f"timeout waiting for {what}")


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def sec(v) -> str:
    return v if v == "n/a" else f"{v}s"


def fmt(x, nd=1) -> str:
    return "n/a" if x is None else (f"{x:.{nd}f}" if isinstance(x, float) else str(x))


# ---- setup / teardown ---------------------------------------------------------------------------
def autoscaler_running() -> bool:
    """Best effort (Windows): is `autoscaler.py` running? It would restart the workers we kill."""
    try:
        ps = subprocess.run(["powershell", "-NoProfile", "-Command",
                             "(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'autoscaler\\.py' }"
                             " | Measure-Object).Count"], capture_output=True, text=True, timeout=20, check=False)
        return ps.stdout.strip() not in ("", "0")
    except Exception:  # noqa: BLE001
        return False


def first_setup() -> None:
    if autoscaler_running():
        sys.exit("The autoscaler is running: stop it (Ctrl+C) first, it would restart the workers we kill.")
    # Recreate api+workers once so the env above (stub 150 ms, lease 10 s, priority) is really in effect.
    for cmd in (["up", "-d", "redis"], ["up", "-d", "--force-recreate", "--scale", "worker=4", "api", "worker"]):
        p = compose(*cmd)
        if p.returncode:
            sys.exit(f"docker compose {' '.join(cmd)} failed:\n{p.stderr}")
    wait_for(lambda: stats()[0] == 200, 60, "the API")
    env = compose("exec", "-T", "worker", "sh", "-c", "echo $LEASE_SECONDS $LLM_STUB_MS $POLICY $LLM_MODE").stdout.split()
    if env != ["10", "150", "priority", "stub"]:
        sys.exit(f"worker env is {env}, expected ['10', '150', 'priority', 'stub']")


def prepare() -> None:
    """Clean start of one run: redis + api up, 4 workers running, reset, 4 live workers."""
    for cmd in (["up", "-d", "redis", "api"], ["up", "-d", "--scale", "worker=4", "worker"]):
        compose(*cmd)
    wait_for(lambda: stats()[0] == 200, 60, "the API after (re)start")
    http("/api/cloud/reset", {})
    wait_for(lambda: (stats()[1] is not None and live(stats()[1]) >= 4), 45, "4 live workers")


def restore() -> None:
    """Back to the demo baseline: 2 workers and an empty queue. Never raises."""
    try:
        compose("up", "-d", "redis", "api")
        compose("up", "-d", "--scale", "worker=2", "worker")
        wait_for(lambda: stats()[0] == 200, 60, "the API")
        http("/api/cloud/reset", {})
    except Exception as exc:  # noqa: BLE001
        print(f"WARNING: restore failed ({exc}); run: docker compose up -d --scale worker=2 worker", file=sys.stderr)


def inspect_redis() -> tuple[int | None, int | None]:
    """(number of result:* keys, number of results whose status is 'error'), straight from Redis."""
    lua = ("local n,e=0,0 for _,k in ipairs(redis.call('KEYS','result:*')) do n=n+1 "
           "if cjson.decode(redis.call('GET',k)).resolution_status=='error' then e=e+1 end end return {n,e}")
    for _ in range(5):
        p = compose("exec", "-T", "redis", "redis-cli", "EVAL", lua, "0")
        nums = [t for t in p.stdout.replace(")", " ").split() if t.isdigit()]
        if p.returncode == 0 and len(nums) >= 2:
            return int(nums[-2]), int(nums[-1])
        time.sleep(2)
    return None, None


# ---- one run ------------------------------------------------------------------------------------
def one_run(scenario: str, run_no: int) -> dict:
    label, kill_n = SCENARIOS[scenario]
    prepare()
    expected = a.events
    row = {c: "" for c in COLUMNS}
    row.update(timestamp=datetime.now().isoformat(timespec="seconds"), scenario=scenario, run=run_no,
               events=a.events, expected=expected)
    anomalies: list[str] = []
    fault = {"start": None, "done": None}

    def inject() -> None:
        time.sleep(a.fault_after)
        fault["start"] = time.time()
        if kill_n:
            ids = running_workers()[-kill_n:]
            docker("kill", *ids)
        elif scenario == "redis_restart":
            compose("restart", "redis")
        else:  # hard kill, then bring the SAME container back
            rid = compose("ps", "-q", "redis").stdout.split()
            docker("kill", *rid)
            compose("up", "-d", "redis")
        fault["done"] = time.time()

    t0 = time.time()
    code, _ = http("/api/cloud/storm", {"n": a.events, "critical": 0, "critical_after_s": 0, "policy": "priority"})
    if code != 200:
        anomalies.append(f"storm POST returned {code}")
    threading.Thread(target=inject, daemon=True).start()

    max_depth, first_retry, reconnect, drain = 0, None, None, None
    seen_fail, idle_streak, last = False, 0, None
    while time.time() - t0 < a.timeout:
        time.sleep(1)
        now = time.time()
        code, s = stats()
        fs, fd = fault["start"], fault["done"]
        if code not in (200, 503, 0):
            anomalies.append(f"API HTTP {code}")
        if code == 0:
            anomalies.append("API unreachable")
        if code != 200 or s is None:
            if fs:
                seen_fail = True
            idle_streak = 0
            continue
        last = s
        max_depth = max(max_depth, s["queue_depth"])
        if fs and first_retry is None and s["retries_total"] > 0:
            first_retry = now - fs
        # reconnect = first good answer after an observed failure (or after the fault command finished)
        if scenario.startswith("redis") and fs and reconnect is None and (seen_fail or fd):
            reconnect = now - fs
        done = s["completed_total"] + s["failed_total"]
        empty = s["queue_depth"] == 0 and s["processing"] == 0
        if fd and now - t0 > 5 and empty:
            if done >= expected:
                drain = now - fs
                break
            idle_streak += 1                      # empty but short of `expected`: jobs vanished (lost)
            if idle_streak >= 8:
                drain = now - fs - idle_streak
                anomalies.append(f"settled {expected - done} short of expected (lost work)")
                break
        else:
            idle_streak = 0
    if drain is None:
        anomalies.append(f"TIMEOUT after {a.timeout:.0f}s")
    # fault thread may still be running if the loop timed out early; give it a moment
    for _ in range(30):
        if fault["done"]:
            break
        time.sleep(1)

    # final numbers (retry a few times: redis may just have returned)
    for _ in range(10):
        code, s2 = stats()
        if code == 200 and s2:
            last = s2
            break
        time.sleep(1)
    keys, errs = inspect_redis()
    c = last["completed_total"] if last else 0
    f = last["failed_total"] if last else 0
    lost = expected - (c + f)
    if keys is not None and keys != c + f:
        anomalies.append(f"result keys ({keys}) != completed+failed ({c + f})")
    if errs:
        anomalies.append(f"{errs} results with status 'error'")
    if last and kill_n and not any(not w["alive"] for w in last["workers"]):
        anomalies.append("no dead worker visible in stats")
    row.update(completed=c, failed=f, jobs_lost=lost, retries_total=last["retries_total"] if last else "",
               first_retry_s=fmt(first_retry) if first_retry is not None else "n/a",
               reconnect_s=fmt(reconnect) if reconnect is not None else "n/a",
               drain_s=fmt(drain) if drain is not None else "n/a", max_depth=max_depth,
               processing_left=last["processing"] if last else "", result_keys="" if keys is None else keys,
               error_results="" if errs is None else errs,
               fault_at_s=fmt(fault["start"] - t0) if fault["start"] else "n/a",
               passed=(lost == 0 and f == 0 and drain is not None), anomalies="; ".join(dict.fromkeys(anomalies)))
    return row


# ---- main ---------------------------------------------------------------------------------------
def main() -> int:
    began = time.time()
    out = REPO / a.out
    out.parent.mkdir(exist_ok=True)
    new = not out.exists()
    rows: list[dict] = []
    first_setup()
    try:
        with open(out, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            if new:
                w.writeheader()
            for scenario in a.scenarios:
                for i in range(1, a.runs + 1):
                    print(f"== {scenario} run {i}/{a.runs}: {SCENARIOS[scenario][0]}", flush=True)
                    try:
                        row = one_run(scenario, i)
                    except Exception as exc:  # noqa: BLE001 - a harness problem must not stop the sweep
                        row = {c: "" for c in COLUMNS}
                        row.update(timestamp=datetime.now().isoformat(timespec="seconds"), scenario=scenario, run=i,
                                   events=a.events, expected=a.events, passed=False, anomalies=f"HARNESS ERROR: {exc}")
                    w.writerow(row)
                    fh.flush()
                    rows.append(row)
                    print(f"   {'PASS' if row['passed'] else 'FAIL'}  completed={row['completed']} failed={row['failed']} "
                          f"lost={row['jobs_lost']} retries={row['retries_total']} first_retry={sec(row['first_retry_s'])} "
                          f"reconnect={sec(row['reconnect_s'])} drain={sec(row['drain_s'])} max_depth={row['max_depth']} "
                          f"{('| ' + row['anomalies']) if row['anomalies'] else ''}", flush=True)
    finally:
        restore()

    def num(r, k):
        try:
            return float(r[k])
        except (TypeError, ValueError):
            return None

    print(f"\nCHAOS SUMMARY  ({a.events} events, 4 workers, stub 150 ms, lease 10 s, priority; median / worst of {a.runs})")
    head = f"{'scenario':<15}{'pass':>6}{'lost(w)':>8}{'failed':>7}{'retries med/w':>14}{'1st retry s':>12}{'reconn s':>9}{'drain med':>10}{'drain worst':>12}"
    print(head + "\n" + "-" * len(head))
    for sc in a.scenarios:
        rs = [r for r in rows if r["scenario"] == sc]
        if not rs:
            continue
        lost = [num(r, "jobs_lost") or 0 for r in rs]
        ret = [num(r, "retries_total") for r in rs]
        dr = [num(r, "drain_s") for r in rs]
        drw = [d for d in dr if d is not None]
        print(f"{sc:<15}{sum(bool(r['passed']) for r in rs):>3}/{len(rs):<2}{max(lost):>8.0f}"
              f"{sum(num(r, 'failed') or 0 for r in rs):>7.0f}{fmt(med(ret), 0):>9}/{fmt(max([x for x in ret if x is not None], default=None), 0):<4}"
              f"{fmt(med([num(r, 'first_retry_s') for r in rs])):>12}{fmt(med([num(r, 'reconnect_s') for r in rs])):>9}"
              f"{fmt(med(dr)):>10}{fmt(max(drw) if drw else None):>12}")
    bad = [r for r in rows if not r["passed"]]
    for r in bad:
        print(f"NOT PASSED: {r['scenario']} run {r['run']}: lost={r['jobs_lost']} failed={r['failed']} {r['anomalies']}")
    print(f"\n{len(rows)} runs -> {out}; total runtime {time.time() - began:.0f}s ({(time.time() - began) / 60:.1f} min)")
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
