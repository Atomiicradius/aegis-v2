"""Block 5 check: FIFO vs priority race gate (REAL workers, namespaced queues).

Usage:
    python scripts/check_race.py [--quick] [--api http://localhost:8010]

Prerequisites:
    docker compose --profile race up -d --scale worker_fifo=2 --scale worker_prio=2

--quick (~3 min): R1-R5
    R1: both race lane workers live (fifo:workers and prio:workers >= 2 each)
    R2: POST /api/cloud/race/start returns {status: started}
    R3: GET  /api/cloud/race/result completes within 5 min (0 failed in both lanes)
    R4: each critical pair has primary_name, secondary_name, lead_h, resolution_ms, lane
    R5: namespace clean — fifo:/prio: keys present; main 'jobs' key name unchanged

Full mode (~6 min): R1-R10
    R6:  priority critical_resolution_ms.avg is >= 10x lower than FIFO's
    R7:  total drain times within 5% of each other
    R8:  all critical pairs have lead_h in (0, 24]
    R9:  main queue stats unchanged by race (GET /api/cloud/stats is consistent)
    R10: regression.py still passes

critical_resolution_ms is wall-clock (finished_at − enqueued_at) written by real workers.
Run from the repo root on the HOST.
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request

parser = argparse.ArgumentParser()
parser.add_argument("--api", default="http://localhost:8010")
parser.add_argument("--quick", action="store_true")
args = parser.parse_args()

API = args.api
_checks: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    tag = "PASS" if ok else "FAIL"
    suffix = f" [{detail}]" if detail else ""
    print(f"  {tag} {label}{suffix}", flush=True)
    _checks.append((label, ok, detail))


def fail(label: str, detail: str = "") -> None:
    check(label, False, detail)


def api(path: str, body=None, timeout: int = 20):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    method = "POST" if data is not None else "GET"
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


# ---------------------------------------------------------------------------
# R1: lane workers live
# ---------------------------------------------------------------------------
print("\n=== R1: race lane workers live (fifo:workers and prio:workers >= 2 each) ===")
try:
    import redis as _redis
    r = _redis.Redis.from_url("redis://localhost:6379", decode_responses=True)

    def _live(ns: str) -> int:
        now_s, now_us = r.time()
        now = now_s + now_us / 1_000_000
        return sum(1 for t in r.hgetall(f"{ns}workers").values() if now - float(t) < 10.0)

    fifo_live = _live("fifo:")
    prio_live = _live("prio:")
    check("R1a. fifo lane workers >= 2", fifo_live >= 2, f"live={fifo_live}")
    check("R1b. prio lane workers >= 2", prio_live >= 2, f"live={prio_live}")
    if fifo_live < 1 or prio_live < 1:
        print(
            "\n  FATAL: lane workers not running.\n"
            "  Run: docker compose --profile race up -d "
            "--scale worker_fifo=2 --scale worker_prio=2",
            flush=True,
        )
        sys.exit(1)
except ImportError:
    print(
        "  SKIP R1 (redis-py not installed on host — "
        "run: pip install redis)\n  Continuing without worker-count check.",
        flush=True,
    )
    _redis = None  # type: ignore[assignment]
    r = None  # type: ignore[assignment]

# ---------------------------------------------------------------------------
# R2: start race
# ---------------------------------------------------------------------------
print("\n=== R2: POST /api/cloud/race/start ===")
try:
    resp = api("/api/cloud/race/start",
               {"n": 300, "critical": 3, "seed": 42, "critical_offset_s": 2.0})
    started = resp.get("status") in ("started", "already_running")
    check("R2. race start returns status=started", started,
          f"status={resp.get('status')} "
          f"fifo_workers={resp.get('fifo_workers')} prio_workers={resp.get('prio_workers')}")
except urllib.error.HTTPError as exc:
    body = exc.read().decode()
    fail("R2. race start exception", f"HTTP {exc.code}: {body[:200]}")
    print("  FATAL: cannot proceed", flush=True)
    sys.exit(1)
except Exception as exc:
    fail("R2. race start exception", str(exc))
    print("  FATAL: cannot proceed", flush=True)
    sys.exit(1)

# ---------------------------------------------------------------------------
# R3: wait for result (up to 5 min), 0 failed in both lanes
# ---------------------------------------------------------------------------
print("\n=== R3: GET /api/cloud/race/result (wait up to 5 min, 0 failed) ===")
result = None
t_start_wait = time.time()
TIMEOUT = 300
for i in range(TIMEOUT):
    time.sleep(1)
    try:
        r_res = api("/api/cloud/race/result", timeout=30)
        st = r_res.get("status")
        if st == "running":
            done_f = r_res.get("fifo_done", 0)
            done_p = r_res.get("prio_done", 0)
            total = r_res.get("total", "?")
            if i % 15 == 14:
                print(f"  t+{i+1}s: running … fifo {done_f}/{total} prio {done_p}/{total}",
                      flush=True)
            continue
        if st == "error":
            fail("R3. race ended with error")
            break
        if "fifo" in r_res and "priority" in r_res:
            result = r_res
            elapsed = round(time.time() - t_start_wait)
            print(f"  t+{elapsed}s: result received", flush=True)
            break
    except urllib.error.HTTPError as e:
        if e.code == 404 and i < 5:
            continue
        raise
    except Exception:
        pass

check("R3a. race result available within 5 min", result is not None,
      "timed out" if result is None else f"n={result.get('n')} total={result.get('total')}")

if result is None:
    print("  FATAL: no race result, skipping R4–R10", flush=True)
    sys.exit(1)

fifo = result.get("fifo", {})
prio = result.get("priority", {})

fifo_failed = fifo.get("failed_jobs", 0)
prio_failed = prio.get("failed_jobs", 0)
check("R3b. fifo lane: 0 failed jobs", fifo_failed == 0, f"failed={fifo_failed}")
check("R3c. prio lane: 0 failed jobs", prio_failed == 0, f"failed={prio_failed}")

# ---------------------------------------------------------------------------
# R4: critical pair fields
# ---------------------------------------------------------------------------
print("\n=== R4: critical pair fields (names, lead_h, resolution_ms, lane) ===")
all_pairs = fifo.get("critical_pairs", []) + prio.get("critical_pairs", [])
if not all_pairs:
    fail("R4. no critical pairs to inspect")
else:
    missing_names = [p for p in all_pairs if not p.get("primary_name") or not p.get("secondary_name")]
    missing_lead  = [p for p in all_pairs if p.get("lead_h") is None]
    missing_lane  = [p for p in all_pairs if p.get("lane") not in ("fifo", "prio")]
    missing_res   = [p for p in all_pairs if p.get("resolution_ms") is None]
    check("R4a. all pairs have primary_name + secondary_name", len(missing_names) == 0,
          f"{len(missing_names)} missing")
    check("R4b. all pairs have lead_h not null", len(missing_lead) == 0,
          f"{len(missing_lead)} null")
    check("R4c. all pairs have lane=fifo|prio", len(missing_lane) == 0,
          f"{len(missing_lane)} invalid")
    check("R4d. all pairs have resolution_ms (wall-clock)", len(missing_res) == 0,
          f"{len(missing_res)} missing")
    sample = all_pairs[0]
    print(f"  sample: {sample.get('primary_name')} x {sample.get('secondary_name')} "
          f"lead_h={sample.get('lead_h')} resolution_ms={sample.get('resolution_ms')} "
          f"lane={sample.get('lane')}", flush=True)

# ---------------------------------------------------------------------------
# R5: namespace isolation
# ---------------------------------------------------------------------------
print("\n=== R5: namespace isolation ===")
if r is not None:
    fifo_jobs_exists = r.exists("fifo:jobs") >= 1  # may be drained but should have existed
    prio_jobs_exists = r.exists("prio:jobs") >= 1
    # Confirm main queue key names unchanged (should NOT be called 'fifo:jobs' in the main ns)
    # 'jobs' may be empty after a drain but must still be named 'jobs' (not 'fifo:jobs')
    race_jobs_exists = r.exists("race:jobs") >= 1
    main_key_ok = not race_jobs_exists
    check("R5a. race:result:latest exists", r.exists("race:result:latest") >= 1)
    check("R5b. no 'race:jobs' key (main namespace untouched)", main_key_ok,
          "race:jobs should not exist")
    # At least one of the lane keys exists (may be cleared if workers drained completely)
    check("R5c. fifo: or prio: namespaced keys seen",
          r.exists("fifo:counters") >= 1 or r.exists("prio:counters") >= 1)
else:
    print("  SKIP R5 (redis-py not available)", flush=True)

if args.quick:
    print("\n--- quick tier done ---", flush=True)
else:
    # -------------------------------------------------------------------------
    # R6: priority critical resolution >= 10x faster than FIFO
    # -------------------------------------------------------------------------
    print("\n=== R6: priority critical_resolution_ms.avg >= 10x lower than FIFO ===")
    fifo_res_avg = fifo.get("critical_resolution_ms", {}).get("avg", 0)
    prio_res_avg = prio.get("critical_resolution_ms", {}).get("avg", 0)
    factor = fifo_res_avg / prio_res_avg if prio_res_avg > 0 else 0
    check("R6. priority critical resolution >= 10x lower", factor >= 10,
          f"fifo={fifo_res_avg}ms prio={prio_res_avg}ms factor={factor:.1f}x")

    # -------------------------------------------------------------------------
    # R7: total drain times within 5%
    # -------------------------------------------------------------------------
    print("\n=== R7: total drain times within 5% ===")
    fifo_drain = fifo.get("drain_ms", 0)
    prio_drain = prio.get("drain_ms", 0)
    if fifo_drain > 0 and prio_drain > 0:
        drain_diff_pct = abs(fifo_drain - prio_drain) / max(fifo_drain, prio_drain) * 100
        check("R7. drain times within 5%", drain_diff_pct <= 5,
              f"fifo={fifo_drain}ms prio={prio_drain}ms diff={drain_diff_pct:.1f}%")
    else:
        fail("R7. drain_ms missing or zero", f"fifo={fifo_drain} prio={prio_drain}")

    # -------------------------------------------------------------------------
    # R8: all critical pairs have lead_h in (0, 24]
    # -------------------------------------------------------------------------
    print("\n=== R8: all critical pairs have lead_h in (0, 24] ===")
    invalid_lead = [p for p in all_pairs
                    if not (isinstance(p.get("lead_h"), (int, float)) and 0 < p["lead_h"] <= 24)]
    check("R8. all lead_h in (0, 24]", len(invalid_lead) == 0,
          f"{len(invalid_lead)} invalid: {[p.get('lead_h') for p in invalid_lead[:3]]}")

    # -------------------------------------------------------------------------
    # R9: main queue stats unchanged (no cross-namespace pollution)
    # -------------------------------------------------------------------------
    print("\n=== R9: main queue stats consistent after race ===")
    try:
        stats = api("/api/cloud/stats")
        queue_depth = stats.get("queue_depth", -1)
        policy = stats.get("policy", "")
        # Main queue should not show 'fifo' or 'prio' as the policy (those are lane workers)
        # and should not show inflated numbers from lane jobs
        check("R9a. /api/cloud/stats responds", isinstance(stats, dict) and "queue_depth" in stats,
              f"queue_depth={queue_depth}")
        # The key assertion: race:status=done and stats.queue_depth is from main queue only
        if r is not None:
            race_status = r.get("race:status")
            main_depth = r.zcard("jobs")  # main queue key (not fifo:jobs or prio:jobs)
            check("R9b. race:status=done", race_status == "done", f"status={race_status}")
            check("R9c. main 'jobs' key is for main queue (not lane jobs)",
                  main_depth == queue_depth,
                  f"redis_jobs={main_depth} stats_depth={queue_depth}")
    except Exception as exc:
        fail("R9. stats check exception", str(exc))

    # -------------------------------------------------------------------------
    # R10: regression.py still passes
    # -------------------------------------------------------------------------
    print("\n=== R10: regression.py ===")
    try:
        cp = subprocess.run(
            [sys.executable, "scripts/regression.py"],
            capture_output=True, text=True, timeout=180,
        )
        passed = cp.returncode == 0 and "REGRESSION PASS" in cp.stdout
        last_lines = (cp.stdout + cp.stderr).strip().splitlines()[-3:]
        check("R10. regression.py PASS", passed, " | ".join(last_lines))
    except subprocess.TimeoutExpired:
        fail("R10. regression timed out")
    except Exception as exc:
        fail("R10. regression exception", str(exc))

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
passed = sum(1 for _, ok, _ in _checks if ok)
failed = sum(1 for _, ok, _ in _checks if not ok)
total  = passed + failed
tier   = "quick" if args.quick else "full"

print(f"\nRace gate ({tier}): {passed}/{total} passed", flush=True)
if failed == 0:
    print("ALL PASS", flush=True)
    sys.exit(0)
else:
    print(f"{failed} FAILED", flush=True)
    sys.exit(1)
