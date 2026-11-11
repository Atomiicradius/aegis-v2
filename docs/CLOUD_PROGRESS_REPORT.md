# Aegis Cloud Redesign: Progress Report

**Repo:** `Atomiicradius/aegis-v2` (a clone of the original `Sxmxrth05/aegis`, extended)
**Report date:** 2026-11-11
**Purpose:** give a new contributor the full context of what exists beyond the original Aegis, how it fits together, how to run it, what is real vs simulated, and where to extend it safely.

> Figures in this report are those reported by the build sessions on the author's Windows machine (Docker Desktop, 2-worker default). Treat them as measured on one laptop, not as production capacity. Where a claim could not be verified it is marked as such. `CLOUD.md` and `DEMO.md` in the repo are the authoritative, as-built references; if they differ from this report, trust them.

---

## 1. One-paragraph summary

The **original Aegis** is a single-process demo: it detects one close approach between two satellites, lets two simulated operators negotiate who maneuvers, validates the plan with an independent SGP4 check, and records the resolution. The **cloud redesign** wraps that same negotiation engine in a distributed system: a Redis priority queue, stateless worker containers, lease-based crash recovery, a queue-depth autoscaler, persistence and outage tolerance, a metrics API, and a new `/cloud` page inside the original React app. The system was benchmarked (throughput scaling, FIFO vs priority, chaos/failure recovery) and extended with a pool of 400 real LEO satellites and an inspectable resolution-records panel.

**One-line pitch:** *Aegis resolves one conjunction autonomously; the cloud layer lets it handle hundreds at once, puts the most dangerous first, scales itself, and does not lose work when parts fail.*

---

## 2. Starting point: the original Aegis

### 2.1 What it was

| Area | Original implementation |
|---|---|
| Frontend | React + Vite + TypeScript, `react-globe.gl` globe. Pages: Landing, Monitor, Negotiate, History, About |
| Backend | FastAPI. WebSockets `/ws/monitor` and `/ws/negotiation/{id}`, REST for history/trajectory |
| Detection | `monitor_agent`: propagate ~20 curated objects with SGP4 to **one instant**, flag pairs under 5 km |
| Negotiation | `negotiator.py` (`NegotiationEngine`), `cost_functions.py` (yield score), `operator_agent.py` (LLM narration with template fallback; model `claude-sonnet-4-6`) |
| Validation | `validation_agent.py`: re-propagate the maneuvered object over a 6-hour window against the tracked set |
| Persistence | SQLite (`storage/db.py`), append-only sessions |
| Scripted demo | One scripted alert: ISS (25544) vs CSS/Tianhe (48274), 3.2 km miss |

### 2.2 Limitations that motivated the redesign

1. Negotiation ran **inside the WebSocket handler**: no job abstraction, one event at a time, in-process state (`ConnectionManager`, session sequence numbers).
2. Detection was a **snapshot**, not a TCA search: `tca_utc` was effectively "now".
3. Only ~15-20 objects; only 6 usable for synthetic events once docked ISS/CSS objects were excluded.
4. Alert IDs were `uuid5` of the NORAD pair only (no TCA), so the same pair twice collides.
5. `resolve_operator_profile` used Python `hash()` for non-numeric IDs, which is salted per process, so two workers could disagree.
6. Each process fetched CelesTrak live with a 10 s timeout and an in-process cache.
7. Dual import paths (`try: from backend.app... except ImportError: from app...`) throughout; `sys.path` edits in `monitor_agent.py`.
8. A hardcoded fallback alert when no conjunction was found.

---

## 3. What was built (chronological)

Work was done in blocks, each verified before the next, mostly via Claude Code with prompts supplied per block.

