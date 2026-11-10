# Manual crash test (PowerShell)

Goal: kill a worker mid-job; its lease expires, a surviving worker's reaper requeues the job, and all jobs still complete.

```powershell
# 1. Slow the jobs down and shorten the lease: ~1 s per job, lease 5 s. Two workers.
$env:LLM_STUB_MS = "500"; $env:LEASE_SECONDS = "5"
docker compose up -d redis
docker compose up -d --force-recreate --scale worker=2 worker

# 2. Clean slate, then enqueue 40 jobs.
docker compose run --rm worker python scripts/reset_redis.py
docker compose run --rm worker python scripts/enqueue.py --n 40 --policy fifo --seed 7

# 3. After ~5 s (a few jobs done, each worker mid-job), kill one worker hard (SIGKILL, no clean shutdown).
Start-Sleep -Seconds 5
docker kill aegis-v2-worker-1

# 4. Wait for the lease to expire (5 s) + a reaper tick (5 s) + the remaining work (~25 s).
Start-Sleep -Seconds 45
docker compose run --rm worker python scripts/queue_status.py
```

Expected: `completed: 40`, `failed: 0`, `queue depth: 0`, `processing: 0`, `retries total: >= 1`,
and the killed worker's container id under `dead workers` (its heartbeat stopped; it never deregistered).
The surviving worker logs `reaped: requeued=1` (`docker compose logs worker`).

Cleanup: `Remove-Item Env:LLM_STUB_MS, Env:LEASE_SECONDS; docker compose down`
