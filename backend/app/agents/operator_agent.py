"""
operator_agent.py — Dev C (Workstream C2, early start)

Satellite Operator Agent: given a computed yield_score and satellite state,
calls the Anthropic API to produce a 1-2 sentence operational justification.

CRITICAL INVARIANT: This module never computes or modifies yield_score.
yield_score is computed by cost_functions.py BEFORE this function is called,
and is passed in as a parameter. The LLM only narrates the pre-computed number.
See architecture.md §Invariants #1 and library-docs.md §Anthropic SDK.

Ownership: Dev C only. This module has zero WebSocket dependencies.
"""

from __future__ import annotations

import logging
import os
import time
from typing import TypedDict

import anthropic
from pydantic import BaseModel, field_validator

try:
    from backend.app.agents.cost_functions import compute_yield_score
except ImportError:
    try:
        from app.agents.cost_functions import compute_yield_score
    except ImportError:
        from cost_functions import compute_yield_score

logger = logging.getLogger("[operator_agent]")

# ---------------------------------------------------------------------------
# Pydantic model for agent state input (validates before LLM call)
# ---------------------------------------------------------------------------

class AgentState(BaseModel):
    """Validated state for one satellite operator entering a negotiation round."""

    agent_id: str
    """Must be 'operator_A' or 'operator_B' — matches DB schema agent_id column."""

    operator: str
    """Human-readable operator name, e.g. 'Starlink Operations' or 'OneWeb'."""

    satellite_name: str
    """Human-readable satellite identifier, e.g. 'STARLINK-1234'."""

    mvi: float
    """Mission Value Index, 0–10."""

    fuel_margin_pct: float
    """Propellant margin as % of capacity, 0–100."""

    delta_v_mps: float
    """Estimated Δv for the proposed avoidance maneuver, in m/s."""

    miss_distance_km: float
    """Current predicted miss distance in km."""

    round_number: int
    """Current negotiation round (1-based)."""

    proposed_action: str
    """'maneuver' | 'stand_down' — this agent's proposal for who moves."""

    @field_validator("agent_id")
    @classmethod
    def validate_agent_id(cls, v: str) -> str:
        if v not in ("operator_A", "operator_B"):
            raise ValueError(f"agent_id must be 'operator_A' or 'operator_B'; got '{v}'")
        return v

    @field_validator("mvi")
    @classmethod
    def validate_mvi(cls, v: float) -> float:
        if not (0.0 <= v <= 10.0):
            raise ValueError(f"mvi must be in [0, 10]; got {v}")
        return v

    @field_validator("fuel_margin_pct")
    @classmethod
    def validate_fuel(cls, v: float) -> float:
        if not (0.0 <= v <= 100.0):
            raise ValueError(f"fuel_margin_pct must be in [0, 100]; got {v}")
        return v

    @field_validator("proposed_action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        if v not in ("maneuver", "stand_down"):
            raise ValueError(f"proposed_action must be 'maneuver' or 'stand_down'; got '{v}'")
        return v


# ---------------------------------------------------------------------------
# Template fallback (used when LLM call fails after one retry)
# ---------------------------------------------------------------------------

_FALLBACK_TEMPLATES: dict[str, str] = {
    "maneuver": (
        "{satellite} (MVI {mvi:.1f}) is prepared to execute the avoidance maneuver. "
        "With a fuel margin of {fuel_pct:.0f}% and an estimated Δv of {delta_v:.1f} m/s, "
        "the computed yield score of {yield_score:.2f} supports this operator yielding."
    ),
    "stand_down": (
        "{satellite} (MVI {mvi:.1f}) is holding position and requests the counterpart to maneuver. "
        "Current fuel margin is {fuel_pct:.0f}%; estimated Δv would be {delta_v:.1f} m/s. "
        "Yield score of {yield_score:.2f} indicates the counterpart satellite should yield."
    ),
}


def _build_fallback(state: AgentState, yield_score: float) -> str:
    template = _FALLBACK_TEMPLATES.get(state.proposed_action, _FALLBACK_TEMPLATES["maneuver"])
    return template.format(
        satellite=state.satellite_name,
        mvi=state.mvi,
        fuel_pct=state.fuel_margin_pct,
        delta_v=state.delta_v_mps,
        yield_score=yield_score,
    )


# ---------------------------------------------------------------------------
# LLM_MODE switch (cloud benchmark path): off | stub | real (default: real)
#   off  -> template fallback immediately
#   stub -> sleep LLM_STUB_MS (default 50) then template fallback
#   real -> Anthropic call (original behaviour)
# Per-mode counters so results can report which path actually ran. "fallbacks"
# counts template use, so in off/stub modes fallbacks == calls.
# ---------------------------------------------------------------------------

LLM_COUNTERS: dict[str, dict[str, int]] = {
    m: {"calls": 0, "real_successes": 0, "fallbacks": 0} for m in ("off", "stub", "real")
}


def get_llm_mode() -> str:
    mode = os.environ.get("LLM_MODE", "real").strip().lower()
    if mode not in LLM_COUNTERS:
        raise ValueError(f"LLM_MODE must be off|stub|real; got {mode!r}")
    return mode


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_agent_justification(
    agent_state: AgentState | dict,
    yield_score: float,
    *,
    max_tokens: int = 300,
    _client: anthropic.Anthropic | None = None,
) -> str:
    """
    Call the Anthropic API to produce 1–2 sentences of operational justification
    for the agent's position, given a pre-computed yield_score.

    The yield_score MUST be computed by cost_functions.compute_yield_score()
    before calling this function — it is passed as a parameter and embedded
    directly into the prompt. The LLM never recomputes or modifies it.

    Retry policy: one automatic retry on any Anthropic SDK exception, then fall
    back to a deterministic template string so the negotiation never halts
    waiting on LLM availability. The fallback is clearly structured to still
    be meaningful in the negotiation transcript.

    Parameters
    ----------
    agent_state : AgentState | dict
        Validated (or auto-validated) satellite operator state.
    yield_score : float
        Pre-computed yield_score from cost_functions.compute_yield_score().
        The LLM narrates this number; it does NOT produce it.
    max_tokens : int
        Anthropic response token cap. Keep low (200–400) — narration only.
    _client : anthropic.Anthropic | None
        Injectable client for testing without hitting the real API. If None,
        a client is constructed from the ANTHROPIC_API_KEY env var.

    Returns
    -------
    str
        1–2 sentences of justification text, suitable for the negotiation console.
        Never raises — fallback is returned on all failure paths.
    """
    # Validate / coerce input
    if isinstance(agent_state, dict):
        agent_state = AgentState(**agent_state)

    mode = get_llm_mode()
    counters = LLM_COUNTERS[mode]
    counters["calls"] += 1

    if mode == "off":
        counters["fallbacks"] += 1
        return _build_fallback(agent_state, yield_score)
    if mode == "stub":
        time.sleep(float(os.environ.get("LLM_STUB_MS", "50")) / 1000.0)
        counters["fallbacks"] += 1
        return _build_fallback(agent_state, yield_score)

    client = _client or anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))

    prompt = _build_prompt(agent_state, yield_score)

    # Attempt 1 → retry once → fallback
    for attempt in range(2):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-6",
                max_tokens=max_tokens,
                messages=[{"role": "user", "content": prompt}],
            )
            text = response.content[0].text.strip()
            # Sanity guard: reject empty or absurdly long responses
            if len(text) < 10 or len(text) > 800:
                raise ValueError(
                    f"LLM response length {len(text)} chars outside expected range"
                )
            logger.info(
                "[operator_agent] %s round %d justification OK (%d chars)",
                agent_state.agent_id,
                agent_state.round_number,
                len(text),
            )
            counters["real_successes"] += 1
            return text

        except Exception as exc:  # noqa: BLE001
            if attempt == 0:
                logger.warning(
                    "[operator_agent] %s attempt 1 failed (%s) — retrying",
                    agent_state.agent_id,
                    exc,
                )
            else:
                logger.warning(
                    "[operator_agent] %s attempt 2 failed (%s) — using template fallback",
                    agent_state.agent_id,
                    exc,
                )

    counters["fallbacks"] += 1
    fallback = _build_fallback(agent_state, yield_score)
    logger.info(
        "[operator_agent] %s returning template fallback for round %d",
        agent_state.agent_id,
        agent_state.round_number,
    )
    return fallback