### Block 1: negotiation as a pure function
- `process_job(job) -> result` in `backend/cloud/jobs.py` (no WebSocket, SQLite or FastAPI).
- `LLM_MODE=off|stub|real` in `operator_agent.py` (stub sleeps `LLM_STUB_MS`, default 50, then returns the template text) with per-mode counters so results record which path ran (`fallbacks_used`).
- `backend/app/profiles.py`: `load_all_tracked_objects`, `resolve_operator_profile`, `KNOWN_OPERATOR_PROFILES` extracted from the orchestrator; **`hash()` replaced by `hashlib`** (stable across processes).
- Cache-only TLE loading (`OFFLINE=1`), `scripts/fetch_tles.py` to create a snapshot, fallback to the 5-object fixture.
- Dockerfile (python:3.11-slim, `PYTHONPATH=/repo:/repo/backend`), `scripts/smoke.py`.
- Event IDs: `uuid5` over sorted pair **plus TCA**.
- Measured: negotiation CPU about 2 ms/job; with stub it is about 104 ms.

### Block 2: Redis queue and workers
- `backend/cloud/queue.py`: Redis sorted set `jobs`, job payloads, atomic **Lua pop** that moves a job into a `processing` hash with a lease deadline, `complete()` writing `result:<id>` with **SETNX** (idempotent), and an atomic **reaper** that requeues expired leases with the original score (up to `MAX_RETRIES`, then `failed`).
- `backend/cloud/worker.py`: single-threaded loop (one job at a time per process so `fallbacks_used` stays accurate), graceful SIGTERM, heartbeat, reaper every 5 s.
- `backend/cloud/events.py`: synthetic event generator (random pairs, miss 0.5-5 km, TCA 1-24 h after newest TLE epoch), **excluding docked ISS/CSS objects**.
- Scripts: `enqueue.py`, `queue_status.py`, `reset_redis.py`, `verify_queue.py`, `throughput.py`, `crash_test.md`.
- `docker-compose.yml` with `redis` and a `worker` service (no `container_name`, so `--scale` works).

### Block 3: risk score, priority scheduling, benchmark
- `backend/cloud/risk.py`: `risk = 0.5*closeness + 0.3*urgency + 0.2*mission`, where `closeness = clamp(1 - miss_km/5)`, `urgency = clamp(1 - hours_to_tca/24)`, `mission = max MVI of the two operators`. `CRITICAL_THRESHOLD = 0.8`. **This is a heuristic risk score, not a collision probability** (TLEs carry no covariance).
- Priority policy with **aging**: priority score = `enqueued_at * AGING_PER_SEC - risk` (default 0.002/s); FIFO score = `enqueued_at`.
- Heartbeat moved to a daemon thread so long jobs do not look dead.
- `scripts/storm.py` (live demo driver), `bench_run.py`, `benchmark.py` (host-side sweep), `plot_results.py`.

### Block 4: stats API, dashboard, autoscaler
- `backend/cloud/api.py` (FastAPI): `GET /api/cloud/stats`, `POST /api/cloud/storm`, `POST /api/cloud/critical`, `POST /api/cloud/reset`. A plain HTML fallback dashboard at the API root.
- `scripts/autoscaler.py` (host-side, stdlib, calls `docker compose` via subprocess): scales on queue depth, min 2, max 8, target 25 queued+processing per worker, 2 s interval, scale **up immediately**, scale **down one step at a time** after a 10 s cooldown.
- `DEMO.md` with tested PowerShell steps.

### Block 5: `/cloud` page integrated into the original React app
- Cloud API host port moved to **8010** (the old backend and frontend use 8000). CORS added for the Vite origin.
- `frontend/src/pages/Cloud.tsx`, route `/cloud`, nav item `CLOUD`, one link from Landing.
- Data layer: `lib/cloudApi.ts`, `lib/useCloudStats.ts` (1 s polling, last 120 samples, pauses when the tab is hidden).
- **Mock mode** for rehearsal/screenshots: `/cloud?mock=idle|storm|scaled|critical|dead|down` (shows an unmissable "MOCK DATA" chip; never active without the param).
- UI: "Cluster Scope" radar hero (queue core, 8 worker slots, job packets, amber critical packets, dead-node and re-claim visuals), telemetry grid, latency and critical-latency panels (with "priority advantage" readout), sparklines (inline SVG), worker register, control dock (Storm 800 priority/fifo, Inject 3 critical, Reset), link-down state, scope callout.
- Design follows the app's language (`context/ui-tokens.md`, `ui-rules.md`): square cells, mono uppercase labels, no filled colored cards.
- **Vendored change:** a small `OFFLINE` check in `celestrak.get_raw_tles()` so the old backend reads the snapshot instead of the network.

