# Block 5 Real-Race Evidence

Seed 42, 300 normal + 3 critical events, 2 fifo workers + 2 prio workers, LLM_MODE=stub 150 ms.
Multiple runs shown; all from real Docker workers and real Redis wall-clock timestamps.

---

## 1. How drain_ms is computed — per-lane, not shared

`_build_lane_result` in `backend/cloud/api.py` lines 374–380:

```python
finished = [r for r in results if r.get("finished_at")]
if finished:
    first_enq = min(datetime.fromisoformat(r["enqueued_at"]) for r in results if r.get("enqueued_at"))
    last_fin  = max(datetime.fromisoformat(r["finished_at"])  for r in finished)
    drain_ms  = round((last_fin - first_enq).total_seconds() * 1000)
```

`results` is the lane's own result list: `fifo_results` (from `fifo:result:*` keys, written by fifo workers) for FIFO, `prio_results` (from `prio:result:*` keys, written by prio workers) for priority. Each lane's `max(finished_at)` comes from that lane's own workers' wall-clock writes. No value is shared between lanes.

**Why the values can be nearly equal:**
- `enqueued_at` for the same job IS identical in both lanes — the MULTI/EXEC pipeline stamps the same `enqueued_at` for a job regardless of which queue it goes into. So `min(enqueued_at)` = same for both lanes (same t0 + microseconds).
- `max(finished_at)` is per-lane, written by whichever worker in that lane processed the last job. Both lanes have the same number of workers (2) processing the same number of jobs (303) at the same stub duration (150 ms). Equal capacity + equal load means the last job in each lane often finishes within milliseconds of the other.

**Raw per-lane drain times across four runs:**

```
Run 1 (check_race.py):        fifo=50,861 ms   prio=489 ms* → drain fifo=51,084 ms  prio=51,081 ms  diff=3 ms (0.006%)
Run 2 (manual API call):      fifo drain=50,548 ms   prio drain=50,548 ms   diff=0 ms (0.000%)
Run 3 (check_race.py fresh):  fifo drain=53,014 ms   prio drain=53,011 ms   diff=3 ms (0.006%)
Run 4 (check_race.py final):  fifo drain=51,732 ms   prio drain=51,738 ms   diff=6 ms (0.012%)
```

*Run 1 R6 line reports critical_resolution_ms averages; drain_ms is in R7.

The 0.0% display in check_race.py R7 is a rounding artefact — the raw diff is 0–6 ms out of ~51 s. Gate threshold is 5% (2,500 ms). These values are **not** from a shared end time; they reflect that equal-capacity lanes drain at nearly the same real clock instant.

---

## 2. Start skew between lanes

```
Run 2: start_skew_ms = 1.2 ms
Run 4: start_skew_ms = 1.4 ms (from check_race.py R4 sample / race:result:latest)
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

Run 2 full JSON:
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

Run 3 (check_race.py R6): `fifo=52,795 ms  prio=551 ms  factor=95.8×`
Run 4 (check_race.py R6): `fifo=51,510 ms  prio=537 ms  factor=95.9×`

`critical_resolution_ms` = `finished_at − enqueued_at` (wall-clock, written by real workers).
`critical_latency_ms`    = `finished_at − started_at`  (processing-only).

Both lanes take ~315–325 ms to process a critical job. The ~51 s gap is entirely queue-wait time:
FIFO waits behind ~200 normal jobs (at 2 workers × 150 ms/job ≈ 50 s), priority jumps to front.

Named critical pairs (consistent across all runs, seed 42):
```
IRIDIUM 112 × STARLINK-30555  lead_h=0.4644  risk=0.911
IRIDIUM 112 × SZ-21 MODULE    lead_h=0.7875  risk=0.882
IRIDIUM 112 × CONNECTA IOT-3  lead_h=0.6176  risk=0.908
```

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
`backend/cloud/worker.py`. All timeline event types (`worker_started`, `worker_stopped`,
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
