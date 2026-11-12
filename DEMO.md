# DEMO.md — live demo script (PowerShell, from `D:\aegis-v2`)

> For a concise audience-facing version of this script, see PRESENT.md.
> For honest talking points (simulated vs real, latency caveats), see PRESENT.md §Honest talking points.

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

## Monitor background traffic (display only)
On `/monitor` the globe now shows **394 small blue dots = real LEO satellites** (Starlink, OneWeb, Iridium NEXT and others, from CelesTrak TLEs) drifting in real time behind the 15 curated objects. The header strip reads `TRACKED 15 / BACKGROUND TRAFFIC 394 / DISPLAY ONLY, NOT SCREENED`. They are NOT in the tracking register, the screening, or any alert: the scripted ISS vs CSS 3.2 km alert, Open resolution desk, Negotiate and History behave exactly as before.
Toggle: `http://localhost:5173/monitor?traffic=off` (none), `?traffic=100` (a balanced 100), default = all 394. Set a different default with `VITE_BACKGROUND_TRAFFIC` (`off`, a number, or `all`). If `frontend/public/traffic-pool.json` is missing the Monitor just looks as it did before. Regenerate it with `python scripts\export_traffic_pool.py` after `python scripts\fetch_tle_pool.py`.
Say: "The dots are real satellites on real orbits, there only so the sky looks like LEO. Aegis screens the 15 curated objects; the only conjunction you see is the one we staged."

## 7. Optional: Redis outage (rehearsed, ~45 s)
Start a deep storm (step 2), then:
```powershell
docker compose stop redis      # dot goes red, /cloud shows CLUSTER LINK DOWN, workers log "redis unavailable ... retrying"
Start-Sleep -Seconds 10
docker compose start redis     # within ~5 s everything is back; the storm resumes and finishes
```
Look at: Completed still reaches the full total, Failed = 0, Retries = 1-3 (leases that expired during the outage). Nothing restarts (`docker compose ps`).
Chaos figures for the 5 fault scenarios: `start results\chaos_recovery.png` (re-run: `python scripts\chaos.py`, ~35 min, stop the autoscaler first, FLUSHES Redis).

## 8. Chaos dock (Block 3 — optional add-on, ~3 min)

Start a fresh storm (step 2, 800 events), then scroll to the **Incident log** and **Fault injection** panels at the bottom of `/cloud`.

```powershell
# window B: start chaos_agent (read-only without --enable)
python scripts\chaos_agent.py --enable --api http://localhost:8010
```

The **Fault injection** panel shows `armed` in the status chip and lists the 5 scenarios.

1. Click **kill 2 of 4 workers** — two container IDs are killed; the worker table goes red within ~2 s.
2. The **Incident log** panel shows a `chaos_fault` event (red ✕) followed by `lease_expired` and `job_requeued` as the reaper catches up (~12 s).
3. Workers revive: `docker compose up -d --scale worker=4 worker` — a `chaos_recover` event (green ◎) appears.
4. Optionally click **hard redis kill** to demonstrate the `redis_down` / `redis_up` pair in the timeline.
5. The **three readouts** at the top of the incident log count total events, show the last event type, and the most common type.

The **Autoscaler** also posts `scale_up` / `scale_down` events to the timeline when started with `--api`:
```powershell
python scripts\autoscaler.py --api http://localhost:8010
```
Scale events appear in the incident log as `+` (blue) and `−` (amber) glyphs.

**Mock mode** (`http://localhost:5173/cloud?mock=chaos`): demonstrates the full chaos scenario (worker kills → redis outage → recovery) without Docker, running on scripted data. The incident log and chaos dock both render; scenario buttons are disabled in mock mode.

Verify the chaos gate:
```powershell
python scripts\check_chaos.py --quick     # < 1 min, checks 1-4
python scripts\check_chaos.py --full      # checks 1-7 including regression.py
```

## 9. FIFO vs priority race (Block 5, ~2 min)

Start the race lane workers (2 fifo + 2 prio) if not already running:
```powershell
docker compose --profile race up -d
```
Confirm: `docker compose ps` shows `worker_fifo` ×2 and `worker_prio` ×2.

Navigate to `/cloud` → **Race** panel → click **Start Race** (default: 300 events + 3 critical, seed 42).

Look at: both progress bars update live every 2 s (fifo NNN/303, priority NNN/303). After ~52 s the race finishes and both lane panels appear side by side.
- Priority CRIT RES AVG ~460–630 ms; FIFO CRIT RES AVG ~49–52 s
- Advantage badge: **91–104×** faster for critical events in priority lane
- Drain times: within 0–0.7% (same total work, different order)
- Named critical pairs: IRIDIUM 112 × STARLINK-30555 / SZ-21 MODULE / CONNECTA IOT-3; `lead_h`, `risk`, TCA gauge, SimClock

Say: "Same 303 jobs enqueued to both queues at the same Redis instant. Priority resolves the 3 high-risk conjunctions in under a second; FIFO waits for the whole queue first. Drain time is identical because the total work is the same."

Re-run: click **clear** then **Start Race**. Reset lane queues only: `Invoke-RestMethod -Method Post http://localhost:8010/api/cloud/race/reset`.

Verify the race gate:
```powershell
python scripts\check_race.py        # full: R1–R10 including regression, ~3 min
python scripts\check_race.py --quick  # R1–R5 only, ~1 min
```

**Honest caveats:**
- The ~300–700 ms priority resolution time is queue-wait-free processing, not response latency to a real satellite uplink.
- The SimClock "lead time consumed" gauge is display-only; it scales demo wall-clock ms by a user factor (×60 default) and has no effect on any real timer.
- DLQ / poison-pill handling and globe pulse on critical conjunctions are designed but not built (see PRESENT.md REAL vs SIMULATED table).

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
