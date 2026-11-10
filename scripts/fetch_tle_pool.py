"""Fetch a pool of REAL low-Earth-orbit satellites for the cloud storm (HOST, needs network).

    python scripts/fetch_tle_pool.py [--max 400] [--max-age-days 14]

Pulls several CelesTrak groups, keeps LEO objects with a fresh epoch, drops the docked
ISS/CSS objects, de-duplicates by NORAD id, caps any one group at 40% of --max and writes
backend/app/data/cache/tle_pool.json (git add -f; the cache dir is ignored).
This is the only network access of the pool feature: workers read the file (OFFLINE=1).
If any fetch fails, NOTHING is written (we never invent satellites) and the exit code is 1.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(REPO), str(REPO / "backend")]

import httpx  # noqa: E402

from backend.app.data.celestrak import CACHE_PATH, RawTLE, _parse_epoch, fetch_tle_group  # noqa: E402
from backend.cloud.events import DOCKED_OBJECT_GROUPS  # noqa: E402

POOL_PATH = CACHE_PATH.parent / "tle_pool.json"
URL = "https://celestrak.org/NORAD/elements/gp.php?GROUP={}&FORMAT=tle"
# Order matters for de-duplication: the first group to claim a NORAD id keeps it.
GROUPS = ["stations", "iridium-NEXT", "oneweb", "starlink", "active"]
LEO_MIN_REV_PER_DAY = 11.25      # ~ period < 128 min (<= ~2000 km)
GROUP_CAP_FRACTION = 0.40
DELAY_S = 3.0                    # be polite: CelesTrak throttles repeated/rapid requests
DOCKED = frozenset().union(*DOCKED_OBJECT_GROUPS)


def fetch(group: str) -> str:
    try:
        return fetch_tle_group(URL.format(group))
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        hint = " (rate limited / blocked: wait a few minutes and retry)" if code in (403, 429) else ""
        raise SystemExit(f"FETCH FAILED for group '{group}': HTTP {code}{hint}. Nothing written.")
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"FETCH FAILED for group '{group}': {exc!r}. No network? Nothing written.")


def parse(text: str, now: datetime, max_age_days: float) -> tuple[list[RawTLE], Counter]:
    """Keep well-formed, fresh, LEO TLEs. Returns (records, drop reasons)."""
    lines = [ln.rstrip() for ln in text.splitlines() if ln.strip()]
    out, drops = [], Counter()
    for i in range(0, len(lines) - 2, 3):
        name, l1, l2 = lines[i].strip(), lines[i + 1], lines[i + 2]
        if not (l1.startswith("1 ") and l2.startswith("2 ")):
            drops["malformed"] += 1
            continue
        try:
            nid, epoch = l1[2:7].strip(), _parse_epoch(l1)
            mean_motion = float(l2[52:63])
        except (ValueError, IndexError):
            drops["unparseable"] += 1
            continue
        if nid in DOCKED:
            drops["docked"] += 1
        elif mean_motion <= LEO_MIN_REV_PER_DAY:
            drops["not LEO"] += 1
        elif (now - epoch).total_seconds() / 86400 > max_age_days:
            drops["stale"] += 1
        else:
            out.append(RawTLE(norad_id=nid, name=name, tle_line1=l1, tle_line2=l2, epoch_utc=epoch.isoformat(),
                              is_active=True, fetched_at_utc=now.isoformat(), is_stale=False))
    return out, drops


def spread(items: list[RawTLE], k: int) -> list[RawTLE]:
    """k items evenly spaced over the id-sorted list: deterministic and orbit-diverse."""
    items = sorted(items, key=lambda r: int(r.norad_id) if r.norad_id.isdigit() else 0)
    if k >= len(items):
        return items
    return [items[(i * len(items)) // k] for i in range(k)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--max", type=int, default=400)
    ap.add_argument("--max-age-days", type=float, default=14.0)
    args = ap.parse_args()
    now = datetime.now(timezone.utc)
    cap = max(1, int(args.max * GROUP_CAP_FRACTION))

    seen: set[str] = set()
    candidates: dict[str, list[RawTLE]] = {}
    print(f"{'group':<14}{'fetched':>8}{'kept':>6}  dropped")
    for n, group in enumerate(GROUPS):
        if n:
            time.sleep(DELAY_S)
        text = fetch(group)
        recs, drops = parse(text, now, args.max_age_days)
        total = len(text.splitlines()) // 3
        fresh = [r for r in recs if r.norad_id not in seen]      # de-duplicate across groups
        if len(recs) - len(fresh):
            drops["duplicate"] += len(recs) - len(fresh)
        seen.update(r.norad_id for r in fresh)
        candidates[group] = fresh
        print(f"{group:<14}{total:>8}{len(fresh):>6}  {dict(drops) or '-'}")

    # Fill up to --max: every group may contribute at most `cap`; share what is left round-robin.
    take = {g: 0 for g in GROUPS}
    budget = args.max
    while budget > 0:
        moved = False
        for g in GROUPS:
            if budget > 0 and take[g] < min(cap, len(candidates[g])):
                take[g] += 1
                budget -= 1
                moved = True
        if not moved:
            break

    chosen: list[RawTLE] = []
    groups: dict[str, str] = {}
    for g in GROUPS:
        for r in spread(candidates[g], take[g]):
            chosen.append(r)
            groups[r.norad_id] = g
    if len(chosen) < 20:
        print(f"Only {len(chosen)} usable satellites - refusing to write a pool.", file=sys.stderr)
        return 1

    payload = {"fetched_at_utc": now.isoformat(), "max_age_days": args.max_age_days, "groups": groups,
               "objects": [r.to_dict() for r in chosen]}
    POOL_PATH.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
    size_kb = POOL_PATH.stat().st_size / 1024
    print(f"\nWrote {len(chosen)} satellites to {POOL_PATH} ({size_kb:.0f} KB)")
    for g, c in Counter(groups.values()).most_common():
        print(f"  {g:<14}{c:>4}  ({c / len(chosen):.0%})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
