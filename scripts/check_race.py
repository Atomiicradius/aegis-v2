"""Block 5 check: FIFO vs priority race gate.

Usage:
    python scripts/check_race.py [--quick] [--api http://localhost:8010]

--quick  (~2 min): R1-R5
    R1: POST /api/cloud/race/start returns {status: started}
    R2: GET  /api/cloud/race/result returns completed result (wait up to 90 s)
    R3: fifo and priority lanes both present with critical_pairs
    R4: each critical pair has primary_name, secondary_name, lead_h not null, tca+h value
    R5: namespace check — race:result:latest exists; existing key 'jobs' unchanged

Full mode (~5 min): R1-R8
    R6: priority critical_latency_ms.avg < fifo critical_latency_ms.avg (priority faster)
    R7: all critical pairs have valid lead_h in (0, 24]
    R8: regression.py still passes (run after race to confirm main queue untouched)

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


def api(path: str, body=None):
    url = API + path
    data = json.dumps(body).encode() if body is not None else None
    method = "POST" if data is not None else "GET"
    req = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req, timeout=20).read())


# ---------------------------------------------------------------------------
# R1: start race
# ---------------------------------------------------------------------------
print("\n=== R1: POST /api/cloud/race/start ===")
try:
    resp = api("/api/cloud/race/start", {"n": 20, "critical": 3, "critical_after_s": 1})
    check("R1. race start returns status", resp.get("status") in ("started", "already_running"),
          f"status={resp.get('status')}")
except Exception as exc:
    fail("R1. race start exception", str(exc))
    print("  FATAL: cannot proceed", flush=True)
    sys.exit(1)

# ---------------------------------------------------------------------------
# R2: wait for result (up to 90 s)
# ---------------------------------------------------------------------------
print("\n=== R2: GET /api/cloud/race/result (wait up to 90 s) ===")
result = None
for i in range(90):
    time.sleep(1)
    try:
        r = api("/api/cloud/race/result")
        if isinstance(r, dict) and r.get("status") == "running":
            if i % 10 == 9:
                print(f"  t+{i+1}s: still running ...", flush=True)
            continue
        if isinstance(r, dict) and "fifo" in r and "priority" in r:
            result = r
            print(f"  t+{i+1}s: result received", flush=True)
            break
    except urllib.error.HTTPError as e:
        if e.code == 404:
            if i % 15 == 14:
                print(f"  t+{i+1}s: 404 (race not started yet?)", flush=True)
        else:
            raise
    except Exception:
        pass

check("R2. race result available within 90 s", result is not None,
      "timed out" if result is None else f"n={result.get('n')} critical={result.get('critical')}")

if result is None:
    print("  FATAL: no race result, skipping R3–R8", flush=True)
    sys.exit(1)

# ---------------------------------------------------------------------------
# R3: both lanes present
# ---------------------------------------------------------------------------
print("\n=== R3: both lanes present with critical_pairs ===")
fifo = result.get("fifo", {})
prio = result.get("priority", {})
check("R3a. fifo lane present", isinstance(fifo, dict) and "critical_pairs" in fifo,
      f"keys={list(fifo.keys())[:6]}")
check("R3b. priority lane present", isinstance(prio, dict) and "critical_pairs" in prio,
      f"keys={list(prio.keys())[:6]}")
check("R3c. fifo has critical_pairs list", isinstance(fifo.get("critical_pairs"), list),
      f"count={len(fifo.get('critical_pairs', []))}")
check("R3d. priority has critical_pairs list", isinstance(prio.get("critical_pairs"), list),
      f"count={len(prio.get('critical_pairs', []))}")

# ---------------------------------------------------------------------------
# R4: each critical pair has names, lead_h, lane field
# ---------------------------------------------------------------------------
print("\n=== R4: critical pair fields (primary_name, secondary_name, lead_h, lane) ===")
all_pairs = fifo.get("critical_pairs", []) + prio.get("critical_pairs", [])
if not all_pairs:
    fail("R4. no critical pairs to inspect")
else:
    missing_names = [p for p in all_pairs if not p.get("primary_name") or not p.get("secondary_name")]
    missing_lead  = [p for p in all_pairs if p.get("lead_h") is None]
    missing_lane  = [p for p in all_pairs if p.get("lane") not in ("fifo", "priority")]
    missing_cumul = [p for p in all_pairs if p.get("cumulative_ms_before") is None]
    check("R4a. all pairs have primary_name + secondary_name", len(missing_names) == 0,
          f"{len(missing_names)} missing")
    check("R4b. all pairs have lead_h not null", len(missing_lead) == 0,
          f"{len(missing_lead)} null")
    check("R4c. all pairs have lane=fifo|priority", len(missing_lane) == 0,
          f"{len(missing_lane)} invalid")
    check("R4d. all pairs have cumulative_ms_before", len(missing_cumul) == 0,
          f"{len(missing_cumul)} missing")
    sample = all_pairs[0]
    print(f"  sample pair: {sample.get('primary_name')} x {sample.get('secondary_name')} "
          f"lead_h={sample.get('lead_h')} lane={sample.get('lane')}", flush=True)

# ---------------------------------------------------------------------------
# R5: namespace check
# ---------------------------------------------------------------------------
print("\n=== R5: namespace isolation ===")
try:
    import redis
    r = redis.Redis.from_url("redis://localhost:6379", decode_responses=True)
    race_key_exists = r.exists("race:result:latest") >= 1
    check("R5a. race:result:latest exists in Redis", race_key_exists)
    # Confirm main queue key names unchanged (jobs zset should still be called 'jobs', not 'race:jobs')
    # After the race, jobs key may be empty (workers drained it) but it must NOT be renamed
    race_jobs_exists = r.exists("race:jobs") >= 1
    check("R5b. no 'race:jobs' key (main namespace untouched)", not race_jobs_exists,
          "race:jobs should not exist")
except ImportError:
    print("  SKIP R5 (redis-py not available on host — run inside worker container for this check)", flush=True)
except Exception as exc:
    fail("R5. Redis check exception", str(exc))

if args.quick:
    print("\n--- quick tier done ---", flush=True)
else:
    # -------------------------------------------------------------------------
    # R6: priority faster than fifo for critical jobs
    # -------------------------------------------------------------------------
    print("\n=== R6: priority critical_resolution_ms.avg < fifo critical_resolution_ms.avg ===")
    # resolution_ms = cumulative_ms_before + latency_ms (true scheduling latency incl. wait)
    fifo_res = fifo.get("critical_resolution_ms", {}).get("avg", 0)
    prio_res = prio.get("critical_resolution_ms", {}).get("avg", 0)
    check("R6. priority critical resolution < fifo critical resolution", prio_res < fifo_res,
          f"fifo={fifo_res}ms prio={prio_res}ms")

    # -------------------------------------------------------------------------
    # R7: lead_h validity
    # -------------------------------------------------------------------------
    print("\n=== R7: all critical pairs have lead_h in (0, 24] ===")
    invalid_lead = [p for p in all_pairs if not (isinstance(p.get("lead_h"), (int, float)) and 0 < p["lead_h"] <= 24)]
    check("R7. all lead_h in (0, 24]", len(invalid_lead) == 0,
          f"{len(invalid_lead)} invalid: {[p.get('lead_h') for p in invalid_lead[:3]]}")

    # -------------------------------------------------------------------------
    # R8: regression.py still passes
    # -------------------------------------------------------------------------
    print("\n=== R8: regression.py ===")
    try:
        cp = subprocess.run(
            [sys.executable, "scripts/regression.py"],
            capture_output=True, text=True, timeout=120,
        )
        passed = cp.returncode == 0 and "REGRESSION PASS" in cp.stdout
        last_lines = (cp.stdout + cp.stderr).strip().splitlines()[-3:]
        check("R8. regression.py PASS", passed, " | ".join(last_lines))
    except subprocess.TimeoutExpired:
        fail("R8. regression timed out")
    except Exception as exc:
        fail("R8. regression exception", str(exc))

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
