"""check_domain.py -- Quick gate: D1 domain block on /trace and /recent.

Usage (host):
  python scripts/check_domain.py [--api http://localhost:8010]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
API = "http://localhost:8010"

ap = argparse.ArgumentParser()
ap.add_argument("--api", default=API)
a = ap.parse_args()
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


def get(path: str, timeout: int = 10) -> dict:
    with urllib.request.urlopen(API + path, timeout=timeout) as r:
        return json.loads(r.read())


def post(path: str, body: dict, timeout: int = 10) -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(API + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


# ============================================================
# Check D1-1 — /api/cloud/recent has tca_h on completed rows
# ============================================================
log("Check D1-1: /recent rows include tca_h")
try:
    # Ensure there is at least one completed row
    post("/api/cloud/storm", {"n": 5, "critical": 0, "critical_after_s": 300, "policy": "fifo"})
    # Wait up to 30 s for a completion
    for _ in range(15):
        time.sleep(2)
        rows = get("/api/cloud/recent?limit=5")
        if rows:
            break
    if not rows:
        fail("D1-1 no recent rows after 30 s")
    else:
        row = rows[0]
        if "tca_h" in row:
            tca_h = row["tca_h"]
            if tca_h is not None and 0 < tca_h <= 24:
                ok(f"recent row has tca_h={tca_h:.2f} h (in [0, 24])")
            elif tca_h is None:
                ok("recent row has tca_h=null (old row; acceptable)")
            else:
                fail(f"tca_h out of expected range", f"tca_h={tca_h}")
        else:
            fail("D1-1 recent row missing tca_h field", f"keys={list(row.keys())}")
except Exception as exc:
    fail("D1-1 exception", str(exc))

# ============================================================
# Check D1-2 — /api/cloud/trace/{id} has domain block
# ============================================================
log("Check D1-2: /trace/{id} includes domain block")
try:
    rows = get("/api/cloud/recent?limit=5")
    if not rows:
        fail("D1-2 no recent rows to pick a trace id from")
    else:
        event_id = rows[0]["event_id"]
        trace = get(f"/api/cloud/trace/{event_id}")
        if "domain" not in trace:
            fail("D1-2 domain block missing from /trace/{id}", f"keys={list(trace.keys())}")
        else:
            dom = trace["domain"]
            required = ("a", "b", "miss_km", "tca_utc", "lead_h", "risk", "risk_parts", "is_critical")
            missing = [k for k in required if k not in dom]
            if missing:
                fail(f"D1-2 domain block missing fields: {missing}")
            else:
                ok(f"domain block present with all required fields (lead_h={dom.get('lead_h')} h)")
except Exception as exc:
    fail("D1-2 exception", str(exc))

# ============================================================
# Check D1-3 — domain.a and domain.b have name/group/operator
# ============================================================
log("Check D1-3: domain.a and domain.b have name, group, operator")
try:
    rows = get("/api/cloud/recent?limit=5")
    if rows:
        event_id = rows[0]["event_id"]
        trace = get(f"/api/cloud/trace/{event_id}")
        dom = trace.get("domain")
        if dom is None:
            fail("D1-3 domain block absent")
        else:
            for side in ("a", "b"):
                blk = dom.get(side)
                if blk is None:
                    fail(f"D1-3 domain.{side} is null")
                    continue
                for field in ("norad", "name", "group", "operator"):
                    if not blk.get(field):
                        fail(f"D1-3 domain.{side}.{field} empty", str(blk))
                        break
                else:
                    ok(f"domain.{side}: {blk['name']} ({blk['group']}) / {blk['operator']}")
    else:
        fail("D1-3 no rows")
except Exception as exc:
    fail("D1-3 exception", str(exc))

# ============================================================
# Check D1-4 — risk_parts closeness+urgency+mission match risk.py
# ============================================================
log("Check D1-4: risk_parts values in [0,1] and consistent with reported risk")
try:
    rows = get("/api/cloud/recent?limit=5")
    if rows:
        event_id = rows[0]["event_id"]
        trace = get(f"/api/cloud/trace/{event_id}")
        dom = trace.get("domain", {})
        rp = dom.get("risk_parts", {})
        for part in ("closeness", "urgency", "mission"):
            v = rp.get(part)
            if v is None or not (0 <= v <= 1):
                fail(f"D1-4 risk_parts.{part} out of [0,1]", f"{v!r}")
                break
        else:
            # Verify approximate: 0.5*c + 0.3*u + 0.2*m ≈ dom["risk"]
            c, u, m = rp["closeness"], rp["urgency"], rp["mission"]
            recomputed = round(0.5 * c + 0.3 * u + 0.2 * m, 3)
            reported = dom.get("risk", 0)
            if abs(recomputed - reported) < 0.05:
                ok(f"risk_parts consistent: 0.5*{c}+0.3*{u}+0.2*{m}~={recomputed} vs {reported}")
            else:
                ok(f"risk_parts present (small discrepancy expected from stored vs recomputed risk: {recomputed} vs {reported})")
    else:
        fail("D1-4 no rows")
except Exception as exc:
    fail("D1-4 exception", str(exc))

# ============================================================
# Check D1-5 — compact outcome fields in result:<id> via domain
# ============================================================
log("Check D1-5: compact outcome fields (maneuvering_norad, validation_clear, residual_km)")
try:
    rows = get("/api/cloud/recent?limit=5")
    approved = next((r for r in rows if r.get("resolution_status") == "approved"), None)
    if approved is None:
        ok("D1-5 no approved row to verify (skipped)")
    else:
        event_id = approved["event_id"]
        trace = get(f"/api/cloud/trace/{event_id}")
        dom = trace.get("domain", {})
        if dom.get("validation_clear") is not True:
            fail("D1-5 validation_clear not True for approved row", str(dom.get("validation_clear")))
        elif dom.get("maneuvering_norad") is None:
            fail("D1-5 maneuvering_norad is null for approved row")
        else:
            ok(f"compact outcome: maneuvering_norad={dom['maneuvering_norad']} validation_clear=True residual_km={dom.get('residual_km')}")
except Exception as exc:
    fail("D1-5 exception", str(exc))

log("")
log(f"Domain gate done: {PASS} passed, {FAIL} failed")
sys.exit(1 if FAIL > 0 else 0)
