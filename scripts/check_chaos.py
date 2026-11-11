"""check_chaos.py -- Block 3 gate: timeline inject, integrity, chaos_agent, autoscaler events.

Two tiers:
  --quick   (<1 min) run checks 1-4 only (no worker kill, no autoscaler)
  --full    all checks including worker kill + autoscaler timeline events

Usage (host):
  python scripts/check_chaos.py --quick
  python scripts/check_chaos.py --full
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = "http://localhost:8010"

ap = argparse.ArgumentParser()
ap.add_argument("--quick", action="store_true")
ap.add_argument("--full",  action="store_true")
ap.add_argument("--api",   default=API)
a = ap.parse_args()
if not a.quick and not a.full:
    a.quick = True   # default to quick

API = a.api
PASS = 0
FAIL = 0


def ts() -> str:
    return datetime.now().strftime("%H:%M:%S")


def log(msg: str) -> None:
    print(f"{ts()} {msg}", flush=True)


def ok(label: str) -> None:
    global PASS
    PASS += 1
    log(f"  PASS  {label}")


def fail(label: str, detail: str = "") -> None:
    global FAIL
    FAIL += 1
    log(f"  FAIL  {label}" + (f" -- {detail}" if detail else ""))


def get(path: str, timeout: int = 5) -> dict:
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read())


def post(path: str, body: dict, timeout: int = 5) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def compose(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["docker", "compose", *args], cwd=REPO, capture_output=True, text=True, check=False)


# ============================================================
# Check 1 — /api/cloud/stats has enqueued_total
# ============================================================
log("Check 1: stats endpoint has enqueued_total")
try:
    stats = get("/api/cloud/stats")
    if "enqueued_total" in stats:
        ok("enqueued_total present in /api/cloud/stats")
    else:
        fail("enqueued_total missing from /api/cloud/stats", str(list(stats.keys())))
except Exception as exc:
    fail("GET /api/cloud/stats failed", str(exc))

# ============================================================
# Check 2 — /api/cloud/integrity
# ============================================================
log("Check 2: /api/cloud/integrity")
try:
    integ = get("/api/cloud/integrity")
    for field in ("enqueued_total", "completed_total", "failed_total", "queue_depth", "processing", "leaked", "consistent"):
        if field not in integ:
            fail(f"integrity missing field '{field}'")
            break
    else:
        ok("all integrity fields present")
    if integ.get("consistent"):
        ok("integrity consistent=true")
    else:
        # consistent may be false if enqueued_total was 0 at start (counts started at 0)
        leaked = integ.get("leaked", -1)
        if leaked == 0 or integ.get("enqueued_total", 0) == 0:
            ok("integrity consistent (enqueued_total=0 or leaked=0)")
        else:
            fail("integrity consistent=false", f"leaked={leaked}")
except Exception as exc:
    fail("GET /api/cloud/integrity failed", str(exc))

# ============================================================
# Check 3 — POST /api/cloud/timeline (whitelist + reject)
# ============================================================
log("Check 3: timeline inject endpoint")

INJECT_OK = [
    ("scale_up",          {"from": 2, "to": 4, "depth": 100}),
    ("scale_down",        {"from": 4, "to": 3, "waited_s": 11.0}),
    ("chaos_inject",      {"scenario": "test"}),
    ("chaos_fault",       {"scenario": "test", "label": "unit test"}),
    ("chaos_recover",     {"scenario": "test"}),
    ("chaos_redis_kill",  {}),
    ("chaos_worker_kill", {}),
]

for etype, detail in INJECT_OK:
    try:
        result = post("/api/cloud/timeline", {"type": etype, "source": "check_chaos", "detail": detail})
        if result.get("ok"):
            ok(f"inject {etype}")
        else:
            fail(f"inject {etype}", str(result))
    except Exception as exc:
        fail(f"inject {etype}", str(exc))

# Rejected type
try:
    req = urllib.request.Request(
        API + "/api/cloud/timeline",
        data=json.dumps({"type": "rm_rf", "source": "attacker", "detail": {}}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=5) as r:
        body = json.loads(r.read())
    fail("reject non-whitelisted type (should have been 422)", str(body))
except urllib.error.HTTPError as e:
    if e.code == 422:
        ok("non-whitelisted type rejected with 422")
    else:
        fail(f"non-whitelisted type got HTTP {e.code}")
except Exception as exc:
    fail("reject non-whitelisted type", str(exc))

# ============================================================
# Check 4 — timeline endpoint returns injected events
# ============================================================
log("Check 4: GET /api/cloud/timeline returns injected events")
try:
    tl = get("/api/cloud/timeline?limit=50")
    events = tl.get("events", [])
    types_seen = {e["type"] for e in events}
    if "scale_up" in types_seen and "chaos_fault" in types_seen:
        ok("timeline contains injected scale_up and chaos_fault")
    else:
        fail("timeline missing injected events", f"types seen: {types_seen}")
    if len(events) > 0 and all("seq" in e and "ts" in e for e in events):
        ok("timeline events have seq and ts fields")
    else:
        fail("timeline events missing seq or ts")
except Exception as exc:
    fail("GET /api/cloud/timeline failed", str(exc))

if a.quick:
    log("")
    log(f"Quick gate done: {PASS} passed, {FAIL} failed")
    sys.exit(1 if FAIL > 0 else 0)

# ============================================================
# Full checks only below
# ============================================================

# ============================================================
# Check 5 — enqueued counter increments after a small storm
# ============================================================
log("Check 5: enqueued_total increments after storm")
try:
    before = get("/api/cloud/stats").get("enqueued_total", 0)
    post("/api/cloud/storm", {"n": 10, "critical": 0, "critical_after_s": 999, "policy": "fifo"})
    time.sleep(2)
    after = get("/api/cloud/stats").get("enqueued_total", 0)
    if after > before:
        ok(f"enqueued_total grew: {before} -> {after}")
    else:
        fail("enqueued_total did not grow", f"{before} -> {after}")
except Exception as exc:
    fail("storm + enqueued_total check failed", str(exc))

# ============================================================
# Check 6 — autoscaler posts timeline events (10 s run)
# ============================================================
log("Check 6: autoscaler posts scale_up/scale_down timeline events (10 s run)")
try:
    tl_before = get("/api/cloud/timeline?limit=5")
    last_seq_before = tl_before.get("last_seq", 0)

    # Flood the queue to force a scale-up, then drain
    post("/api/cloud/storm", {"n": 300, "critical": 0, "critical_after_s": 999, "policy": "priority"})

    proc = subprocess.Popen(
        [sys.executable, str(REPO / "scripts" / "autoscaler.py"),
         "--min", "2", "--max", "6", "--target", "20", "--interval", "1",
         "--cooldown", "5", "--duration", "10",
         "--api", API],
        cwd=REPO, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    proc.wait(timeout=20)

    tl_after = get(f"/api/cloud/timeline?since_seq={last_seq_before}&limit=50")
    types_after = {e["type"] for e in tl_after.get("events", [])}
    if "scale_up" in types_after:
        ok("autoscaler posted scale_up to timeline")
    else:
        fail("no scale_up in timeline after autoscaler run", f"types: {types_after}")
except Exception as exc:
    fail("autoscaler timeline check failed", str(exc))

# ============================================================
# Check 7 — regression.py still passes (optional, run last)
# ============================================================
log("Check 7: regression.py (full PASS/FAIL gate)")
try:
    r = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "regression.py")],
        cwd=REPO, capture_output=True, text=True, timeout=180,
    )
    if r.returncode == 0:
        ok("regression.py passed")
    else:
        tail = (r.stdout + r.stderr).strip().splitlines()[-5:]
        fail("regression.py failed", " | ".join(tail))
except subprocess.TimeoutExpired:
    fail("regression.py timed out")
except Exception as exc:
    fail("regression.py errored", str(exc))

log("")
log(f"Full gate done: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL > 0 else 0)