### Extras branch (`extras`), Tasks 0 and A to D

**Task 0: safety gate.** `scripts/regression.py` (reset, 300-event priority storm, inject 3 critical, assert 303 completed, 0 failed, queue/processing 0, critical avg latency under overall avg / 5, at least 2 live workers). The gate = `regression.py` + `verify_queue.py`.

**Task A: 400 real LEO satellites for the storm.**
- `scripts/fetch_tle_pool.py` (run once, needs network) writes `backend/app/data/cache/tle_pool.json` (force-added to git; the cache dir is ignored).
- Pool (400): OneWeb 105, Starlink 105, "active" subset 104, Iridium NEXT 80, stations 6. LEO only, TLE epochs 8-10 Nov 2026, docked ISS/CSS objects dropped, de-duplicated, no group over 40%.
- `EVENT_POOL=pool|curated` (default `pool` in containers). Falls back to curated with a loud warning if the file is missing.
- Derived operator profiles by constellation group (deterministic, hashlib).
- **Validation cost control:** validating against the full 400-object pool cost about 79.6 ms/job (budget was about 15 ms). Design chosen: **bounded validation set** = curated objects + the pair + the K nearest-orbit objects (`VALIDATION_K`, default 50, `VALIDATION_SET` env), about 13.9 ms/job. Objects too far in orbit to reach the pair are not screened.
- Stats gained `pool_size` and `pool_groups`; `/cloud` header shows `OBJECTS / <n>`.

**Task B: persistence and fault tolerance.**
- Redis with **AOF (`appendfsync everysec`)** on a named volume `redis-data`. `down` keeps data, `down -v` wipes it.
- `backend/cloud/resilience.py`: capped exponential backoff (0.2 s to 5 s, jitter). Workers and the API survive Redis outages; a finished job keeps its result in memory and retries `complete()` until it lands; the API returns `503 {"error":"redis_unavailable"}` during outages and recovers on its own.
- `scripts/chaos.py` and `plot_chaos.py` (results below).

**Task C: inspectable resolutions.**
- `process_job` also returns the transcript and resolution. The worker stores `transcript:<event_id>` (TTL about 2 h) for every critical job and a rolling window of the last 30 finished jobs, written atomically by one Lua script (`Queue.record`). `RECORD_TRANSCRIPTS=0` is a kill switch. Overhead: about 0.69 ms/job, no measurable throughput change in an A/B.
- API: `GET /api/cloud/recent?limit=10` (also appends up to 3 older critical rows so they stay visible) and `GET /api/cloud/resolution/{event_id}` (404 JSON when expired).
- `/cloud` "Resolution Records" panel styled like History's ledger; clicking a row opens the transcript using the **existing** `NegotiationConsole` and `ResolutionCard` components from the Negotiate page.
- **Edits to original components (all opt-in props, Negotiate unchanged):** `NegotiationConsole` `instant`; `ResolutionCard` `showTrajectoryLink` and `residualAsDistance`; a `.cloud-legible` CSS wrapper.

