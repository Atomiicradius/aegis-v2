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
import sys
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


POOL_PATH = Path(__file__).parent / "data" / "cache" / "tle_pool.json"


def event_pool_mode() -> str:
    """EVENT_POOL = 'pool' (hundreds of real LEO satellites) | 'curated' (the 15-object snapshot).

    The code default is 'curated' (= the pre-pool behaviour); the Docker image and
    docker-compose set EVENT_POOL=pool. Never touches the network either way.
    """
    mode = os.environ.get("EVENT_POOL", "curated").strip().lower()
    if mode not in ("pool", "curated"):
        raise ValueError(f"EVENT_POOL must be pool|curated; got {mode!r}")
    return mode


def _read_pool():
    """(RawTLE list, {norad_id: group}) from tle_pool.json (scripts/fetch_tle_pool.py), or None."""
    from backend.app.data.celestrak import RawTLE

    if not POOL_PATH.exists():
        return None
    data = json.loads(POOL_PATH.read_text(encoding="utf-8"))  # corrupt file raises: loud on purpose
    return [RawTLE(**o) for o in data["objects"]], dict(data["groups"])


_tracked_objects: list[TrackedObject] | None = None
_tracked_objects_source: str | None = None
_curated_ids: frozenset[str] = frozenset()
_pool_groups: dict[str, str] = {}      # norad_id -> constellation group (pool mode only)
_pool_active = False                   # True once the pool was really loaded


def load_all_tracked_objects() -> list[TrackedObject]:
    """Snapshot (or live, if OFFLINE=0) -> fixture, plus the pool when EVENT_POOL=pool. Memoised per process."""
    global _tracked_objects, _tracked_objects_source, _curated_ids, _pool_groups, _pool_active
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

    curated_ids = frozenset(o.norad_id for o in objects)
    groups: dict[str, str] = {}
    if event_pool_mode() == "pool":
        pool = _read_pool()
        if pool is None:
            msg = f"EVENT_POOL=pool but {POOL_PATH} is missing - FALLING BACK TO THE CURATED SET ({len(objects)} objects)"
            logger.warning("[profiles] %s", msg)
            print(f"WARNING: {msg}", file=sys.stderr, flush=True)
        else:
            raw_pool, groups = pool
            have = set(curated_ids)
            extra = _propagate_raw([r for r in raw_pool if r.norad_id not in have])
            objects = [*objects, *extra]       # curated first (keeps its TLEs), pool after
            source += "+pool"

    logger.info("[profiles] loaded %d tracked objects from %s", len(objects), source)
    _tracked_objects, _tracked_objects_source = objects, source
    _curated_ids, _pool_groups, _pool_active = curated_ids, groups, bool(groups)
    return objects


def curated_ids() -> frozenset[str]:
    """NORAD ids of the curated snapshot (always part of the validation set)."""
    load_all_tracked_objects()
    return _curated_ids


def object_group(norad_id: str) -> str:
    """Constellation group of a pool object ('starlink', 'oneweb', ...) or 'curated'."""
    load_all_tracked_objects()
    return _pool_groups.get(norad_id, "curated")


def pool_active() -> bool:
    """True when EVENT_POOL=pool AND tle_pool.json was found (False after the curated fallback)."""
    load_all_tracked_objects()
    return _pool_active


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


# Derived profiles for pool objects, by constellation: (operator, (mvi lo, hi), (fuel % lo, hi), (dv m/s lo, hi)).
# Mission value, fuel margin and available dv are *illustrative* (they drive the negotiation, they are not
# real operator data). Ranges follow what each constellation is: Starlink = cheap, numerous, strong
# electric propulsion; Iridium = a service that must not drop; stations = crewed/high value.
_GROUP_PROFILES: dict[str, tuple[str, tuple[float, float], tuple[float, float], tuple[float, float]]] = {
    "starlink": ("SpaceX Starlink", (0.30, 0.45), (60.0, 90.0), (3.0, 4.0)),
    "oneweb": ("OneWeb / Eutelsat", (0.40, 0.55), (50.0, 80.0), (2.0, 3.0)),
    "iridium-NEXT": ("Iridium Communications", (0.55, 0.75), (40.0, 70.0), (1.5, 2.5)),
    "stations": ("Station programme operator", (0.60, 0.85), (25.0, 55.0), (1.5, 2.5)),
}
# 'active' group: operator guessed from the real satellite name; anything else gets a neutral profile.
_NAME_OPERATORS: tuple[tuple[str, str], ...] = (
    ("KUIPER", "Amazon Kuiper"), ("QIANFAN", "Qianfan (SSST)"), ("GLOBALSTAR", "Globalstar"),
    ("FLOCK", "Planet Labs"), ("LEMUR", "Spire Global"), ("CAPELLA", "Capella Space"),
    ("ORBCOMM", "ORBCOMM"), ("NOAA", "NOAA"), ("IRIDIUM", "Iridium Communications"),
    ("STARLINK", "SpaceX Starlink"), ("ONEWEB", "OneWeb / Eutelsat"),
)
_DEFAULT_PROFILE = ("Commercial operator", (0.35, 0.60), (20.0, 70.0), (1.5, 3.5))


def _unit(norad_id: str, field: str) -> float:
    """Deterministic value in [0, 1): sha256, so it is identical across processes and machines."""
    digest = hashlib.sha256(f"{norad_id}|{field}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def _pool_profile(norad_id: str, name: str, group: str) -> tuple[str, float, float, float]:
    """(operator, mvi, fuel_margin_pct, delta_v_mps) for a pool object, stable across processes."""
    op, mvi_r, fuel_r, dv_r = _GROUP_PROFILES.get(group, _DEFAULT_PROFILE)
    if group not in _GROUP_PROFILES:
        upper = name.upper()
        op = next((label for prefix, label in _NAME_OPERATORS if upper.startswith(prefix)), op)
    pick = lambda r, f: r[0] + (r[1] - r[0]) * _unit(norad_id, f)  # noqa: E731
    return op, round(pick(mvi_r, "mvi"), 2), round(pick(fuel_r, "fuel"), 1), round(pick(dv_r, "dv"), 1)


def resolve_operator_profile(
    norad_id: str,
    satellite_name: str = "",
    agent_id: AgentId = AgentId.OPERATOR_A,
) -> OperatorProfile:
    """Resolves or constructs an OperatorProfile for any satellite."""
    if norad_id in KNOWN_OPERATOR_PROFILES:
        op_name, mvi, fuel_pct, dv_mps = KNOWN_OPERATOR_PROFILES[norad_id]
    elif norad_id in _pool_groups:
        op_name, mvi, fuel_pct, dv_mps = _pool_profile(norad_id, satellite_name, _pool_groups[norad_id])
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
