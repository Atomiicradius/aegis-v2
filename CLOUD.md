# CLOUD.md — as-built cloud contract (branch `cloud-redesign`)

One conjunction negotiation = one job. `process_job(job) -> dict` (`backend/cloud/jobs.py`) is a pure function (no WS/DB/FastAPI);
workers pull jobs from Redis and call it. Commands are PowerShell, run from the repo root.

## Redis keys (all optionally prefixed by `Queue(ns=...)`, default none)
| key | type | meaning |
|---|---|---|
| `job:<id>` | string | job JSON + `score` |
| `jobs` | zset | id -> score; **lowest score pops first** |
| `processing` | hash | id -> lease deadline (unix s, Redis TIME) |
| `owner` | hash | id -> worker_id (debug only) |
| `result:<id>` | string | result JSON, written once (`SET NX`) |
| `retries:<id>` | string int | expired-lease count for the job |
| `counters` | hash | `completed`, `failed`, `retries_total` |
| `workers` | hash | worker_id -> last heartbeat (unix s) |
| `recent` | list | last 1000 completed ids, newest first (stats API window) |
| `worker_done` | hash | worker_id -> jobs completed |
| `meta:policy` | string | policy used by the last enqueue/storm (dashboard) |
| `transcript:<id>` | string | `{summary, transcript, resolution}` of a finished job, **TTL 2 h**; kept for every critical job and for the last 30 finished jobs (deleted when a normal job leaves that window) |
| `recent_transcripts` | list | summary JSON of the last 30 finished jobs, newest first (trimmed in the same atomic script that stores the transcript) |
| `recent_critical` | list | summary JSON of the last 10 critical jobs, newest first |
| `recent_retried` | list | summary JSON of the last 10 jobs that required >1 attempt, newest first (**Block 1**) |
| `trace:<id>` | string | per-job timing trace JSON, **TTL 1 h** (**Block 1**); `{enqueued_at, risk, is_critical, attempts[], completed_at, final_status}` — see trace structure below |
| `timeline` | list | system-level event log, capped to 2000, newest first (**Block 1**); each entry: `{seq, ts, type, source, detail, event_id?}` |
| `timeline:seq` | string int | monotonically increasing counter for timeline ordering (**Block 1**) |
| `timeline:outage:<ts_s>` | string | NX sentinel (120 s TTL) to deduplicate `redis_down` events per second across workers (**Block 1**) |

`<id>` = `event_id` = uuid5(sorted pair + `tca_utc`), so the same encounter always has the same id.

## Job JSON (`Job`, `backend/cloud/jobs.py`)
`event_id, primary_id, secondary_id, miss_distance_km, tca_utc, mission_priority, enqueued_at, risk, is_critical`, plus `score` when stored.
`enqueued_at` is stamped by `Queue.submit()` at enqueue time (ISO UTC).

## Result JSON
`event_id, worker_id, enqueued_at, started_at, finished_at, retries, risk, is_critical, llm_mode, resolution_status, fallbacks_used`
plus from `process_job`: `maneuvering_agent, delta_v_mps, rounds, duration_ms, negotiate_ms` (**Block 1**: `negotiate_ms` added for trace waterfall, existing keys unchanged).
`resolution_status` = `approved | approved_no_action | no_safe_maneuver_found | error` (worker caught an exception; has `error`) `| failed` (written by the reaper after retries ran out; `started_at` empty, `finished_at` is an epoch string).

## Scoring (`backend/cloud/scoring.py`)
`fifo`: score = enqueued_at (epoch s). `priority`: score = enqueued_at * AGING_PER_SEC - risk. 1 s of waiting = 0.002 risk, so a 0.8 risk gap takes 400 s to overturn: no starvation, but inside a storm the riskiest job goes first. Equal risk: older first.
Risk (`risk.py`): `0.5*closeness + 0.3*urgency + 0.2*mission`; closeness = 1 - miss/5 km, urgency = 1 - hours_to_tca/24 (all clamped 0..1), mission = max MVI of the two operators. `is_critical = risk >= CRITICAL_THRESHOLD`.
For synthetic events "now" is the newest TLE epoch; `hours_to_tca` is the generator's offset from it.

## Queue rules (`backend/cloud/queue.py`, Lua scripts are atomic)
- **pop**: ZPOPMIN + lease (`now + LEASE_SECONDS`) in one script; ids that already have a result are skipped.
- **complete**: `SET result NX` — first completion wins, duplicates are ignored; always releases the lease and ZREMs any requeued copy.
- **reap**: every expired lease is removed; `retries += 1`; if `retries <= MAX_RETRIES` ZADD back with the ORIGINAL score, else write a `failed` result. Atomic, so two reapers never double-requeue.
- **enqueue/submit**: skipped if `result:<id>` already exists (idempotent).
- Delivery is at-least-once: a slow worker whose lease expired may run a job twice; the first result is kept.

