"""
jobs.py — one conjunction negotiation as a pure function: process_job(job) -> dict.

No WebSocket, SQLite or FastAPI. Everything is synchronous and in-process; a
queue worker later just calls process_job() per message.
"""

from __future__ import annotations

import math
import time
import uuid
from datetime import datetime, timezone

from pydantic import BaseModel, model_validator

from backend.app.agents.negotiator import NegotiationEngine
from backend.app.agents.operator_agent import LLM_COUNTERS, get_llm_mode
from backend.app.profiles import load_all_tracked_objects, resolve_operator_profile
from backend.app.schemas.conjunction import ConjunctionAlert, ConjunctionStatus
from backend.app.schemas.negotiation import AgentId

_EVENT_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "aegis.conjunction")


def make_event_id(primary_id: str, secondary_id: str, tca_utc: str) -> str:
    """uuid5 over (sorted pair + tca_utc): same encounter -> same id, regardless of pair order."""
    a, b = sorted((primary_id, secondary_id))
    return str(uuid.uuid5(_EVENT_NAMESPACE, f"{a}|{b}|{tca_utc}"))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class Job(BaseModel):
    event_id: str = ""  # filled with make_event_id() when omitted
    primary_id: str
    secondary_id: str
    miss_distance_km: float
    tca_utc: str
    mission_priority: float | None = None
    enqueued_at: str = ""

    @model_validator(mode="after")
    def _fill_defaults(self) -> "Job":
        if not self.event_id:
            self.event_id = make_event_id(self.primary_id, self.secondary_id, self.tca_utc)
        if not self.enqueued_at:
            self.enqueued_at = _now_iso()
        return self


def process_job(job: Job | dict, *, force_rejection: bool = False) -> dict:
    """Run one negotiation to completion and summarise it.

    Raises (KeyError/ValueError/...) on bad input rather than substituting a
    fallback alert or object. `force_rejection` is the engine's
    force_initial_rejection flag (forces a round-1 validation rejection).
    """
    if isinstance(job, dict):
        job = Job(**job)

    t0 = time.perf_counter()
    mode = get_llm_mode()
    fallbacks_before = LLM_COUNTERS[mode]["fallbacks"]

    all_objects = load_all_tracked_objects()
    by_id = {o.norad_id: o for o in all_objects}
    for nid in (job.primary_id, job.secondary_id):
        if nid not in by_id:
            raise KeyError(f"NORAD {nid} not in tracked objects ({len(by_id)} loaded)")
    sat_a, sat_b = by_id[job.primary_id], by_id[job.secondary_id]

    rel_v = math.dist(sat_a.velocity_kmps, sat_b.velocity_kmps)
    alert = ConjunctionAlert(
        id=job.event_id,
        primary_id=job.primary_id,
        secondary_id=job.secondary_id,
        tca_utc=job.tca_utc,
        miss_distance_km=job.miss_distance_km,
        relative_velocity_kmps=rel_v,
        status=ConjunctionStatus.ALERTED,
        created_at=job.enqueued_at,
    )
    profile_a = resolve_operator_profile(sat_a.norad_id, sat_a.name, AgentId.OPERATOR_A)
    profile_b = resolve_operator_profile(sat_b.norad_id, sat_b.name, AgentId.OPERATOR_B)

    engine = NegotiationEngine(alert, profile_a, profile_b, sat_a, sat_b, all_objects)
    transcript, resolution = engine.run_negotiation(force_initial_rejection=force_rejection)

    return {
        "event_id": job.event_id,
        "resolution_status": resolution.status.value,
        "maneuvering_agent": resolution.maneuvering_agent,
        "delta_v_mps": resolution.delta_v_mps,
        "rounds": max((m.round for m in transcript), default=0),
        "duration_ms": round((time.perf_counter() - t0) * 1000.0, 2),
        "llm_mode": mode,
        "fallbacks_used": LLM_COUNTERS[mode]["fallbacks"] - fallbacks_before,
    }