**Task D: background traffic on the Monitor globe (display-only).**
- 394 real satellites (the 400 minus the 6 already curated) drawn as one `THREE.Points` layer, propagated client-side with satellite.js from `frontend/public/traffic-pool.json` (generated by `scripts/export_traffic_pool.py`). Refreshes each satellite once per second in 4 slices. Layer cost about 2.4 ms per full pass.
- Controls: `?traffic=off|<N>|all`, `VITE_BACKGROUND_TRAFFIC` default. Silent no-op if the JSON fails to load. Runs only on `/monitor` live/conjunction views.
- Header line: `TRACKED 15 / BACKGROUND TRAFFIC 394 / DISPLAY ONLY, NOT SCREENED`.
- **Invariant verified before/after in a real browser:** the scripted ISS-CSS alert (25544 vs 48274, 3.200 km), register (13 nominal + 2 flagged), Open Resolution Desk, Negotiate resolving, and History are all unchanged.

---

## 4. Architecture (as built)

```text
                        +------------------+
 Browser  --polls-->    |  Cloud API :8010 |  (FastAPI: stats, storm, critical, reset,
 (/cloud)               +--------+---------+   recent, resolution)
                                 |
                                 v
 scripts / API  --enqueue-->  +---------+   Lua pop + lease    +-----------+
 (storm, critical)            |  Redis  | -------------------> | worker xN |  process_job():
                              | (AOF)   | <--- complete(NX) -- | (1 job at | NegotiationEngine
                              +----+----+      heartbeat       |  a time)  | + bounded SGP4 validation
                                   ^                           +-----------+
                                   |  reaper (every 5 s, in every worker):
                                   |  expired lease -> requeue (<= MAX_RETRIES)
                                   |
 host: scripts/autoscaler.py --reads queue depth--> `docker compose up --scale worker=N`
```

Original app (unchanged flow) runs separately: old backend on `:8000` serving Monitor/Negotiate/History over WebSockets; Vite dev server on `:5173` serves all pages including `/cloud`.

### 4.1 Redis contract (summary; see `CLOUD.md` for the full as-built version)

| Key | Type | Purpose |
|---|---|---|
| `jobs` | ZSET | member = event_id, score = priority (lower = sooner) |
| `job:<id>` | STRING | job JSON (pair, miss_km, tca_utc, risk, is_critical, score, enqueued_at) |
| `processing` | HASH | event_id -> lease deadline (epoch) |
| `result:<id>` | STRING | written with SETNX: status, worker_id, timings, retries, llm_mode, resolution_status, fallbacks_used |
| `retries:<id>` | counter | reaper retry count |
| `workers` | HASH | worker_id -> last heartbeat |
| `recent`, `recent_transcripts`, `recent_critical`, `transcript:<id>` | list / list / list / STRING | recent results and stored transcripts |
| `meta:policy` | STRING | last policy used (shown in the UI) |

**Semantics:** at-least-once delivery (a job can run twice if a lease expires on a live worker); the **first completion wins** (SETNX). After `MAX_RETRIES` (3) a job is written as `failed`. A worker is "alive" if its heartbeat is under 10 s old.

### 4.2 Stats API contract (`GET /api/cloud/stats`)

```json
{
  "queue_depth": 0, "processing": 0, "completed_total": 0, "failed_total": 0, "retries_total": 0,
  "workers": [{"id": "...", "last_heartbeat_age_s": 0.0, "alive": true, "jobs_done": 0}],
  "throughput_per_s": 0.0,
  "latency_ms": {"avg": 0, "p95": 0, "p99": 0},
  "critical_latency_ms": {"avg": 0, "max": 0, "count": 0},
  "policy": "priority", "llm_mode": "stub", "stub_ms": 150,
  "pool_size": 400, "pool_groups": {},
  "server_time": "..."
}
```
Latency covers the **last 500** results. Do not rename existing keys; the frontend types mirror this.

### 4.3 Environment variables