## Worker (`python -m backend.cloud.worker`)
One job at a time per process (needed for `fallbacks_used`). Heartbeat in a daemon thread every 2 s; reaper every 5 s (between jobs); idle sleep 20 ms. SIGTERM: finish current job, deregister, exit. `worker_id` = hostname. A job that raises is recorded as `error` and still completed.
Live worker = heartbeat < 10 s old; dead = registered but silent >= 10 s.

## API + dashboard (`backend/cloud/api.py`, compose service `api`, http://localhost:8010)
`GET /` dashboard; `GET /api/cloud/recent?limit=10`, `GET /api/cloud/resolution/{event_id}` (see Resolution records); `GET /api/cloud/stats` (fixed key contract; **added** `pool_size` (int) and `pool_groups` ({group: count}) - existing keys unchanged); `POST /api/cloud/storm {n,critical,critical_after_s,policy}`, `/critical {k}`, `/reset` (FLUSHDB). Details and demo flow: DEMO.md.

**Block 1 additions (additive only — existing endpoints unchanged):**
- `GET /api/cloud/timeline?since_seq=<n>&limit=<m>` — returns `{events: [...], last_seq: N}` in ascending order. Default limit 200. 503 when Redis unavailable. Use `since_seq` to poll incrementally (pass `last_seq` back as `since_seq`).
- `GET /api/cloud/trace/{event_id}` — returns `{event_id, enqueued_at, risk, is_critical, attempts[], completed_at, final_status}`. 404 JSON `{"error":"not_found"}` when expired or unknown.
- `GET /api/cloud/recent` — extended: each row now includes `attempts` (int, = retries+1) and up to 5 retried-job rows appended.

**Block 1 timeline event types** (emitted best-effort; never block a job):
| type | source | when |
|---|---|---|
| `worker_started` | worker_id | on worker startup |
| `worker_stopped` | worker_id | on graceful shutdown |
| `lease_expired` | worker_id | reaper finds expired lease (per job); detail: `{prev_worker}` |
| `job_requeued` | worker_id | reaper requeues job (per job) |
| `job_failed` | worker_id | reaper exhausts retries and writes failed result |
| `redis_down` | worker | first retryable Redis error detected (deduplicated per second) |
| `redis_up` | worker | Redis recovered |
| `storm_started` | api | POST /api/cloud/storm; detail: `{n, policy, critical}` |
| `reset` | api | POST /api/cloud/reset |

**Block 1 trace attempt outcomes:** `pending`, `completed`, `lease_expired`, `error`, `duplicate_discarded`, `dead_lettered` (reserved for Block 4).

**Block 1 kill switches:**
| var | default | effect |
|---|---|---|
| `TIMELINE` | `1` | `0` = no timeline writes |
| `RECORD_TRACES` | `1` | `0` = no trace writes |

