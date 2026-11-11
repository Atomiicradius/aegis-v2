# DEMO.md — live demo script (PowerShell, from `D:\aegis-v2`)

Timings are from rehearsals on this machine (stub 150 ms/call = ~0.3 s per job per worker, ~3.2 jobs/s per worker).
Use **two PowerShell windows**: A = commands, B = autoscaler. Dashboard: http://localhost:8010

## Start everything (three windows)
The React app (`/cloud` page) needs the original backend on :8000, the cloud stack (API on :8010) and Vite on :5173.
```powershell
# window 1 - cloud stack (Docker Desktop running)
cd D:\aegis-v2
Set-Content .env "LEASE_SECONDS=10"
docker compose up -d redis api
docker compose up -d --scale worker=2 worker

# window 2 - original backend (OFFLINE=1: TLEs come from the committed snapshot, never waits on CelesTrak)
cd D:\aegis-v2\backend
$env:OFFLINE = "1"
python -m uvicorn app.main:app --port 8000

# window 3 - React app
cd D:\aegis-v2\frontend
npm run dev
```
Open http://localhost:5173/cloud . The nav badge (top right) reads **Live** on every page once window 2 is up; **Reconnecting** if the backend is down.
The plain HTML dashboard at http://localhost:8010 still works and needs none of windows 2-3.

## 0. Pre-flight (5 min before)
```powershell
cd D:\aegis-v2
docker info | Select-Object -First 1                       # Docker Desktop must be running
Set-Content .env "LEASE_SECONDS=10"                        # short lease so the crash demo is fast; read by every compose call, any window
docker compose down                                        # keeps the Redis volume; add -v only to wipe it
docker compose build                                       # only needed after code changes
docker compose up -d redis api
docker compose up -d --scale worker=2 worker
Invoke-RestMethod -Method Post http://localhost:8010/api/cloud/reset
start http://localhost:8010
```
Checklist: dashboard dot is **green**, Live workers = **2**, everything else 0, policy/LLM pill shows `stub (150 ms)`.
In the React `/cloud` header strip the **objects / 400** chip shows the size of the real-satellite pool the storm draws from (`006` = curated fallback: the pool file is missing or `EVENT_POOL=curated`).
After pulling code or the pool file you must `docker compose build` (the pool JSON is baked into the image).
Between rehearsals always run the reset line again (`Invoke-RestMethod -Method Post http://localhost:8010/api/cloud/reset`;
`python scripts/reset_redis.py` does NOT work on the host - redis is only installed in the image; use
`docker compose run --rm worker python scripts/reset_redis.py` if the API is down).

## 1. Quiet system (30 s)
Say: queue + 2 workers, nothing happening. Point at the heartbeat ages (< 2 s) in the worker table.

## 2. Storm: 800 events, 2 workers (about 90 s if left alone)
Click **Storm 800 (priority)** (or: `Invoke-RestMethod -Method Post -Uri http://localhost:8010/api/cloud/storm -ContentType 'application/json' -Body '{"n":800,"critical":0,"policy":"priority"}'`).
Look at: Queue depth jumps to ~800 within 2 s, throughput settles at ~6.1-6.4 jobs/s (about 3.1 per worker: stub sleep + ~14 ms of validation per job), latency p95 climbs.

## 3. Autoscaler 2 -> 8 (35 s drain instead of 92 s)
Window B, started right after the storm (it also works if started before):
```powershell
cd D:\aegis-v2; python scripts\autoscaler.py
```
Look at: log `SCALE UP -> 8` within ~2 s; the **Live workers tile flashes** and the green sparkline steps 2 -> 8; throughput ~21-25 jobs/s; queue empty after ~36 s.
Scale-down is deliberately slow: one worker per ~13 s (cooldown 10 s) back to 2, ~80 s total. Leave it running for step 4, stop it (Ctrl+C) before step 5.

## 4. Critical injection mid-storm
While the queue is still deep (start a fresh storm if needed): click **Inject 3 critical**.
Look at: **Critical latency** tile (orange) avg ~0.5 s while normal latency is many seconds. The `jobs` count there also includes normal events
whose risk happened to be >= 0.8 - they are critical by definition and priority picks them first. Compare: reset, run **Storm 800 (fifo)**, inject 3 critical -> they wait for the whole queue (tens of seconds).

**Resolution records** (React `/cloud`, panel under the sparklines): every finished job appears as a row (object pair with real satellite names, miss km, risk, rounds, outcome, delta-v, latency), newest first, updated every 2 s. The injected critical jobs are marked with the **amber rule + dot + CRITICAL chip** and stay pinned at the bottom of the table after the storm moves on. Move the pointer over the table to freeze it; click **open →** on a row to see the full negotiation transcript and the final decision record (the same components as the Negotiate page). Say: "each row is a real negotiation the workers ran: real satellites, synthetic close approach". A row whose transcript is older than 2 h shows "Transcript expired". Needs the `api` + `worker` image rebuilt after pulling (`docker compose build`).