| Variable | Meaning | Default / note |
|---|---|---|
| `LLM_MODE` | `off` (template), `stub` (sleep then template), `real` (Anthropic API) | `stub` in containers |
| `LLM_STUB_MS` | simulated LLM call latency | 150 for demos |
| `LEASE_SECONDS` | job lease length | 30 (chaos tests used 10) |
| `MAX_RETRIES` | lease-expiry retries before `failed` | 3 |
| `POLICY` | `fifo` or `priority` | `priority` |
| `AGING_PER_SEC` | priority aging rate | 0.002 |
| `CRITICAL_THRESHOLD` | risk at/above which a job is critical | 0.8 |
| `EVENT_POOL` | `pool` or `curated` | `pool` |
| `VALIDATION_K`, `VALIDATION_SET` | bounded validation set size/mode | K = 50 |
| `RECORD_TRANSCRIPTS` | `0` disables transcript recording | on |
| `OFFLINE` | never touch the network for TLEs | `1` |
| `REDIS_URL` | Redis location | `redis://redis:6379` |
| `VITE_CLOUD_API` | frontend base URL for the cloud API | `http://localhost:8010` |
| `VITE_BACKGROUND_TRAFFIC` | Monitor traffic layer default | `all` |
| `ANTHROPIC_API_KEY` | only for `LLM_MODE=real` | never commit; use the git-ignored `.env` |

---

## 5. Results

### 5.1 Throughput scaling and FIFO vs priority
Setup: stub 150 ms (about 300 ms/job), 300 normal + 3 critical events, seed 42, **curated 15-object set** (measured before the 400 pool).

| Workers | Policy | Jobs/s | Overall avg latency (ms) | Critical avg (ms) | Critical max (ms) |
|---|---|---|---|---|---|
| 1 | fifo | 3.23 | 46,711 | 65,470 | 65,779 |
| 1 | priority | 3.24 | 46,701 | 853 | 1,162 |
| 2 | fifo | 6.46 | 23,382 | 32,751 | 32,955 |
| 2 | priority | 6.45 | 23,444 | 701 | 906 |
| 4 | fifo | 12.90 | 11,790 | 16,311 | 16,326 |
| 4 | priority | 12.87 | 11,829 | 559 | 560 |
| 8 | fifo | 25.64 | 6,006 | 7,998 | 7,999 |
| 8 | priority | 25.70 | 5,996 | 515 | 516 |

- Throughput scales about linearly with workers and is **identical under both policies**: priority costs nothing overall, it only reorders.
- Critical-job latency improves about 77x at 1 worker and about 15x at 8 workers.
- **After the 400-satellite pool (bounded validation):** 1 worker 3.07 jobs/s, 4 workers 12.33 jobs/s (4.0x, still linear); about 3.1 jobs/s per worker (about 4% lower than before, from ~14 ms of validation per job).
- Files: `results/benchmark.csv`, `throughput_vs_workers.png`, `critical_latency_fifo_vs_priority.png`.

### 5.2 Storm demo (2 workers, 600 normal jobs over 2 s, 3 critical injected at t=8 s)

| Policy | Critical avg | Critical max | Normal avg |
|---|---|---|---|
| priority | 454 ms | 660 ms | 45.9 s |
| fifo | 85,180 ms | 85,387 ms | 45.6 s |

Normal-job latency is the same under both policies; priority just moves critical jobs to the front.

### 5.3 Autoscaling
800-event priority storm starting at 2 workers: the autoscaler went 2 to 8 within about 2 s and drained the storm in **35.8 s** (about 92 s estimated at 2 workers). Scale-down back to 2 takes about 80 s by design. Log: `results/autoscaler_run.log`. (The ramp costs a few seconds, so the speedup is 2.6x, not 4x.)

### 5.4 Chaos / failure recovery (4 workers, 400 events, stub 150 ms, lease 10 s, 3 runs each)

