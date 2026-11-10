"""risk.py — 0..1 risk score for a conjunction event (placeholder model, weights below)."""

from __future__ import annotations

import os

# risk = 0.5*closeness + 0.3*urgency + 0.2*mission
W_CLOSENESS = 0.5
W_URGENCY = 0.3
W_MISSION = 0.2

MISS_SCALE_KM = 5.0     # closeness reaches 0 at the conjunction threshold
HORIZON_HOURS = 24.0    # urgency reaches 0 at 24 h to TCA

CRITICAL_THRESHOLD = float(os.environ.get("CRITICAL_THRESHOLD", "0.8"))


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def risk(miss_km: float, hours_to_tca: float, mission_priority: float) -> float:
    """mission_priority is the 0..1 mission value (see mission_value())."""
    closeness = _clamp(1 - miss_km / MISS_SCALE_KM)
    urgency = _clamp(1 - hours_to_tca / HORIZON_HOURS)
    mission = _clamp(mission_priority)
    return W_CLOSENESS * closeness + W_URGENCY * urgency + W_MISSION * mission


def is_critical(risk_value: float, threshold: float | None = None) -> bool:
    return risk_value >= (CRITICAL_THRESHOLD if threshold is None else threshold)


def mission_value(primary_id: str, secondary_id: str) -> float:
    """max(MVI) of the two operators, from the same profiles the negotiation uses."""
    from backend.app.profiles import resolve_operator_profile

    return max(resolve_operator_profile(primary_id).mvi, resolve_operator_profile(secondary_id).mvi)
