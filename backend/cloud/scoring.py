"""
scoring.py — queue score for a job. LOWEST score is popped first. Pure functions
(no Redis import) so they are unit-testable anywhere.

  fifo      score = enqueued_at                         (epoch seconds)
  priority  score = enqueued_at * AGING_PER_SEC - risk

With aging, one second of waiting is worth AGING_PER_SEC of risk. A job enqueued
Δt seconds earlier than another wins as soon as Δt * AGING_PER_SEC > risk gap.
At the default 0.002 a risk gap of 0.8 needs 400 s, so inside a short storm the
riskiest job still goes first, but a low-risk job cannot starve forever. Equal
risk -> older first (deterministic tie-break).
"""

from __future__ import annotations

import os
from datetime import datetime

AGING_PER_SEC = float(os.environ.get("AGING_PER_SEC", "0.002"))


def enqueued_epoch(job) -> float:
    return datetime.fromisoformat(job.enqueued_at).timestamp()


def compute_score(job, policy: str, aging_per_sec: float | None = None) -> float:
    t = enqueued_epoch(job)
    if policy == "fifo":
        return t
    if policy == "priority":
        return t * (AGING_PER_SEC if aging_per_sec is None else aging_per_sec) - job.risk
    raise ValueError(f"policy must be fifo|priority; got {policy!r}")
