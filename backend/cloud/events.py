"""events.py — synthetic conjunction events for load tests."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from backend.cloud.jobs import Job

# Copied from the original orchestrator.py DOCKED_OBJECT_GROUPS. Objects inside
# a group propagate to the same position (0.0 km apart), so they make every
# validation look like a secondary-conjunction. Exclude them from the pool.
DOCKED_OBJECT_GROUPS: list[frozenset[str]] = [
    frozenset({"25544", "36086", "49044", "67796", "68689", "68837"}),  # ISS complex
    frozenset({"48274", "53239", "54216", "69049", "69180"}),           # CSS complex
]
_DOCKED = frozenset().union(*DOCKED_OBJECT_GROUPS)


def tle_epoch(line1: str) -> datetime:
    yy, day = int(line1[18:20]), float(line1[20:32])
    return datetime(2000 + yy, 1, 1, tzinfo=timezone.utc) + timedelta(days=day - 1)


def eligible_ids(objects) -> list[str]:
    return sorted(o.norad_id for o in objects if o.norad_id not in _DOCKED)


def random_job(rng: random.Random, objects, *, risk: float | None = None,
               is_critical: bool = False) -> Job:
    """Random non-docked pair, miss 0.5-5 km, TCA 1-24 h after the newest TLE epoch."""
    ids = eligible_ids(objects)
    if len(ids) < 2:
        raise RuntimeError(f"only {len(ids)} non-docked objects available; need >= 2")
    a, b = rng.sample(ids, 2)
    newest = max(tle_epoch(o.tle_line1) for o in objects)
    tca = (newest + timedelta(hours=rng.uniform(1, 24))).replace(microsecond=0)
    return Job(
        primary_id=a,
        secondary_id=b,
        miss_distance_km=round(rng.uniform(0.5, 5.0), 2),
        tca_utc=tca.isoformat().replace("+00:00", "Z"),
        mission_priority=rng.random(),
        risk=rng.random() if risk is None else risk,  # placeholder risk score
        is_critical=is_critical,
    )
