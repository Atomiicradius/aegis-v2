from datetime import datetime, timedelta, timezone

from backend.cloud.jobs import Job
from backend.cloud.scoring import compute_score

T0 = datetime(2026, 11, 10, 12, 0, 0, tzinfo=timezone.utc)


def _job(tag: str, risk: float, at: datetime) -> Job:
    return Job(primary_id="1", secondary_id="2", miss_distance_km=1.0, tca_utc="2026-11-11T00:00:00Z" + tag,
               risk=risk, enqueued_at=at.isoformat())


def test_fifo_is_enqueue_order():
    a, b = _job("a", 0.1, T0), _job("b", 0.9, T0 + timedelta(seconds=1))
    assert compute_score(a, "fifo") < compute_score(b, "fifo")


def test_priority_picks_higher_risk_within_a_storm():
    old_low = _job("a", 0.1, T0)
    new_high = _job("b", 0.9, T0 + timedelta(seconds=30))   # arrives 30 s later
    assert compute_score(new_high, "priority") < compute_score(old_low, "priority")


def test_old_low_risk_job_eventually_overtakes_new_high_risk_job():
    # risk gap 0.8 / 0.002 per s = 400 s of waiting
    old_low = _job("a", 0.1, T0)
    just_short = _job("b", 0.9, T0 + timedelta(seconds=399))
    long_wait = _job("c", 0.9, T0 + timedelta(seconds=401))
    assert compute_score(just_short, "priority") < compute_score(old_low, "priority")
    assert compute_score(old_low, "priority") < compute_score(long_wait, "priority")


def test_equal_risk_older_first():
    a, b = _job("a", 0.5, T0), _job("b", 0.5, T0 + timedelta(milliseconds=5))
    assert compute_score(a, "priority") < compute_score(b, "priority")
