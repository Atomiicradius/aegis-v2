"""Export the real-satellite pool as a small static file for the Monitor's display-only background traffic (HOST, stdlib).

    python scripts/export_traffic_pool.py

Reads backend/app/data/cache/tle_pool.json (scripts/fetch_tle_pool.py) and writes frontend/public/traffic-pool.json:
    {"fetched_at_utc": "...", "sats": [[name, tle_line1, tle_line2, group], ...]}
Satellites that are already in the curated snapshot (the 6 'stations' objects) are dropped: they are drawn as curated
objects, never as background traffic. The frontend propagates these TLEs client-side with satellite.js; nothing here
touches the backend, the detection loop or the alert channel.
"""
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
POOL = REPO / "backend/app/data/cache/tle_pool.json"
SNAPSHOT = REPO / "backend/app/data/cache/tle_snapshot.json"
OUT = REPO / "frontend/public/traffic-pool.json"

pool = json.loads(POOL.read_text(encoding="utf-8"))
curated = {o["norad_id"] for o in json.loads(SNAPSHOT.read_text(encoding="utf-8"))}
sats = [[o["name"], o["tle_line1"], o["tle_line2"], pool["groups"][o["norad_id"]]]
        for o in pool["objects"] if o["norad_id"] not in curated]
OUT.parent.mkdir(exist_ok=True)
OUT.write_text(json.dumps({"fetched_at_utc": pool["fetched_at_utc"], "sats": sats}, separators=(",", ":")), encoding="utf-8")
by_group: dict[str, int] = {}
for s in sats:
    by_group[s[3]] = by_group.get(s[3], 0) + 1
print(f"wrote {len(sats)} satellites ({OUT.stat().st_size / 1024:.0f} KB) to {OUT}; dropped {len(pool['objects']) - len(sats)} already curated")
print(by_group)