| Scenario | Pass | Jobs lost | Failed | Retries | Fault to first retry | Fault to reconnect | Drain median / worst |
|---|---|---|---|---|---|---|---|
| kill 1 of 4 workers | 3/3 | 0 | 0 | 1 | 13.3 s | n/a | 35.8 / 35.9 s |
| kill 2 of 4 | 3/3 | 0 | 0 | 2 | 14.3 s | n/a | 53.3 / 53.3 s |
| kill 3 of 4 | 3/3 | 0 | 0 | 3 | 14.4 s | n/a | 105.5 / 105.6 s |
| Redis graceful restart | 3/3 | 0 | 0 | 0 | n/a | 1.1 s | 28.7 / 31.7 s |
| Redis hard kill | 3/3 | 0 | 0 | 0 | n/a | 6.1 s | 31.7 / 31.7 s |

- 15 runs, **0 jobs lost**. Drain times are measured from the fault, not from storm start. The slow 3-of-4 case is reduced capacity (one surviving worker), not slow recovery.
- **Caveat:** `docker kill` stops only the Redis process; the OS keeps its writes, so the roughly 1 s `everysec` loss window of a real system/power crash was **not** reproduced. Claims should say "AOF persistence, up to about 1 s of writes at risk on a full system crash".
- Files: `results/chaos.csv`, `results/chaos_recovery.png`.

### 5.5 Other measured costs
- Transcript recording: 0.69 ms/job; A/B throughput identical (6.15-6.17 jobs/s with and without).
- Monitor traffic layer: about 2.4 ms per full pass over 394 satellites (about 0.24% of a core); headless CPU numbers were dominated by the existing globe's software rendering and are not meaningful.
- `/cloud` page: adds about 2-3 points of style recalculation over other pages in headless testing; the globe already holds about one core in software GL.

---

## 6. Real vs simulated (be precise about this when presenting)

| Element | Status |
|---|---|
| Satellite names, NORAD IDs, TLEs/orbits (400 in the pool, 15 curated) | **Real** (CelesTrak, fetched once, cached; epochs 8-10 Nov 2026) |
| Propagation (SGP4) and the 6-hour validation | **Real computation** on real orbits (bounded satellite set in cloud mode) |
| Queue, leases, reaper, priority scheduler, autoscaler, workers, persistence, failure recovery | **Real machinery** |
| Conjunction events in a storm | **Synthetic**: random pairs with an injected 0.3-5 km miss distance |
| The Monitor's ISS-CSS alert (3.2 km) | **Scripted scenario** with a fixed TCA (`2026-01-14T06:12:00Z`), not a live prediction |
| Operator mission value, fuel margin, delta-v budget | **Simulated** (hand-written for known satellites, derived deterministically from constellation group for others) |
| LLM negotiation text in storms | **Simulated** (template + 150 ms sleep). `LLM_MODE=real` exists but is not used in benchmarks |
| Risk score | **Heuristic** (not a collision probability; TLEs have no covariance) |
| Scale | **Single host** Docker Compose; stands in for Kubernetes/KEDA |

---

## 7. Repository state, branches and tags

| Branch | Contents |
|---|---|
| `main` | Original app + Blocks 1-5 (as pushed by the author before the extras work). **Verify with `git log`**; whether `extras` has been merged is not recorded here |
| `cloud-redesign` | Blocks 1-4 (backend, queue, workers, API, autoscaler, benchmarks) |
| `cloud-ui` | Block 5 (`/cloud` page in the React app) |
| `extras` | Tasks 0, A, B, C, D on top of the above |

Tags: `demo-safe-main` (safe fallback), `extras-A-ok`, `extras-B-ok`, `extras-C-ok`, `extras-D-ok`.

Known doc issue: `DEMO.md` on `main` had control characters in its Windows startup paths; this was repaired on `extras`.

### Key paths

