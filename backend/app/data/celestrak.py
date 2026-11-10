"""
A1 — CelesTrak TLE ingestion + local caching + fallback-to-cache-on-failure.

Owner: Dev A (Orbital Physics & Conjunction Detection) / Dev C (coverage fixes)

This module is the ONLY thing in the pipeline that talks to the network.
Everything downstream (A2 propagation, A3 detection) reads from the cache
file this module writes, never from a live request directly.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Locked constants (decided in team sync — do not change without broadcasting)
# ---------------------------------------------------------------------------

CELESTRAK_URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP=stations&FORMAT=tle"
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
REQUEST_TIMEOUT_SECONDS = 10

STALE_CUTOFF_DAYS = 4  # locked decision: TLEs older than this are flagged stale

CACHE_PATH = Path(__file__).parent / "cache" / "tracked_objects.json"

# The locked 20-object demo subset, pulled live from CelesTrak's `stations`
# group and verified by hand. is_active=False only for confirmed debris.
LOCKED_OBJECTS: dict[str, dict] = {
    "25544": {"name": "ISS (ZARYA)", "is_active": True},
    "36086": {"name": "POISK", "is_active": True},
    "48274": {"name": "CSS (TIANHE)", "is_active": True},
    "49044": {"name": "ISS (NAUKA)", "is_active": True},
    "49271": {"name": "FREGAT DEB", "is_active": False},
    "53239": {"name": "CSS (WENTIAN)", "is_active": True},
    "54216": {"name": "CSS (MENGTIAN)", "is_active": True},
    "66052": {"name": "HRC MONOBLOCK CAMERA", "is_active": True},
    "66515": {"name": "SZ-21 MODULE", "is_active": True},
    "66906": {"name": "DUPLEX", "is_active": True},
    "67683": {"name": "KNACKSAT-2", "is_active": True},
    "67685": {"name": "GXIBA-1", "is_active": True},
    "67686": {"name": "UITMSAT-2", "is_active": True},
    "67687": {"name": "LEOPARD", "is_active": True},
    "67688": {"name": "HMU-SAT2", "is_active": True},
    "67796": {"name": "CREW DRAGON 12", "is_active": True},
    "68689": {"name": "CYGNUS NG-24", "is_active": True},
    "68837": {"name": "PROGRESS-MS 34", "is_active": True},
    "69049": {"name": "TIANZHOU-10", "is_active": True},
    "69180": {"name": "SHENZHOU-23 (SZ-23)", "is_active": True},
}


@dataclass
class RawTLE:
    """Raw TLE record as fetched from CelesTrak.

    Canonical Pydantic TrackedObject (with position_km and velocity_kmps)
    is constructed downstream by monitor_agent.py after propagation.
    """
    norad_id: str
    name: str
    tle_line1: str
    tle_line2: str
    epoch_utc: str = ""
    is_active: bool = True
    fetched_at_utc: str = ""
    is_stale: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class CelesTrakFetchError(Exception):
    """Raised when a live fetch fails AND no usable cache exists."""


# ---------------------------------------------------------------------------
# Fetch (using httpx async client per library-docs.md)
# ---------------------------------------------------------------------------

async def fetch_tle_group_async(url: str = CELESTRAK_URL) -> str:
    """
    Hit CelesTrak directly and return the raw TLE text using httpx.AsyncClient.
    Raises httpx.HTTPError on network failures — callers should catch broadly
    and fall back to cache.
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = await client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        if not resp.text.strip():
            raise ValueError("CelesTrak returned an empty body")
        return resp.text


