"""Smoke test: process 10 synthetic conjunction jobs through process_job().

    python scripts/smoke.py            # honours LLM_MODE / OFFLINE env
"""

from __future__ import annotations

import json
import random
import statistics
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "backend")]

from backend.app.agents.operator_agent import LLM_COUNTERS  # noqa: E402
from backend.app.profiles import load_all_tracked_objects, tracked_objects_source  # noqa: E402
from backend.cloud.jobs import Job, process_job  # noqa: E402

N_JOBS = 10
FORCE_REJECTION_JOB = 3  # index of the job that gets force_rejection=True
SEED = 42


def _tle_epoch(line1: str) -> datetime:
    yy, day = int(line1[18:20]), float(line1[20:32])
    return datetime(2000 + yy, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)


def main() -> int:
    rng = random.Random(SEED)
    objects = load_all_tracked_objects()
    print(f"tracked objects: {len(objects)} (source: {tracked_objects_source()})")

    # SGP4 is only trustworthy near a TLE's epoch. The negotiator validates from
    # tca_utc for 6h, so put every TCA 1-24h after the NEWEST epoch in the set.
    newest = max(_tle_epoch(o.tle_line1) for o in objects)
    ids = sorted(o.norad_id for o in objects)

    jobs = []
    for _ in range(N_JOBS):
        a, b = rng.sample(ids, 2)
        tca = (newest + timedelta(hours=rng.uniform(1, 24))).replace(microsecond=0)
        jobs.append(
            Job(
                primary_id=a,
                secondary_id=b,
                miss_distance_km=round(rng.uniform(0.5, 5.0), 2),  # < 7.5 km: no fast path
                tca_utc=tca.isoformat().replace("+00:00", "Z"),
                mission_priority=rng.random(),
            )
        )

    results = []
    for i, job in enumerate(jobs):
        r = process_job(job, force_rejection=(i == FORCE_REJECTION_JOB))
        results.append(r)
        print(
            f"job {i:2d} {job.primary_id}-{job.secondary_id} miss={job.miss_distance_km:4.2f}km "
            f"-> {r['resolution_status']:<22} mover={r['maneuvering_agent']:<10} "
            f"dv={r['delta_v_mps']:.1f} rounds={r['rounds']} "
            f"fallbacks={r['fallbacks_used']} {r['duration_ms']:8.2f} ms"
            + ("  [forced rejection]" if i == FORCE_REJECTION_JOB else "")
        )

    durs = [r["duration_ms"] for r in results]
    print(f"\nmean duration_ms: {statistics.mean(durs):.2f}  (min {min(durs):.2f}, max {max(durs):.2f})")
    print(f"mean excluding job 0 (cold start): {statistics.mean(durs[1:]):.2f}")
    print(f"resolution statuses: { {s: [r['resolution_status'] for r in results].count(s) for s in {r['resolution_status'] for r in results}} }")
    print(f"jobs with rounds>1 (rejection path exercised): {sum(r['rounds'] > 1 for r in results)}")
    print(f"LLM counters: {json.dumps(LLM_COUNTERS)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