```text
backend/cloud/            jobs.py queue.py worker.py events.py risk.py api.py resilience.py static/index.html
backend/app/profiles.py   operator profiles + tracked-object loading (hashlib-stable)
backend/app/data/cache/   tle_snapshot.json (15), tle_pool.json (400)   [force-added to git]
frontend/src/pages/Cloud.tsx, lib/cloudApi.ts, lib/useCloudStats.ts, components/cloud/*
frontend/public/traffic-pool.json         394 satellites for the Monitor layer
frontend/src/components/globe/BackgroundTraffic.ts
scripts/                  enqueue storm autoscaler benchmark bench_run chaos regression verify_queue
                          fetch_tles fetch_tle_pool export_traffic_pool plot_results plot_chaos check_stats ...
results/                  benchmark.csv chaos.csv *.png autoscaler_run.log  cloud-ui/  monitor-traffic/
CLOUD.md  DEMO.md         as-built cloud contract and the tested demo script
```

---

## 8. How to run it (Windows / PowerShell)

Three windows, plus a fourth for the autoscaler. `DEMO.md` has the authoritative commands.

```powershell
# Window 1: cloud stack (Docker Desktop must be running)
cd D:\aegis-v2
docker compose up -d redis api
docker compose up -d --scale worker=2 worker
Invoke-RestMethod http://localhost:8010/api/cloud/stats

# Window 2: original backend (must be run from the backend folder)
cd D:\aegis-v2\backend
$env:OFFLINE = "1"
python -m uvicorn app.main:app --port 8000

# Window 3: frontend
cd D:\aegis-v2\frontend
npm install        # first time
npm run dev        # open http://localhost:5173 (use /cloud for the cluster page)
```

Ports: old backend `8000`, cloud API `8010` (host) to `8000` (container), Vite `5173`, Redis `6379`.

**Reset between runs** (Redis now persists data, so leftovers resume processing):
1. Stop the autoscaler (Ctrl+C).
2. `docker compose up -d --no-recreate --scale worker=2 worker`
3. Click `RESET` on `/cloud` (or `Invoke-RestMethod -Method Post http://localhost:8010/api/cloud/reset`).
4. Confirm: queue 0, live workers 2, `docker compose ps` shows 2 workers.

Plain fallback dashboard: `http://localhost:8010`. Rehearse the look without the stack: `/cloud?mock=storm` etc.

### Regression gate (run after every change)

```powershell
python scripts/regression.py
docker compose run --rm worker python scripts/verify_queue.py
```
`verify_queue.py` flushes the Redis database. Code and scripts are **baked into the image**: run `docker compose build` after editing them.

---

## 9. Known issues and fragile spots