def build_negotiation_message(
    agent_state: AgentState | dict,
    *,
    _client: anthropic.Anthropic | None = None,
) -> dict:
    """
    Convenience wrapper: compute yield_score + get justification in one call,
    returning a dict shaped like the NegotiationMessage DB schema row.

    This is the function the orchestrator will call per negotiation round.
    The orchestrator receives a fully formed message ready to emit over the
    WebSocket, with the deterministic yield_score already inside it.

    Parameters
    ----------
    agent_state : AgentState | dict
        Validated operator state for this round.

    Returns
    -------
    dict
        Keys matching architecture.md §Database Schema negotiation_messages:
        agent_id, round, yield_score, justification_text, proposed_action.
    """
    if isinstance(agent_state, dict):
        agent_state = AgentState(**agent_state)

    # INVARIANT: yield_score is computed here, from deterministic Python,
    # before the LLM is called. This value cannot be changed by the LLM.
    yield_score = compute_yield_score(
        mvi=agent_state.mvi,
        fuel_margin_pct=agent_state.fuel_margin_pct,
        delta_v_mps=agent_state.delta_v_mps,
        miss_distance_km=agent_state.miss_distance_km,
    )

    justification = get_agent_justification(
        agent_state,
        yield_score,
        _client=_client,
    )

    return {
        "agent_id": agent_state.agent_id,
        "round": agent_state.round_number,
        "yield_score": yield_score,
        "justification_text": justification,
        "proposed_action": agent_state.proposed_action,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _build_prompt(state: AgentState, yield_score: float) -> str:
    action_context = (
        "You are proposing to execute an avoidance maneuver for your satellite."
        if state.proposed_action == "maneuver"
        else "You are proposing that the counterpart satellite should maneuver, not yours."
    )

    return (
        f"You are the autonomous negotiation agent for {state.operator}, "
        f"representing satellite {state.satellite_name}.\n\n"
        f"Conjunction negotiation — Round {state.round_number}.\n"
        f"{action_context}\n\n"
        f"Your satellite's computed yield_score is {yield_score:.2f} "
        f"(range 0–1; higher = more willing to maneuver).\n"
        f"Mission Value Index: {state.mvi:.1f}/10. "
        f"Fuel margin: {state.fuel_margin_pct:.0f}%. "
        f"Estimated Δv: {state.delta_v_mps:.1f} m/s. "
        f"Current predicted miss distance: {state.miss_distance_km:.2f} km.\n\n"
        "Write exactly 1–2 sentences of plain operational justification for your position. "
        "Reference the yield_score and key factors. Do not invent any numbers — "
        "only narrate the figures provided above."
    )
