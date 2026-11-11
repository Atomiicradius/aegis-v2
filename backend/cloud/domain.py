"""
domain.py — per-event domain data (orbit pair, risk breakdown, compact outcome).

Call get_domain(r, ns, event_id) -> dict | None.
Returns None when result:<id> is missing (expired or unknown).

lead_h = hours from enqueue to TCA, matching risk.py's hours_to_tca definition
(urgency = clamp(1 - lead_h / 24)). Stored directly in result:<id> by worker.py
(set at job creation by events.py). Synthetic TLEs use TLE epoch as "now", so
tca_utc - wall_clock_enqueued_at is negative — always read the stored value.

Satellite names are loaded once via profiles.load_all_tracked_objects (memoised per process).
Unknown NORAD ids degrade to "NORAD <id>".
"""
from __future__ import annotations

import json

from redis import Redis

from backend.app.profiles import load_all_tracked_objects, object_group, resolve_operator_profile
from backend.app.schemas.negotiation import AgentId

_HORIZON_HOURS = 24.0  # must match risk.py HORIZON_HOURS


def _name(norad_id: str, by_id: dict) -> str:
    obj = by_id.get(norad_id)
    return obj.name if obj else ""


def _sat_block(norad_id: str, by_id: dict, agent_id: AgentId) -> dict:
    obj = by_id.get(norad_id)
    name = obj.name if obj else f"NORAD {norad_id}"
    group = object_group(norad_id)
    profile = resolve_operator_profile(norad_id, name, agent_id)
    return {"norad": norad_id, "name": name, "group": group, "operator": profile.operator_name}


def get_domain(r: Redis, ns: str, event_id: str) -> dict | None:
    """Return the domain block for any job state, or None if the event is unknown.

    Reads result:<id> first (completed jobs, has compact outcome fields).
    Falls back to job:<id> for unfinished jobs (queued / leased / requeued).
    lead_h is stored in both payloads at job creation by events.py.
    """
    prefix = ns if ns.endswith(":") else (ns + ":" if ns else "")
    raw = r.get(f"{prefix}result:{event_id}")
    from_result = raw is not None
    if raw is None:
        # Unfinished job — read job payload (queued / leased / requeued)
        raw = r.get(f"{prefix}job:{event_id}")
    if raw is None:
        return None
    d = json.loads(raw)

    primary_id = d.get("primary_id", "")
    secondary_id = d.get("secondary_id", "")
    tca_utc = d.get("tca_utc", "")
    # result:<id> stores "miss_km"; job:<id> stores "miss_distance_km"
    miss_km = float(d.get("miss_km") if from_result else d.get("miss_distance_km", 0.0))
    risk = float(d.get("risk", 0.0))
    is_critical = bool(d.get("is_critical", False))

    # lead_h stored at job creation by events.py (in both job and result payloads).
    # Do NOT compute from tca_utc - enqueued_at: synthetic events have negative deltas
    # because tca_utc = TLE_epoch + hours_to_tca while enqueued_at = wall clock.
    lead_h: float | None = d.get("lead_h")

    # Load tracked objects (memoised per process — no network, no overhead after first call)
    objects = load_all_tracked_objects()
    by_id = {o.norad_id: o for o in objects}

    # risk_parts — formulas mirror risk.py exactly
    closeness = round(max(0.0, min(1.0, 1.0 - miss_km / 5.0)), 3)
    urgency = round(max(0.0, min(1.0, 1.0 - (lead_h / _HORIZON_HOURS))), 3) if lead_h is not None else 0.0
    prof_a = resolve_operator_profile(primary_id, _name(primary_id, by_id))
    prof_b = resolve_operator_profile(secondary_id, _name(secondary_id, by_id))
    mission = round(max(prof_a.mvi, prof_b.mvi), 3)

    return {
        "a": _sat_block(primary_id, by_id, AgentId.OPERATOR_A) if primary_id else None,
        "b": _sat_block(secondary_id, by_id, AgentId.OPERATOR_B) if secondary_id else None,
        "miss_km": miss_km,
        "tca_utc": tca_utc,
        "lead_h": lead_h,
        "risk": round(risk, 3),
        "risk_parts": {"closeness": closeness, "urgency": urgency, "mission": mission},
        "is_critical": is_critical,
        # compact outcome fields — only present in result:<id> (completed jobs)
        "maneuvering_norad": d.get("maneuvering_norad") if from_result else None,
        "delta_v_mps": d.get("delta_v_mps") if from_result else None,
        "residual_km": d.get("residual_km") if from_result else None,
        "validation_clear": d.get("validation_clear") if from_result else None,
    }
