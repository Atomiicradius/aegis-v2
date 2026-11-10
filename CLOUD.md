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

`<id>` = `event_id` = uuid5(sorted pair + `tca_utc`), so the same encounter always has the same id.

## Job JSON (`Job`, `backend/cloud/jobs.py`)
`event_id, primary_id, secondary_id, miss_distance_km, tca_utc, mission_priority, enqueued_at, risk, is_critical`, plus `score` when stored.
`enqueued_at` is stamped by `Queue.submit()` at enqueue time (ISO UTC).

## Result JSON
`event_id, worker_id, enqueued_at, started_at, finished_at, retries, risk, is_critical, llm_mode, resolution_status, fallbacks_used`
plus from `process_job`: `maneuvering_agent, delta_v_mps, rounds, duration_ms`.
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

## Scripts (`docker compose run --rm worker python scripts/<x>.py ...` unless HOST)
| script | does |
|---|---|
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
- Redis has no persistence volume; restarting it loses the queue.
- Stub-mode throughput scales ~linearly because the stub sleeps; real negotiation CPU is ~2 ms/job.
- Only 6 non-docked objects exist in the snapshot (1 in the fixture, where `random_job` raises). With docked objects excluded every synthetic job approves in round 1.
- `scoring.py`/`risk.py` deliberately avoid importing `redis`, so their unit tests run on the host (redis is only installed in the image).