- `npm run build` fails (satellite.js top-level-await vs Vite's output format). Identical on the base branch; the demo uses `npm run dev`. `npm run lint` and `tsc` have known errors only in `OrbitalSituation.tsx`.
- The "critical" tiles count every job with risk >= 0.8, including naturally high-risk ones, and the stats window covers only the last 500 results (the critical tile can read 0 after a very large storm).
- Re-running `enqueue.py --seed S` without a reset enqueues nothing (deterministic event IDs; finished jobs are skipped). `API storm` reseeds by time but can still collide in rare cases.
- At-least-once delivery means a lease expiring on a live worker causes a duplicate run (first result wins, work is wasted). Heartbeat is a thread, so long jobs no longer look dead, but real-LLM jobs may exceed the 30 s lease.
- A pop reply lost to a connection drop leaves a job leased to a worker that does not know it; the reaper requeues it (one extra retry).
- Priority ties/starvation: aging at 0.002/s means a critical job loses to a normal one only after about 400 s of extra waiting.
- The engine's `residual_risk` on approved resolutions is actually a **distance in km**, but the original Negotiate page prints it as a percent. `/cloud` shows it correctly as a distance; the original page was left alone.
- Storm events almost always approve in round 1, so the rejection/re-negotiation path is rarely exercised in cloud runs.
- The cloud API has **no authentication** (open reset endpoint, CORS limited to localhost). Fine for a local demo, not for exposure.
- `chaos.py`'s autoscaler check is Windows/PowerShell-specific. TLEs age (pool fetched 2026-11-10): regenerate with `fetch_tle_pool.py` then `export_traffic_pool.py`.
- The Monitor background traffic depends on `react-globe.gl`'s `globe.getCoords` and scene API; failures are swallowed by design, so a library upgrade could silently disable it.
- The Monitor's 15 curated dots are a static snapshot while the 394 background points drift (slowly). This is intentional but visible over time.

---

## 10. Rules of the road for new contributors

1. **Do not change the scripted ISS-CSS flow** (Monitor alert 25544 vs 48274 at 3.200 km, Open Resolution Desk, Negotiate, History). It is the demo's one synthetic close approach.
2. **Background traffic and storm satellites must never enter** the Monitor's detection, register, alert channel or history DB.
3. **Do not rename stats API keys** or change the Redis contract without updating `CLOUD.md` and the frontend types.
4. **Do not modify vendored agent code** (`backend/app/agents/*`) unless unavoidable; list any change. Existing vendored edits: `operator_agent.py` (`LLM_MODE`, counters), `celestrak.py` (`OFFLINE` check).
5. **Frontend:** follow `context/ui-tokens.md`, `ui-rules.md`, `ui-registry.md` (no filled colored cards; reuse shared components; register new ones). No new npm dependencies without need.
6. Work on a branch off `extras`, run the regression gate, and do not merge to `main` without a clean 5-step flow (reset, storm, autoscaler 2 to 8, inject critical, kill one worker with the autoscaler stopped; final completed == total, failed 0).
7. Never commit secrets (`.env`, `ANTHROPIC_API_KEY`).

---

## 11. Suggested next features (rough value vs risk)

| Idea | Why | Notes / risk |
|---|---|---|
| **Windowed conjunction detection with real TCA search** | Biggest remaining gap vs "real" conjunction screening; makes time-to-TCA in the risk score meaningful | Touches `monitor_agent`; keep the scripted scenario intact |
| **Distribute screening** (shard pairwise propagation across workers) | The truly CPU-bound part; would make scaling curves non-trivially bend | New job type, new benchmark |
| **Real-Claude sample endpoint** (`LLM_MODE=real` for about 5 critical jobs, backoff on 429, key from env only) | Shows real narration in the cloud flow | Rate limits; must record which path ran |
| **Kubernetes + KEDA on kind** | Replaces "Compose stands in for K8s" with the real thing | Heavy on a laptop; only if the course needs it |
| **Postgres for results/history** | Durable, queryable records beyond Redis TTLs | Touches `db.py` migration |
| **Prometheus + Grafana** or push-based updates (WebSocket/SSE) instead of 1 s polling | Observability; lower latency UI | Extra services |
| `critical_completed_total` counter in stats | Fixes the critical-tile window artifact and enables exact critical packets in the radar | Small API + UI change |
| Pause the globe on non-globe routes | Cuts CPU on every page (globe renders even on `/history`) | Existing code; measure before/after |
| Fix the `residual_risk` label on the Negotiate page | Removes the percent-vs-km display bug | Original-page change |
| Auth on the cloud API, rate limiting | Hardening | Not needed for a local demo |
| Use the 400-pool in real Monitor detection (opt-in) | Real alerts on the globe | High risk to the scripted flow; gate behind a flag |

---

## 12. Quick glossary

- **Conjunction:** a predicted close approach between two space objects. **TCA:** time of closest approach.
- **TLE / SGP4:** standard orbital element sets and the propagator that turns them into positions.
- **Lease / reaper:** a job is "leased" to a worker for a limited time; if the lease expires (worker died), the reaper puts the job back in the queue.
- **Idempotent write:** repeating the write (a duplicate completion) does not change the stored result (SETNX).
- **Aging:** priority bonus for waiting time so low-risk jobs cannot starve forever.
- **KEDA:** Kubernetes event-driven autoscaling; the queue-depth logic in `autoscaler.py` is what a KEDA ScaledObject would express.
