"""Fetch CelesTrak TLEs ONCE and write backend/app/data/cache/tle_snapshot.json.

This is the only place in the cloud path that touches the network. Run it on a
connected machine; the worker/container then reads the snapshot (OFFLINE=1).

    python scripts/fetch_tles.py
"""

from __future__ import annotations

import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "backend")]

from backend.app.data.celestrak import fetch_tle_group, parse_tle_text, write_cache  # noqa: E402
from backend.app.profiles import SNAPSHOT_PATH  # noqa: E402


def main() -> int:
    now = datetime.now(timezone.utc)
    try:
        objects = parse_tle_text(fetch_tle_group(), fetched_at=now)
    except Exception as exc:  # noqa: BLE001
        print(f"FETCH FAILED: {exc!r} - no snapshot written (fixture will be used)", file=sys.stderr)
        return 1
    if not objects:
        print("Fetch succeeded but yielded zero locked objects - no snapshot written", file=sys.stderr)
        return 1
    write_cache(objects, SNAPSHOT_PATH)
    stale = sum(o.is_stale for o in objects)
    print(f"Wrote {len(objects)} TLEs to {SNAPSHOT_PATH} ({stale} stale)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