## Satellite pool (EVENT_POOL=pool)
**Real satellites, real orbits, synthetic close approaches.** The storm picks random pairs from ~400 real LEO satellites
(CelesTrak TLEs, fetched once by `fetch_tle_pool.py`, groups: starlink, oneweb, iridium-NEXT, stations, a general `active` subset);
the miss distance and time to closest approach of each event are invented. Nothing here is a real conjunction.
- Pool file `backend/app/data/cache/tle_pool.json` (~140 KB, committed with `git add -f`): `{groups: {norad: group}, objects: [RawTLE...]}`. Workers never touch the network.
- The old Monitor/globe and its curated set are unchanged (`profiles.py` is cloud-only).
- Derived operator profiles per constellation (`profiles._GROUP_PROFILES`, hashlib-stable): Starlink low MVI / big fuel / dv 3-4, OneWeb mid, Iridium higher MVI, stations high MVI. `KNOWN_OPERATOR_PROFILES` untouched. Transcripts use the real TLE names (`ONEWEB-0149`, ...). The profile numbers are illustrative, not operator data.
- **Validation cost** (`process_job`, LLM_MODE=off, 50 seeded jobs, mean ms): full-pool validation grows with the pool (15 objects 3.1, 100: 19.8, 250: 49.0, 400: **79.6**); curated+pair is flat 3.5 but ignores the pool; `bounded` (curated + pair + 50 nearest-orbit objects, mean motion then inclination) is **13.9 ms / p95 14.6** at 400. Default = `bounded`. Trade-off: a maneuver is screened against the 15 curated objects + 50 orbit neighbours, not all 400 (the far ones cannot reach the pair's orbit anyway).
- Old API without the pool keys, or no pool file: `/cloud` shows `objects / --` or 006.

## Resolution records (Task C)
What the system DECIDED, not only how fast. `process_job` returns the full negotiation next to its summary: `transcript` (list of `NegotiationMessage`, JSON identical to what the Negotiate page renders), `resolution` (`Resolution`), `primary_name`, `secondary_name`. The worker pops those four out before building `result:<id>` (so it stays small) and, only when its `complete()` was the first one (duplicates never record twice), calls `Queue.record()`: ONE atomic Lua script that SETs `transcript:<id>` (EX 2 h), LPUSHes the summary onto `recent_transcripts`, deletes the transcript of any normal job that fell out of the 30-job window, trims the list, and, for a critical job, also pushes onto `recent_critical` (last 10). Memory is bounded however big the storm is (about 30 + 10 transcripts of ~2.3 KB). `record()` is best effort: a failure (Redis down >20 s) skips the transcript, never the result. `RECORD_TRANSCRIPTS=0` switches recording off.
Measured cost: `record()` 0.69 ms per job (against ~320 ms stub jobs); 200-job, 2-worker throughput with records on vs off: 6.16 vs 6.16 jobs/s.

- `GET /api/cloud/recent?limit=10` (1..30). Rows, newest first: `{event_id, primary_id, secondary_id, primary_name, secondary_name, miss_km, risk, is_critical, rounds, resolution_status, maneuvering_agent, delta_v_mps, latency_ms, finished_at}`. The last `limit` finished jobs, plus up to 3 of the newest critical jobs that already scrolled out of that view (appended at the bottom, so the priority lane stays visible after a storm). One pipelined round trip, two LRANGE, no SCAN/KEYS.
- `GET /api/cloud/resolution/{event_id}` -> `{event_id, summary, transcript, resolution}`; **404** `{"error":"not_found","detail":...}` once the TTL expired, if it never existed or if the id is malformed.
- Redis down: both return the same `503 {"error":"redis_unavailable"}` as every other endpoint.
- Semantics note: for an APPROVED resolution the engine's `residual_risk` is a **distance in km** (`min(expected miss, closest third party)`), not a probability. The Negotiate page prints it as a percent (unchanged); `/cloud` shows it as "Residual min distance" (`ResolutionCard residualAsDistance`).
- The transcripts are the deterministic template narration (`LLM_MODE=stub`), real satellite names, synthetic close approaches. No real-LLM text.

## Persistence and Redis outages (Task B)
**Persistence.** Redis runs `redis-server --appendonly yes --appendfsync everysec` with its data on the named volume `redis-data`.
- `docker compose down` (no `-v`) and `docker compose restart redis` keep the queue, results, counters and leases. `docker compose down -v` deletes the volume = empty Redis. `POST /api/cloud/reset` / `scripts/reset_redis.py` (FLUSHDB) still work (the flush is written to the AOF).
- **`everysec` is not zero-loss.** Redis fsyncs the AOF once a second, so an *operating-system / VM / power* crash can lose up to ~1 s of acknowledged writes (up to ~2 s if the disk stalls). A graceful restart/stop loses nothing (the AOF is flushed on shutdown). A mere process kill (`docker kill` = SIGKILL of the redis process) loses essentially nothing, because what Redis already `write()`-ed is held by the OS: our hard-kill chaos runs lost 0 jobs and we could not provoke the 1 s window with them. The AOF is a consistent prefix: a Lua script (pop / complete / reap) is replayed whole or not at all.
- What a lost second means here: **a lost enqueue = that event never runs** (nothing re-creates it; only possible while a storm is still being enqueued, ~2 s for 800 events). **A lost completion = the job is re-run** (its lease is still in the AOF, it expires, the reaper requeues it) and the first result written wins (`SET NX`), so the final counts stay right; `retries_total` shows the duplicate.

**Outage tolerance** (`backend/cloud/resilience.py`). A Redis `ConnectionError` / `TimeoutError` / `BusyLoadingError` (Redis still loading its AOF) is never fatal:
- `Backoff`: sleep 0.2 s, 0.4 s, 0.8 s ... capped at 5 s, each multiplied by a random 0.5-1.0 (jitter, so workers do not retry in lock-step). One log line when the outage starts, one when it ends.
- **Worker**: the main loop (reap + pop) waits and resumes. A job that finishes while Redis is down keeps its result in memory; `retry(complete)` loops until it lands (never dropped, process never exits). The heartbeat thread catches every error and resumes beating. Redis clients have a 5 s socket timeout / 2 s connect timeout so a dead connection raises instead of hanging.
- **API**: every Redis failure becomes `503 {"error": "redis_unavailable", "detail": ...}` (with CORS headers); the next request reconnects by itself. `/cloud` shows CLUSTER LINK DOWN and recovers on its own. The storm background task retries each chunk for up to 2 min.
- **Reaper after the outage**: leases that expired while Redis was away are requeued exactly once (the reap script removes the lease in the same atomic step); a worker whose lease expired still completes first-writer-wins and removes the requeued copy.
- Edge: if the connection dies after the pop script ran but before the worker got the reply, the job is leased to a worker that does not know it; the lease expires and the reaper requeues it (one `retries_total`, no loss).

## Chaos benchmark (`scripts/chaos.py`, `scripts/plot_chaos.py`)
4 workers, stub 150 ms, lease 10 s, priority, autoscaler OFF, killed workers are not restarted. N events (400), the fault ~6 s into the storm. Scenarios: kill 1 / 2 / 3 of 4 workers, graceful `docker compose restart redis`, hard `docker kill` of redis then `docker compose up -d redis`. Per run: jobs lost = expected - (completed + failed) (target 0), failed, retries_total (= duplicate executions), time to first retry, time to reconnect (redis scenarios: fault to the first good stats answer after a failed one), time to drain, max queue depth, anomalies. A run passes only if lost == 0 and failed == 0. Rows go to `results/chaos.csv`; `python scripts/plot_chaos.py` draws `results/chaos_recovery.png`. `--quick` = 1 run each, N=200.

**Measured** (this machine, 3 runs each, 400 events, `results/chaos.csv`, `results/chaos_recovery.png`; the sweep took 15.4 min):

| scenario | pass | jobs lost | failed | retries (dup. runs) | fault -> first retry | fault -> reconnect | drain median / worst |
|---|---|---|---|---|---|---|---|
| kill 1 of 4 | 3/3 | 0 | 0 | 1 | 13.3 s | n/a | 35.8 / 35.9 s |
| kill 2 of 4 | 3/3 | 0 | 0 | 2 | 14.3 s | n/a | 53.3 / 53.3 s |
| kill 3 of 4 | 3/3 | 0 | 0 | 3 | 14.4 s | n/a | 105.5 / 105.6 s |
| redis graceful restart | 3/3 | 0 | 0 | 0 | n/a | 1.1 s | 28.7 / 31.7 s |
| redis hard kill | 3/3 | 0 | 0 | 0 | n/a | 6.1 s | 31.7 / 31.7 s |

Reading it: one retry per killed worker (its in-flight job; the lease is 10 s, the reaper runs every 5 s, hence the 12-15 s to the first retry). Drain time follows the remaining capacity (4 workers ~12 jobs/s, 3 ~9, 2 ~6, 1 ~3). Redis faults cost no work: the queue is on the AOF volume and the workers/API just wait (reconnect = 1.1 s graceful, 6.1 s hard kill, which includes the container restart and the 0.2-5 s backoff).

## Env vars
| var | default | used by |
|---|---|---|
| `REDIS_URL` | `redis://redis:6379` | everything |
| `OFFLINE` | `1` | `profiles.py`: 1 = snapshot/fixture only, never the network; `0` = live CelesTrak |
| `LLM_MODE` | `real` (compose: `stub`) | `off` template now, `stub` sleep then template, `real` Anthropic |
| `LLM_STUB_MS` | `50` (compose: `150`) | stub sleep per justification (2 per job in round 1) |
| `LEASE_SECONDS` | `30` | lease length |
| `MAX_RETRIES` | `3` | reaper cap |
| `POLICY` | `fifo` | default for `--policy` in enqueue/storm |
| `AGING_PER_SEC` | `0.002` | priority score |
| `CRITICAL_THRESHOLD` | `0.8` | `is_critical` |
| `EVENT_POOL` | code `curated`; image/compose `pool` | `pool` = ~400 real LEO satellites from `tle_pool.json`; `curated` = the 15-object snapshot (6 usable). Missing pool file -> loud warning + curated |
| `VALIDATION_SET` | `bounded` | which objects the safety validation screens against: `full` \| `curated` \| `bounded` |
| `RECORD_TRANSCRIPTS` | `1` | `0` = the worker does not store transcripts / recent rows (kill switch) |
| `VALIDATION_K` | `50` | `bounded`: nearest-orbit objects added to curated + the pair |
| `TIMELINE` | `1` | **Block 1** `0` = disable all timeline writes |
| `RECORD_TRACES` | `1` | **Block 1** `0` = disable all trace writes |

## Scripts (`docker compose run --rm worker python scripts/<x>.py ...` unless HOST)
| script | does |
|---|---|
| `fetch_tle_pool.py [--max 400]` (HOST, network) | CelesTrak stations/iridium-NEXT/oneweb/starlink/active -> LEO + fresh (<=14 d) + not docked + de-duplicated, <=40% per group -> `tle_pool.json` (`git add -f`) |
| `measure_pool.py` | process_job ms vs pool size and validation set (LLM_MODE=off) |
| `regression.py` (HOST) | reset, 300-event priority storm + 3 critical, PASS/FAIL checks, exit 1 on failure (**FLUSHES Redis**) |
| `chaos.py [--quick]` (HOST) | fault-injection sweep, writes `results/chaos.csv` (**FLUSHES Redis**, autoscaler must be off, ~35 min full) |
| `plot_chaos.py` (HOST) | `results/chaos.csv` -> `results/chaos_recovery.png` (needs matplotlib) |
| `fetch_tles.py` (HOST) | fetch CelesTrak once -> `backend/app/data/cache/tle_snapshot.json` (tracked in git) |
| `smoke.py` | 10 synthetic jobs through `process_job`, no Redis |
| `enqueue.py --n N --policy P [--critical K --critical-after M] [--seed S]` | enqueue N normal (+K critical after M) |
| `queue_status.py` | depth, processing, completed, failed, retries, live/dead workers |
| `reset_redis.py` | FLUSHDB |
| `verify_queue.py` | PASS/FAIL: drain, priority vs fifo rank, idempotency, reaper |
| `throughput.py --n N` | wait for N completions, print jobs/s |
| `storm.py --n 600 --policy priority --critical 3 --critical-after 8 --watch [--reset]` | live demo: burst + critical injection, 1 line/s |
| `bench_run.py ...` | one benchmark run, prints one JSON line |
| `benchmark.py [--quick]` (HOST) | sweep workers x policy -> `results/benchmark.csv` (overwrites unless `--append`) |
| `plot_results.py` (HOST) | CSV -> `results/*.png` (needs matplotlib) |
| `crash_test.md` | manual `docker kill` test |
| `check_stats.py [--storm N]` (HOST) | poll `/api/cloud/stats` each second and print it |
| `autoscaler.py` (HOST) | scale `worker` 2..8 by queue depth (up fast, down 1 step per cooldown) |
| `check_trace.py [--skip-kill] [--skip-ab]` (HOST) | **Block 1** verify trace/timeline after a worker kill + A/B overhead measurement |

```powershell
docker compose build worker                          # REQUIRED after editing backend/ or scripts/ (baked into the image)
docker compose up -d redis; docker compose up -d --scale worker=4 worker
docker compose run --rm worker python scripts/queue_status.py
docker compose run --rm worker python scripts/storm.py --n 600 --policy priority --critical 3 --critical-after 8 --watch --reset
python scripts/benchmark.py --quick; python scripts/plot_results.py
pytest backend/cloud -q                              # host; PYTHONPATH must contain the repo root and backend/
docker compose down
```

## Gotchas
- Same `--seed` re-run without a reset is a **no-op**: event ids repeat and finished jobs are skipped. Reset first (`--reset`).
- `verify_queue.py`, `bench_run.py`, `benchmark.py`, `storm.py --reset` all **FLUSHDB**.
- Scripts and code are baked into the image: rebuild after edits.
- A job longer than `LEASE_SECONDS` is requeued while still running (duplicate work, first result kept). Real-LLM jobs need a bigger lease.
- Redis persists to the `redis-data` volume (AOF, everysec): `down` keeps the queue, `down -v` wipes it. Hard-crash loss <= ~1 s of writes (see Persistence). A leftover queue from an earlier session is still there after `up`: always reset before a demo.
- Stub-mode throughput scales ~linearly because the stub sleeps; real negotiation CPU is ~2 ms/job.
- With `EVENT_POOL=curated` only 6 non-docked objects exist in the snapshot (1 in the fixture, where `random_job` raises). Pool mode has ~400. With docked objects excluded every synthetic job approves in round 1.
- `scoring.py`/`risk.py` deliberately avoid importing `redis`, so their unit tests run on the host (redis is only installed in the image).
