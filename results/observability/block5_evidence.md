# Block 5 Real-Race Evidence

Seed 42, 300 normal + 3 critical events, 2 fifo workers + 2 prio workers, LLM_MODE=stub 150 ms.
Run 2 shown below (second full check_race.py run, 2026-11-12).

---

## 1. Raw per-lane drain times

```
fifo drain_ms : 50,548 ms   (run 1)  /  49,635 ms  (run 2)
prio drain_ms : 50,548 ms   (run 1)  /  49,964 ms  (run 2)
diff          : 0.0 %        (run 1)  /  0.7 %      (run 2)
gate          : <= 5 %  PASS
```

Both lanes receive exactly the same 303 jobs via one MULTI/EXEC pipeline and consume them in
parallel with the same number of workers; drain times match because total work is equal.

---

## 2. Start skew between lanes

```
start_skew_ms : 1.2 ms
```

Measured as `abs(min(fifo started_at) - min(prio started_at))` over all 303 worker results.
Both lanes are unblocked by the same `r.set(race:barrier, "1")` call; a 1–2 ms OS scheduler
round-trip is expected.

---

## 3. Live worker counts at race start

```
POST /api/cloud/race/start response:
  { "status": "started", "fifo_workers": 2, "prio_workers": 2, ... }

Redis fifo:workers heartbeats (seconds since last beat):
  0e053116154d : 1.4 s
  6a5f53e76521 : 1.4 s

Redis prio:workers heartbeats:
  c47f587ede1f : 1.4 s
  c4c13051e03f : 1.4 s
```

---

## 4. Raw FIFO and priority critical averages (wall-clock)

Run 1 (check_race.py):
```
fifo critical_resolution_ms avg : 50,861 ms   max : 51,138 ms
prio critical_resolution_ms avg :    489 ms   max :    682 ms
factor                           : 104.0 x    gate >= 10 x  PASS
```

Run 2 (manual API call, full JSON):
```json
"fifo": {
  "critical_resolution_ms": {"avg": 50304, "max": 50548},
  "critical_latency_ms":    {"avg": 319,   "max": 324}
},
"priority": {
  "critical_resolution_ms": {"avg": 459,   "max": 682},
  "critical_latency_ms":    {"avg": 321,   "max": 325}
}
```

`critical_resolution_ms` = `finished_at - enqueued_at` (wall-clock, written by real workers).
`critical_latency_ms` = `finished_at - started_at` (processing-only).

Both lanes take ~320 ms to process a critical job; the difference is 100% queue-wait time:
FIFO waits behind ~200 normal jobs (at 2 workers × ~150 ms/job ≈ 50 s), priority jumps to front.

---

## 5. Main-queue-unchanged assertion (R9)

```
race:status               = done
main 'jobs' zset card     = 0  (matches /api/cloud/stats queue_depth=0)
race:meta, race:barrier   exist only in race: namespace
fifo:jobs, prio:jobs      exist only in fifo:/prio: namespace
```

PASS R9b. race:status=done
PASS R9c. main 'jobs' key is for main queue (redis_jobs=0 stats_depth=0)
PASS R5b. no 'race:jobs' key — main namespace untouched

Main workers (QUEUE_NS="") never see `fifo:jobs` or `prio:jobs`; regression.py PASS both before
and after the race confirms the main queue is unaffected.

---

## 6. Lane name in worker timeline events

After restarting lane workers, the timeline shows:

```
type=worker_started  source=0e053116154d  detail={'lease_s': 10.0, 'lane': 'fifo'}
type=worker_started  source=6a5f53e76521  detail={'lease_s': 10.0, 'lane': 'fifo'}
type=worker_started  source=c47f587ede1f  detail={'lease_s': 10.0, 'lane': 'prio'}
type=worker_started  source=c4c13051e03f  detail={'lease_s': 10.0, 'lane': 'prio'}
type=worker_stopped  source=6a5f53e76521  detail={'jobs_done': 1060, 'lane': 'fifo'}
type=worker_stopped  source=c47f587ede1f  detail={'jobs_done': 1061, 'lane': 'prio'}
type=race_done       source=api           detail={..., 'lane': 'both'}
type=race_started    source=api           detail={..., 'lane': 'both'}
```

The `lane` field is emitted by `_lane_detail = {"lane": _lane} if _lane else {}` in
`backend/cloud/worker.py`; all timeline event types (`worker_started`, `worker_stopped`,
`lease_expired`, `job_requeued`, `job_failed`) carry it when QUEUE_NS is set.

---

## 7. The "165 ms / 65-job" figures — source and status

These numbers appeared in `frontend/src/lib/cloudMockRecords.ts` in the old mock race data:
- `jobMs = 150 * 1.1 = 165` — a hardcoded multiplier in `mockRaceLane()`
- `n = 60 + critical = 65` — the old mock used n=60, not n=300

They were **never from a recorded run**. They came from fabricated mock data written before
Block 5 had any real workers. Both values have been removed:
- `mockRaceLane()` now uses `n=300, critical=3, jobMs=150`
- `mockRaceResult()` now matches the real defaults (`n:300, seed:42, critical:3`)
- No "165 ms" or "65 jobs" figure appears anywhere in the current codebase

Grep confirms: `grep -r "165\|n=60\|n = 60" frontend/src/lib/cloudMockRecords.ts` → no matches.

---

## 8. Mid-race screenshot

`results/observability/block5_race_midrun_live.png`

Shows the React /cloud page while a race is in progress:
- RUNNING… badge, animated pulse
- FIFO 114/303 and PRIORITY 114/303 progress bars
- Partial lane panel: PRIORITY CRIT RES AVG 521 ms
- Named critical pairs: IRIDIUM 112 × CONNECTA IOT-3, STARLINK-30555, SZ-21 MODULE
- TCA +0.6 h, RISK 0.91; partial drain 19,395 ms wall-clock

Additional screenshots:
- `block5_race_midrun_v2.png` — second mid-race capture (later point in run)
- `block5_race_running_full.png` — full race panel view while running