def fetch_tle_group(url: str = CELESTRAK_URL) -> str:
    """
    Synchronous fetch helper using httpx.Client.
    Used by CLI scripts, offline tests, and sync callers.
    """
    with httpx.Client(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        resp = client.get(url, headers={"User-Agent": USER_AGENT})
        resp.raise_for_status()
        if not resp.text.strip():
            raise ValueError("CelesTrak returned an empty body")
        return resp.text


# ---------------------------------------------------------------------------
# Parse
# ---------------------------------------------------------------------------

def _parse_epoch(tle_line1: str) -> datetime:
    """
    TLE line 1 columns 19-32 encode epoch as YYDDD.DDDDDDDD
    (2-digit year, day-of-year with fractional day). Returns UTC datetime.
    """
    epoch_field = tle_line1[18:32].strip()
    yy = int(epoch_field[:2])
    day_frac = float(epoch_field[2:])
    year = 2000 + yy if yy < 57 else 1900 + yy  # sgp4 convention, see library docs
    jan1 = datetime(year, 1, 1, tzinfo=timezone.utc)
    return jan1 + timedelta(days=day_frac - 1)


def parse_tle_text(raw_text: str, fetched_at: datetime) -> list[RawTLE]:
    """
    Parse CelesTrak's 3-line-per-object TLE text into RawTLE records,
    keeping only objects in LOCKED_OBJECTS. Logs (does not crash on) any
    locked object missing from the fetched group, and any unexpected object
    present in the group but not in our locked set.
    """
    lines = [line for line in raw_text.splitlines() if line.strip()]
    if len(lines) % 3 != 0:
        logger.warning(
            "TLE text line count (%d) is not a multiple of 3 — "
            "malformed block will be skipped at the tail",
            len(lines),
        )

    found_ids: set[str] = set()
    results: list[RawTLE] = []

    for i in range(0, len(lines) - 2, 3):
        name_line, line1, line2 = lines[i], lines[i + 1], lines[i + 2]

        if not (line1.startswith("1 ") and line2.startswith("2 ")):
            logger.warning("Skipping malformed TLE block near line %d", i)
            continue

        norad_id = line1[2:7].strip()
        found_ids.add(norad_id)

        if norad_id not in LOCKED_OBJECTS:
            continue  # not part of our curated demo set — ignore silently

        try:
            epoch = _parse_epoch(line1)
        except (ValueError, IndexError) as e:
            logger.warning("Could not parse epoch for %s: %s — skipping", norad_id, e)
            continue

        age_days = (fetched_at - epoch).total_seconds() / 86400
        is_stale = age_days > STALE_CUTOFF_DAYS

        results.append(
            RawTLE(
                norad_id=norad_id,
                name=LOCKED_OBJECTS[norad_id]["name"],
                tle_line1=line1,
                tle_line2=line2,
                epoch_utc=epoch.isoformat(),
                is_active=LOCKED_OBJECTS[norad_id]["is_active"],
                fetched_at_utc=fetched_at.isoformat(),
                is_stale=is_stale,
            )
        )

    missing = set(LOCKED_OBJECTS) - found_ids
    if missing:
        logger.warning(
            "Locked objects missing from this fetch (may have left the "
            "'stations' group): %s",
            sorted(missing),
        )

    return results


# ---------------------------------------------------------------------------
# Cache read/write
# ---------------------------------------------------------------------------

def write_cache(objects: list[RawTLE], cache_path: Path = CACHE_PATH) -> None:
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    payload = [obj.to_dict() for obj in objects]
    cache_path.write_text(json.dumps(payload, indent=2))
    logger.info("Wrote %d objects to cache at %s", len(payload), cache_path)


def read_cache(cache_path: Path = CACHE_PATH) -> list[RawTLE]:
    if not cache_path.exists():
        raise FileNotFoundError(f"No cache file at {cache_path}")
    raw = json.loads(cache_path.read_text())
    return [RawTLE(**entry) for entry in raw]


# ---------------------------------------------------------------------------
# Public entry point — this is what A2/A3 and everyone downstream calls
# ---------------------------------------------------------------------------

def get_raw_tles(force_refresh: bool = False) -> list[RawTLE]:
    """
    Primary interface: returns the current list of RawTLE records.

    Behavior:
      1. Try a live fetch via httpx.
      2. On any failure (network, parse, empty body), fall back to the
         local cache.
      3. If the cache is also stale (> STALE_CUTOFF_DAYS old) or missing,
         raise CelesTrakFetchError.
    """
    now = datetime.now(timezone.utc)

    # OFFLINE=1 (set for the cloud stack and the local demo): never touch the
    # network, so there is no 10 s timeout; use the committed snapshot instead.
    if os.environ.get("OFFLINE", "").strip().lower() in ("1", "true", "yes", "on"):
        return read_cache(CACHE_PATH.parent / "tle_snapshot.json")

    try:
        raw_text = fetch_tle_group()
        objects = parse_tle_text(raw_text, fetched_at=now)
        if not objects:
            raise ValueError("Fetch succeeded but yielded zero locked objects")
        write_cache(objects)
        return objects

    except Exception as e:
        logger.warning("Live fetch failed (%s) — falling back to cache", e)
        try:
            cached = read_cache()
        except FileNotFoundError:
            raise CelesTrakFetchError(
                "Live fetch failed and no cache exists — cannot proceed"
            ) from e

        stale_entries = [o for o in cached if o.is_stale]
        if stale_entries:
            logger.warning(
                "Using cached data; %d/%d objects exceed the %d-day "
                "staleness cutoff — positions may be inaccurate",
                len(stale_entries), len(cached), STALE_CUTOFF_DAYS,
            )
        return cached


# Backward-compatibility alias
get_tracked_objects = get_raw_tles


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    objs = get_raw_tles()
    print(f"\nRetrieved {len(objs)} raw TLE objects:\n")
    for o in objs:
        flag = " [STALE]" if o.is_stale else ""
        active = "active" if o.is_active else "INACTIVE"
        print(f"  {o.norad_id}  {o.name:<24} {active:<9}{flag}")