## 5. Kill a worker (use a deep queue; needs the autoscaler STOPPED)
```powershell
# window B: Ctrl+C the autoscaler. If it keeps running it restarts the killed container within 2 s and no dead worker shows.
Invoke-RestMethod -Method Post http://localhost:8010/api/cloud/reset
docker compose up -d --scale worker=4 worker
Invoke-RestMethod -Method Post -Uri http://localhost:8010/api/cloud/storm -ContentType 'application/json' -Body '{"n":800,"critical":0,"policy":"priority"}'
Start-Sleep -Seconds 6
docker kill (docker compose ps -q --status running worker | Select-Object -Last 1)
```
Look at: ~10 s later the worker row turns **red / DEAD** (stays visible); at ~12 s **Retries = 1** (the lease of its in-flight job expired,
a surviving worker's reaper requeued it); Failed stays 0 and Completed still reaches 800. Bring it back: `docker compose up -d --scale worker=4 worker`.
(Rehearsed: dead=1 at +9 s, retries=1 at +12 s, 803/803 done, failed=0.)

## 6. Benchmark figures
```powershell
start results\throughput_vs_workers.png
start results\critical_latency_fifo_vs_priority.png
Get-Content results\benchmark.csv
```
Say: the events use real satellites and real orbits (Starlink, OneWeb, Iridium NEXT, ... from CelesTrak) but the close approaches are invented for load - none is a real conjunction. Throughput scales ~linearly (3.2 -> 25.7 jobs/s for 1 -> 8 workers); priority costs no throughput and cuts critical latency 15-77x. Re-run: `python scripts\benchmark.py` (~7 min, FLUSHES Redis).

## 7. Optional: Redis outage (rehearsed, ~45 s)
Start a deep storm (step 2), then:
```powershell
docker compose stop redis      # dot goes red, /cloud shows CLUSTER LINK DOWN, workers log "redis unavailable ... retrying"
Start-Sleep -Seconds 10
docker compose start redis     # within ~5 s everything is back; the storm resumes and finishes
```
Look at: Completed still reaches the full total, Failed = 0, Retries = 1-3 (leases that expired during the outage). Nothing restarts (`docker compose ps`).
Chaos figures for the 5 fault scenarios: `start results\chaos_recovery.png` (re-run: `python scripts\chaos.py`, ~35 min, stop the autoscaler first, FLUSHES Redis).

## Cleanup
```powershell
Remove-Item .env; docker compose down        # keeps the Redis volume (queue + results survive the next `up`)
docker compose down -v                       # ONLY if you want a completely empty Redis (deletes the volume)
```

## If X fails, do Y
| Symptom | Do |
|---|---|
| Dashboard blank / red dot | `docker compose ps`; `docker compose logs api --tail 20`; `docker compose up -d redis api`; hard refresh (Ctrl+F5) |
| `docker` commands hang or "cannot connect" | start Docker Desktop, wait for the whale to stop animating |
| Redis down (API returns 503 `redis_unavailable`, dot red, `/cloud` shows CLUSTER LINK DOWN) | `docker compose up -d redis` - workers and API reconnect by themselves within ~5 s (backoff cap), no restart needed; the queue is kept (AOF volume). Reset before the next storm |
| API down, Redis fine | `docker compose up -d api`; drive the demo with `docker compose run --rm worker python scripts/storm.py --n 800 --policy priority --critical 3 --critical-after 8 --watch --reset` |
| Port 8010 (cloud API) or 8000 (old backend) in use | `netstat -ano \| findstr :8010`, `taskkill /PID <pid> /F`; or change the host side of `"8010:8000"` in docker-compose.yml (and `VITE_CLOUD_API`) |
| Workers not scaling | autoscaler window shows `ERROR`? run `docker compose ps`; check it runs from `D:\aegis-v2`; `python scripts\autoscaler.py --dry-run` to see its decisions; set manually: `docker compose up -d --scale worker=8 worker` |
| Live workers stuck at 0 after reset | wait 2-3 s (heartbeats re-register); else `docker compose up -d --scale worker=2 worker` |
| Dead worker never shows in step 5 | autoscaler still running (restarted it) - Ctrl+C it and kill again |
| Retry takes ~35 s instead of ~12 s | `.env` missing, so lease is 30 s: `Set-Content .env "LEASE_SECONDS=10"; docker compose up -d --force-recreate --scale worker=4 worker` |
| Numbers look stale after code edits | `docker compose build` then `docker compose up -d --force-recreate api worker` (code is baked into the image) |
| Same storm does nothing the 2nd time | reset first (finished events are skipped as duplicates) |
