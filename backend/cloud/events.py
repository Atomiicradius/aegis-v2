"""events.py — synthetic conjunction events for load tests."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from backend.cloud.jobs import Job
from backend.cloud.risk import is_critical, mission_value, risk

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


def _build(a: str, b: str, miss_km: float, hours_to_tca: float, objects) -> Job:
    # "now" for a synthetic event is the newest TLE epoch, so SGP4 stays valid.
    newest = max(tle_epoch(o.tle_line1) for o in objects)
    tca = (newest + timedelta(hours=hours_to_tca)).replace(microsecond=0)
    mission = mission_value(a, b)
    r = risk(miss_km, hours_to_tca, mission)
    return Job(
        primary_id=a,
        secondary_id=b,
        miss_distance_km=round(miss_km, 2),
        tca_utc=tca.isoformat().replace("+00:00", "Z"),
        mission_priority=mission,
        risk=r,
        is_critical=is_critical(r),
    )


def _pool(objects) -> list[str]:
    ids = eligible_ids(objects)
    if len(ids) < 2:
        raise RuntimeError(f"only {len(ids)} non-docked objects available; need >= 2")
    return ids


def random_job(rng: random.Random, objects) -> Job:
    """Random non-docked pair, miss 0.5-5 km, TCA 1-24 h out; risk from risk.py."""
    a, b = rng.sample(_pool(objects), 2)
    return _build(a, b, rng.uniform(0.5, 5.0), rng.uniform(1, 24), objects)


def make_critical_job(rng: random.Random, objects) -> Job:
    """Very close, very soon, and one of the pair is the highest-MVI eligible object."""
    ids = _pool(objects)
    star = max(ids, key=lambda i: mission_value(i, i))
    other = rng.choice([i for i in ids if i != star])
    return _build(star, other, rng.uniform(0.3, 0.6), rng.uniform(0.3, 1.0), objects)
