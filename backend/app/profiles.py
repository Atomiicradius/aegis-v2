"""
profiles.py — tracked-object loading + operator profiles, extracted from
orchestrator/orchestrator.py with all WebSocket/orchestrator coupling removed.

Differences from the original (deliberate, for the cloud worker path):
  * TLE loading is CACHE-ONLY by default (OFFLINE=1). The network is never
    touched; objects come from data/cache/tle_snapshot.json (written by
    scripts/fetch_tles.py), falling back to the 5-object fixture.
    Set OFFLINE=0 to allow the original live-first celestrak.get_raw_tles().
  * No TTL cache: the result is memoised for the life of the process.
  * No DOCKED_OBJECT_GROUPS / _HARDCODED_ALERT fallback — callers fail loudly.
  * Non-numeric NORAD IDs use a hashlib-based stable hash instead of the
    per-process-randomised builtin hash().
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from backend.app.agents.negotiator import OperatorProfile
from backend.app.data.scenario import FIXTURES_DIR
from backend.app.schemas.negotiation import AgentId
from backend.app.schemas.tracked_object import TrackedObject

logger = logging.getLogger(__name__)

SNAPSHOT_PATH = Path(__file__).parent / "data" / "cache" / "tle_snapshot.json"


def is_offline() -> bool:
    """OFFLINE defaults to on: the worker path must never hit the network."""
    return os.environ.get("OFFLINE", "1").strip().lower() in ("1", "true", "yes", "on")


def load_tracked_objects_fixture() -> list[TrackedObject]:
    """The 5-object scripted-demo set (data/fixtures/tracked_objects.json)."""
    with open(FIXTURES_DIR / "tracked_objects.json", encoding="utf-8") as f:
        raw = json.load(f)
    return [TrackedObject(**obj) for obj in raw]


def _read_snapshot_tles():
    """RawTLE list from the snapshot, or None if no snapshot file exists."""
    from backend.app.data.celestrak import read_cache

    if not SNAPSHOT_PATH.exists():
        return None
    return read_cache(SNAPSHOT_PATH)  # a corrupt snapshot raises — loud on purpose


def _propagate_raw(raw_tles) -> list[TrackedObject]:
    """Build TrackedObjects from RawTLEs, each propagated to its own TLE epoch.

    Epoch (not wall-clock "now") keeps SGP4 well inside its validity window and
    makes the result independent of when the process runs. The negotiation
    engine only consumes the TLE lines (validation re-propagates itself), so
    the stored position/velocity are informational.
    """
    from backend.app.agents.monitor_agent import build_satellite, propagate

    results: list[TrackedObject] = []
    for raw in raw_tles:
        try:
            epoch = datetime.fromisoformat(raw.epoch_utc)
            if epoch.tzinfo is None:
                epoch = epoch.replace(tzinfo=timezone.utc)
            sat = build_satellite(raw.tle_line1, raw.tle_line2)
            results.append(
                propagate(
                    satellite=sat,
                    norad_id=raw.norad_id,
                    time_utc=epoch,
                    name=raw.name,
                    tle_line1=raw.tle_line1,
                    tle_line2=raw.tle_line2,
                )
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[profiles] skipping %s: propagation failed (%s)", raw.norad_id, exc)
    return results


_tracked_objects: list[TrackedObject] | None = None
_tracked_objects_source: str | None = None


def load_all_tracked_objects() -> list[TrackedObject]:
    """Snapshot (or live, if OFFLINE=0) -> fixture. Memoised per process."""
    global _tracked_objects, _tracked_objects_source
    if _tracked_objects is not None:
        return _tracked_objects

    objects: list[TrackedObject] = []
    source = "fixture"

    if is_offline():
        raw = _read_snapshot_tles()
        if raw:
            objects = _propagate_raw(raw)
            source = "snapshot"
    else:
        from backend.app.data.celestrak import get_raw_tles

        objects = _propagate_raw(get_raw_tles())
        source = "live"

    if not objects:
        objects = load_tracked_objects_fixture()
        source = "fixture"

    logger.info("[profiles] loaded %d tracked objects from %s", len(objects), source)
    _tracked_objects, _tracked_objects_source = objects, source
    return objects


def tracked_objects_source() -> str:
    """'snapshot' | 'live' | 'fixture' — which source load_all_tracked_objects() used."""
    load_all_tracked_objects()
    assert _tracked_objects_source is not None
    return _tracked_objects_source


# Known profiles for CelesTrak locked satellites; stable hash fallback for any other NORAD ID
KNOWN_OPERATOR_PROFILES: dict[str, tuple[str, float, float, float]] = {
    # norad_id: (operator_name, mvi, fuel_margin_pct, delta_v_mps)
    "25544": ("NASA / Roscosmos", 0.95, 15.0, 1.8),
    "48274": ("CMSA", 0.70, 45.0, 2.4),
    "44713": ("SpaceX", 0.40, 80.0, 3.5),
    "36086": ("Roscosmos", 0.85, 25.0, 1.5),
    "49044": ("Roscosmos", 0.90, 20.0, 1.6),
    "49271": ("Arianespace / Debris", 0.15, 5.0, 0.8),
    "53239": ("CMSA", 0.75, 40.0, 2.2),
    "54216": ("CMSA", 0.75, 42.0, 2.2),
    "66052": ("JAXA", 0.35, 60.0, 2.0),
    "66515": ("CMSA Cargo", 0.65, 55.0, 2.5),
    "66906": ("Astroscale", 0.50, 70.0, 3.0),
    "67683": ("GeoInformatics", 0.45, 65.0, 2.8),
    "67685": ("SpaceX", 0.40, 75.0, 3.2),
    "67686": ("UiTM Tech", 0.38, 70.0, 2.7),
    "67687": ("Leopard Space", 0.42, 68.0, 2.9),
    "67688": ("HMU Aerospace", 0.48, 62.0, 3.1),
    "67796": ("SpaceX / NASA", 0.92, 35.0, 2.5),
    "68689": ("Northrop Grumman", 0.60, 30.0, 2.0),
    "68837": ("Roscosmos", 0.75, 50.0, 2.2),
    "69049": ("CMSA Cargo", 0.80, 65.0, 3.0),
    "69180": ("CMSA Crewed", 0.95, 40.0, 2.5),
}


def _stable_id_number(norad_id: str) -> int:
    if norad_id.isdigit():
        return int(norad_id)
    return int.from_bytes(hashlib.sha256(norad_id.encode("utf-8")).digest()[:8], "big")


def resolve_operator_profile(
    norad_id: str,
    satellite_name: str = "",
    agent_id: AgentId = AgentId.OPERATOR_A,
) -> OperatorProfile:
    """Resolves or constructs an OperatorProfile for any satellite."""
    if norad_id in KNOWN_OPERATOR_PROFILES:
        op_name, mvi, fuel_pct, dv_mps = KNOWN_OPERATOR_PROFILES[norad_id]
    else:
        op_name = f"Operator-{norad_id}"
        num_id = _stable_id_number(norad_id)
        mvi = round(0.35 + ((num_id * 17) % 55) / 100.0, 2)
        fuel_pct = round(15.0 + ((num_id * 31) % 70), 1)
        dv_mps = round(1.5 + ((num_id * 13) % 25) / 10.0, 1)

    return OperatorProfile(
        agent_id=agent_id,
        operator_name=op_name,
        satellite_name=satellite_name or f"SAT-{norad_id}",
        norad_id=norad_id,
        mvi=mvi,
        fuel_margin_pct=fuel_pct,
        delta_v_mps=dv_mps,
    )
