# PRESENT.md — Aegis demo guide

## What Aegis is

Aegis is a multi-agent orbital collision de-confliction system. When two tracked satellites are on a close-approach trajectory, Aegis treats the event as a job: it scores the risk (how tight the miss distance, how soon the TCA, how mission-critical each operator), assigns it a queue priority, and dispatches it to a worker that runs a two-agent negotiation. Operator A and Operator B each compute a yield score from their satellite's fuel margin, mission priority index, and maneuverability; a validation agent re-propagates the post-maneuver trajectory and screens for secondary conjunctions within a 6-hour lookahead; the first round that passes validation is accepted and a resolution record is written. All of this happens on real LEO satellite names and orbits from CelesTrak TLEs — the close approaches themselves are synthetic load, not real conjunctions.

---

## REAL vs SIMULATED

| Element | Status |
|---|---|
| Satellite names and NORAD IDs (Starlink, OneWeb, Iridium NEXT, …) | **REAL** — from CelesTrak TLEs |
| Orbital positions and velocities | **REAL** — SGP4 propagation |
| Close-approach events (miss distance, TCA) | **SIMULATED** — generated synthetically for load |
| Risk score (closeness × urgency × mission priority) | **COMPUTED** — deterministic heuristic, not a true collision probability |
| Negotiation transcript and agent justifications | **TEMPLATE** — `LLM_MODE=stub`; real Anthropic API calls are disabled in the demo |
| Priority queue ordering and queue-wait latency | **REAL** — measured wall-clock, real Redis queue, real workers |
| Chaos / fault recovery (worker kill, Redis outage) | **REAL** — Docker kill on live containers |
| FIFO vs priority race (Block 5) | **REAL** — two namespaced Redis queues, real Docker workers |
| SimClock "lead time consumed" gauge | **SIMULATED DISPLAY** — scales wall-clock ms by a user-selected factor (×1 / ×60 / ×300 / ×600); illustrative only |
| Background traffic globe dots | **REAL** orbits, **display only** — not screened, not in the queue |
| DLQ / poison-pill handling | **DESIGNED, NOT BUILT** — `dead_lettered` outcome reserved in schema |
| Globe pulse on critical conjunction | **DESIGNED, NOT BUILT** — Block 4 work |

---

## 5-minute demo script

All commands are PowerShell from the repo root. The React app is at `http://localhost:5173/cloud`.

### Before you start (2 min)
```powershell
Set-Content .env "LEASE_SECONDS=10"
docker compose down
docker compose up -d redis api
docker compose up -d --scale worker=2 worker
Invoke-RestMethod -Method Post http://localhost:8010/api/cloud/reset
start http://localhost:5173/cloud
```
Check: green dot, Live workers = 2, queue empty.

### Step 1 — Storm and autoscaler (90 s)
Click **Storm 800 (priority)** in the UI, then start the autoscaler:
```powershell
python scripts\autoscaler.py
```
> "800 events land in the queue in ~2 s. With 2 workers, throughput is ~6 jobs/s — that's a 2-minute drain. The autoscaler reads queue depth and scales to 8 workers; throughput jumps to ~21 jobs/s and the queue empties in ~36 s."

Point at: queue-depth spike → Live workers tile stepping 2 → 8 → sparkline → queue empties.

### Step 2 — Critical injection (30 s)
While a fresh storm is running: click **Inject 3 critical**.
> "These 3 high-risk conjunctions jumped the entire queue. CRITICAL LATENCY is ~500 ms while the 800 normal events average 20+ seconds."

Point at: amber CRITICAL LATENCY tile, resolution-records table with the amber CRITICAL chip.

Click **open →** on a critical row: show the named satellite pair, negotiation transcript, yield scores, validation result, delta-v.

### Step 3 — Kill a worker (45 s)
Stop the autoscaler (Ctrl+C), reset, start 4 workers + storm:
```powershell
Invoke-RestMethod -Method Post http://localhost:8010/api/cloud/reset
docker compose up -d --scale worker=4 worker
Invoke-RestMethod -Method Post -Uri http://localhost:8010/api/cloud/storm -ContentType 'application/json' -Body '{"n":800,"critical":0,"policy":"priority"}'
Start-Sleep -Seconds 6
docker kill (docker compose ps -q --status running worker | Select-Object -Last 1)
```
> "One container is gone. Watch the incident timeline: at +10 s the worker row goes red and DEAD; at +12 s RETRIES=1 — the lease expired, a surviving worker picked up the abandoned job. Completed still reaches 800, failed stays 0."

### Step 4 — Two-story trace (20 s)
In the resolution-records table, click **open →** on any row with the **RETRIED ×2** badge.
> "This job ran twice: attempt 1 shows LEASE EXPIRED in the waterfall; attempt 2 shows the full NEG + VAL segments and outcome COMPLETED. The first result written wins; the duplicate is discarded."

### Step 5 — FIFO vs priority race (90 s)
Start the race workers if not already running:
```powershell
docker compose --profile race up -d
```
Navigate to the **Race** panel. Click **Start Race**.
> "303 events — 300 normal + 3 critical — are enqueued to both lanes simultaneously in one atomic Redis pipeline. Same jobs, different ordering policies. Watch the progress bars."

After ~52 s: show the finished result.
> "Priority resolved all 3 critical conjunctions in under 700 ms wall-clock. FIFO took 50 seconds — the critical jobs sat behind the entire queue. The advantage badge shows 91–104× depending on the run. Drain time is within 1% — identical total work, different order."

---

## Honest talking points

- **The latency benefit is about queue depth and policy, not raw speed.** Priority takes the same ~320 ms to process a critical job as FIFO does. The gap (50 s vs 0.5 s) is entirely queue-wait time under a 300-job load.
- **The SimClock is an illustration.** The "lead time consumed" gauge scales wall-clock milliseconds by a user-chosen factor (×60 default) and displays how much of a satellite's TCA lead time would be consumed if the demo timestamps were real orbital ones. It has no input from real clocks, no effect on any worker or queue decision.
- **Risk is a heuristic, not a collision probability.** `0.5×closeness + 0.3×urgency + 0.2×mission` is a scoring function tuned for the demo. We have no covariance data and do not use a Foster's-method Pc calculation.
- **Storm events are synthetic.** The satellite names are real (CelesTrak TLEs, real NORAD IDs). The miss distances and TCAs are invented for load generation. No event in a demo storm represents a real conjunction.
- **Throughput scales near-linearly because the stub sleeps.** Real LLM negotiation runs at ~2 ms/job CPU; a real deployment would need a bigger lease and a different throughput profile.
