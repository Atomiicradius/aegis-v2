# UI Registry

A living catalogue of components as they get built, so later screens stay visually consistent with earlier ones. Update this after building each component — but don't let it slow you down; a quick note is enough, this isn't meant to be exhaustive documentation.

---

## How to Use

1. Before building a new component, skim this file for something similar already built.
2. If something similar exists, reuse its general approach (doesn't need to match exactly — use judgment).
3. Once you build something new, add a short entry below so the next component/session can reference it.

---

## Components

### Landing Page
*(operations briefing, live conjunction snapshot, telemetry rail, execution chain)*

File: `frontend/src/pages/Landing.tsx`
Last updated: 2026-09-12

| Property | Class / pattern |
|---|---|
| Background | `ops-grid bg-background`; `bg-surface-muted/80` only for operational side panels |
| Border | Square `border border-border` divisions; status accents use a narrow semantic border |
| Border radius | None — ops surfaces are rectilinear |
| Text — primary | Large condensed-feeling uppercase IBM Plex Sans, `tracking-[-0.055em]` |
| Text — secondary | `text-text-secondary`; telemetry labels use `font-mono text-[8px–10px] uppercase tracking-wider` |
| Spacing | Dense `px-6/8`, `py-3/5`; hero alone receives larger optical spacing |
| Hover/focus | `transition duration-200`, semantic border/text shift, visible `focus-visible:outline-accent` |
| Motion | One low-opacity `.scan-beam`; disabled by the global reduced-motion rule |
| Accent usage | Blue for active controls/provenance, red for hazard, amber for gated/pending, green for verified |

**Pattern notes:** The landing page is an operational briefing, not a marketing funnel. Do not restore centered gradient headlines, equal stat cards, numbered feature cards, pill badges, or a centered CTA banner. Metrics live in edge-to-edge telemetry rails; the seeded conjunction is presented as a live queue item.

### Shared Nav
*(top navigation bar, used across all pages)*
Built in `frontend/src/components/shared/NavBar.tsx` — 64px rectilinear `bg-surface` bar with `border-b border-border`; `AEGIS/OPS` monospace wordmark; uppercase 10px navigation; active route uses a thin accent underline; inactive links receive border/text hover and visible focus states. The far-right semantic status square reads the real Zustand WebSocket connection state.

### Globe Component
*(configurable — live view / conjunction highlight / before-after trajectory / ambient landing modes)*
Built in `frontend/src/components/globe/Globe.tsx` — one `react-globe.gl` instance, driven entirely by props (never rebuilt per screen, per architecture.md invariant 9). `GlobeStage.tsx` now owns the sole mounted instance above `<Routes>`; Landing, Monitor, and Trajectory select a presentation profile instead of mounting their own canvas. This is the ONLY globe implementation in the app.
- `trackedObjects: TrackedObject[]` (type in `globe/types.ts`, mirrors `schemas/tracked_object.py` field-for-field), `mode: 'live' | 'conjunction' | 'trajectory' | 'landing' | 'transition'`, optional `conjunctionAlert` (mirrors `schemas/conjunction.py`).
- `'landing'` mode (Landing.tsx's hero globe): same rendering/data/label-clustering pipeline as every other mode, just with ambient, non-interactive framing layered on top in `configureScene()` — `controls().autoRotate = true` (speed `0.28`), `enableZoom`/`enablePan` off, and a fixed initial `pointOfView({ lat: 20, lng: 10, altitude: 2.1 })`. Rings and orbit-trail arcs render in this mode too (same condition as `'live'`/`'conjunction'`). Landing.tsx sources `trackedObjects`/`conjunctionAlert` from `useNegotiationStore` exactly like `Monitor.tsx` (with the same `mockTrackedObjects.ts` fallback) — since `useAegisSocket()` is mounted once in `App.tsx` (D5), the same live connection and store state carry across a landing→monitor navigation with no reconnect.
- **Overlapping-label fix (2026-09-12):** physically docked/co-located objects (ISS complex, CSS cluster — see `backend/app/orchestrator/orchestrator.py`'s `DOCKED_OBJECT_GROUPS`, which this mirrors) propagate to identical screen positions, so per-object HUD labels used to stack illegibly. `Globe.tsx`'s `clusterHudPoints()` now runs three passes: (1) merge known docked groups from `globe/dockedGroups.ts` into one named marker (`"ISS COMPLEX (6)"`, `"CSS CLUSTER (5)"`) — keep this file in sync with the backend's group if the curated object set changes; (2) generic screen-space proximity clustering (≤18px) for any other incidental overlap; (3) a final de-collision pass that nudges markers vertically if two *different* clusters/singletons still end up screen-adjacent purely from the current camera angle (this is real and reproducible — e.g. ISS COMPLEX and CSS CLUSTER can visually approach each other depending on rotation, even though they're unrelated locations). `GlobeHud.tsx` renders the combined label with a `(<count>)` suffix and exposes the individual members via a native `title` hover tooltip on cluster markers only (`pointer-events: auto` scoped to `.globe-hud__marker--cluster`, everything else in the HUD overlay stays click-through).
- ECI `position_km` → lat/lng/alt conversion lives in `globe/eciToGeo.ts`, using `satellite.js`'s `eciToGeodetic`/`gstime` directly (no re-propagation needed — it just needs a position + timestamp).
- Auto-sizes to its parent via a `ResizeObserver` (`globe/Globe.tsx`) rather than taking pixel `width`/`height` props — wrap it in a sized container (Monitor uses `h-[calc(100vh-4rem)] w-full` for full-bleed).
- Colors: accent (`#3b82f6`) for normal points, danger (`#ef4444`) for anything matching `conjunctionAlert`'s `primary_id`/`secondary_id` — same semantic mapping as D3's `Badge`. In `'conjunction'` mode the flagged pair also gets a pulsing danger ring.
- `'trajectory'` mode is accepted but currently renders identically to `'live'` — before/after maneuver arcs depend on Dev A's Phase 2 propagation arrays, not yet available. Wiring the prop now means `Trajectory.tsx` can reuse this same instance later with no shape change.
- Globe texture (`earth-night.jpg`) is copied into `frontend/src/assets/` rather than imported from `three-globe`'s package directly — `three-globe`'s `exports` map blocks deep subpath imports in this version, and a local copy is also better for the offline demo path than the alternative unpkg CDN URL.
- Mock data: `globe/mockTrackedObjects.ts` has 5 hand-written `TrackedObject`s, physically real (each one's `position_km`/`velocity_kmps` came from actually running `satellite.js`'s SGP4 propagation against a real TLE at a fixed timestamp, not fabricated numbers). `TODO(Dev A)` comment marks where to swap in the real Phase 0 fixture or live data once available.
- **Vite config note:** `satellite.js` ships a WASM build using top-level await, which esbuild's default target can't pre-bundle. `vite.config.ts` now sets `build.target`/`optimizeDeps.esbuildOptions.target` to `'esnext'` to fix this — needed by any future code importing `satellite.js`, not just Globe.
- Wired into `pages/Monitor.tsx` as the central watch-floor viewport, surrounded by squared translucent tracking, conjunction, and frame telemetry panels. The globe remains the primary surface; operational overlays use `bg-background/88–90` plus subtle `backdrop-blur-sm`.
- **Hazard ring/marker recipe:** `ringColor` must be a function of the ring's progress `t` (`(t) => \`rgba(239, 68, 68, ${1 - t})\``), not a flat color string — otherwise every overlapping ring generation renders at full opacity instead of fading. Keep `ringRepeatPeriod` >= one ring's full lifetime (`ringMaxRadius / ringPropagationSpeed * 1000`, currently 3/2*1000=1500ms vs. a 1600ms repeat) so generations never overlap. Points render as `CylinderGeometry` pins — set `pointResolution={32}` (three-globe's default of 12 facets visibly at this scale). Skipping any of these three produces a jagged/scratchy hazard indicator instead of a clean pulsing ring.

**Scene enhancement recipe:** Keep one `Globe` and extend the `react-globe.gl` instance only through its public `scene()` and `postProcessingComposer()` methods. `Globe.tsx` installs a named Fresnel `ShaderMaterial` mesh, configures `UnrealBloomPass` on the existing composer, and removes its pass on unmount. Use local `earth-night.jpg` plus `earth-bump.png` for the globe colour/bump inputs. The sibling `GlobeHud.tsx` is a pointer-events-none CSS layer for projected marker labels only — it intentionally has no frame brackets, hairlines, or ECI/GEO reticle.

**Starfield recipe:** `createStarfield()` adds two deterministic Fibonacci-distributed `Points` layers to the same scene: a sparse cool field for depth and a much smaller warm field for visual hierarchy. It is intentionally behind the globe, depth-tested, and dim enough to stay below the bloom threshold. `.globe-star-dust` adds only five fixed CSS pinpricks as a static fallback texture, not a second animated star renderer.

**Twilight material recipe:** `MeshPhongMaterial` keeps the local night texture and bump map while using a restrained cool emissive fill (`#071a36`, `0.28`), low shininess, and a `0.72` bump scale. A directional key plus named ambient fill make land and relief legible. Keep Fresnel intensity near `0.52` and bloom strength below `1`; increase light/material first, not overall exposure, when the globe needs to read brighter.

**Persistent-stage recipe (2026-09-13):** `GlobeStageProvider` holds the public `GlobeHandle.pointOfView()` ref and wraps one absolute `motion.div` in `LayoutGroup`. Its `layoutId="globe-stage"` runs a direct Landing → Monitor handoff: 180ms Hero-copy exit, followed by one 820ms shared-layout/camera reframe while the landing offset eases to centre, a temporary low-contrast veil hides the WebGL resize, and Monitor chrome fades in at 420ms. Do not add an intermediate stage geometry: it forces a second canvas resize and looks like a snap. The Hero stage is document-positioned so it scrolls away with the landing section; the stage becomes pointer-interactive only in Monitor/Trajectory, while route shells stay click-through and their panels opt back in. During the non-interactive `'transition'` mode, projected labels are hidden so they cannot detach while the canvas offsets animate. Do not render `<Globe>` inside a route component.

### Monitor Watch Floor

File: `frontend/src/pages/Monitor.tsx`
Last updated: 2026-09-12

| Property | Class / pattern |
|---|---|
| Background | Full-viewport Globe + restrained navy directional overlay |
| Panels | `border border-border bg-background/88–90 backdrop-blur-sm` |
| Border radius | None |
| Text | 8–10px uppercase mono labels; compact sans titles; tabular mono values |
| Spacing | `px-4 py-3` rows, `p-4/6` outer shell |
| Interactive | Solid danger action for active conjunction; 200ms hover, focus outline, pressed shift |
| Status | Square glowing points; blue normal, red hazard, green live, amber reconnecting |

**Background traffic (display only):** `components/globe/BackgroundTraffic.ts` (hook) + `lib/useTrafficSelection.ts` (loader, `?traffic=off|<N>|all`, env `VITE_BACKGROUND_TRAFFIC`). ONE `THREE.Points` object in the globe scene: 394 real LEO satellites (static `public/traffic-pool.json`, made by `scripts/export_traffic_pool.py`) propagated client-side with satellite.js, `#74a9ff` 2.6 px points at 0.85 opacity, `sizeAttenuation: false`, no DOM labels, no hover. Drawn only on `/monitor` in the live/conjunction views; updates 4 slices x 250 ms (each satellite once per second), paused when the tab is hidden, drawn once under prefers-reduced-motion; every failure is swallowed. NOT in the register, HUD clustering, detection or alerts. Header strip (left cluster) gains one mono line: `tracked 15 / background traffic 394 / display only, not screened` (hidden when off or when the pool did not load; header stays 42 px tall at 1366 and 1920).

**Background traffic hover card (display only):** `components/globe/BackgroundHover.tsx` (behaviour), `BackgroundTrafficCard.tsx` (card), `SatSchematic.tsx` (4 hand-drawn inline-SVG line-art schematics ~120x70: starlink / oneweb / iridium / generic, token colours, no images), `pickNearest.ts` (pure screen-space pick), `lib/satStats.ts` (client-side SGP4 numbers), `lib/satArt.ts` (group -> drawing). Pointer pick throttled to 60 ms, nearest dot within 10 px that is not behind the Earth, paused while dragging/zooming; 80 ms in / 50 ms out. Highlight = one separate one-vertex ring; optional orbit ring = one LineLoop of 90 samples cached 30 s. Card: portal to body, `fixed`, `pointer-events-none`, `z-30`, kept below the 64 px nav, hairline border + blue `BACKGROUND TRAFFIC` eyebrow, amber left-rule footer "DISPLAY ONLY / NOT SCREENED FOR CONJUNCTIONS", no filled colour. `?hover=off|card|full` (default full). Hover events come only from the globe canvas, so the register and alert panels (above it) hide the card automatically.

**Pattern notes:** Keep the Globe visually dominant. Overlay only information needed to identify the tracked set, inspect the active conjunction, and enter the resolution flow. Do not use a rounded floating legend card.

### Conjunction Details
*(unified object/risk/control incident desk)*
Built in `frontend/src/pages/Negotiate.tsx`. The primary object, secondary object, and risk geometry occupy one contiguous squared grid rather than separate rounded cards. Values are monospace/tabular; unavailable operator fields say `not transmitted`. A compact control rail opens the existing negotiation socket. Status uses bordered rectangular flags, never pills. The honest empty state links back to Monitor.

### Negotiation Console
*(structured decision ledger)*
Built in `frontend/src/components/negotiation/NegotiationConsole.tsx` — semantic fixed-column table with event index, UTC, round, origin node, decision, deterministic yield score, and evidence/narration. Validation is a highlighted safety-gate row, not a centered chat bubble. The header exposes record/round/gate counts; the no-message state is an armed ledger rather than a hidden component.

### Negotiation Result
*(agreed plan card, rationale block, mini trajectory preview)*
Built in `frontend/src/components/negotiation/ResolutionCard.tsx` — squared semantic decision record with rationale on one side and a gapless numeric ledger on the other. Approved and no-safe-maneuver outcomes retain green/red status semantics without tinting the full component background. Links to the existing trajectory route.

### Trajectory Simulation
*(globe in before/after mode, timeline scrubber)*
← Agent fills this in when built

### History Table
*(filterable table of past conjunctions and outcomes)*
Built in `frontend/src/pages/History.tsx` — archive briefing + derived record counters + fixed-column conjunction ledger. Uses square status flags, tabular figures, object-pair provenance, before→after miss distance, and explicit skeleton/error/empty states. Filter control is a squared, labelled operational input with hover and focus-visible states.

### Cloud Page (/cloud)
*(ground-station control room for the Redis queue + worker cluster)*

Header strip also shows `objects / NNN` (zero-padded `pool_size` from the stats API; `--` when absent). The scope callout and footer state: real satellites and orbits, synthetic close approaches.

Files: `frontend/src/pages/Cloud.tsx`, `frontend/src/components/cloud/*`, `frontend/src/lib/{cloudApi,cloudMock,cloudMockRecords,useCloudStats,useCloudRecords}.ts`
Last updated: 2026-11-10

| Property | Class / pattern |
|---|---|
| Background | `ops-grid bg-background`; panels `border border-border bg-surface-muted/75`, square, no radius |
| Typography | Landing-style headline ("SCALE. PRIORITIZE. RECOVER.", last word `text-accent-light` + glow); every mono label is 14px (projector legibility), tracked uppercase; numerals `font-mono text-[clamp(...)]` zero-padded |
| Accent discipline | blue = normal flow, amber = critical/priority, red = dead/failed, green = healthy/online; always paired with a label or shape (diamond / square / X) |
| Motion | anime.js entry via `useAnimeStagger`/`useAnimeEntry`; scope animation is one rAF loop (~30 fps, pooled SVG elements, transform/opacity only), paused when the tab is hidden, static under reduced motion |

**Shared cloud components** (extracted because each appears 2+ times on the page): `StatusChip` (mono caps chip, tinted border + status light), `PanelHeader` (eyebrow above bold title), `StatCell` (big zero-padded numeral cell, 1 s border/glow flash via `flashOn`), `Sparkline` (inline SVG, 120 samples, min/max, dot on latest), `LeftRuleCallout` (coloured left rule + label + statement), `CornerBrackets` (panel corner brackets, `.cloud-brackets` in globals.css), `tone.ts` (Tone type + text colour map).

**ClusterConstellation** (the hero): queue = glowing core with depth numeral and a particle swirl that thickens with depth; 8 worker slots on 3 orbit rings (angles from a min-spacing search, rigid drift so labels never touch). Real-data mapping: throughput -> blue job-packet rate; alive workers -> lit nodes (newly lit slots boot in sequence, 260 ms apart, green boot ring); a rise in `critical_latency_ms.count` -> amber diamond packets crossing the swirl + amber core ring + "PRIORITY LANE +N"; a worker with `alive:false` -> red node, X, flickering broken ring, stays visible; a rise in `retries_total` -> dashed red square packet from the dead node to a live one (re-claim); a jump in queue depth -> inbound ingest ring. Documented limit: critical packets are inferred from the critical-count delta, not per-job events.

**Offline / mock:** link failure renders a History-style `border-l-2 border-danger` block ("CLUSTER LINK DOWN", what to check, auto-retry) and dims the scope. `/cloud?mock=idle|storm|scaled|critical|dead|down` feeds scripted stats through the same hook for QA; an amber "MOCK DATA" chip is shown and the mock code path never runs without the param.

**Resolution records** (`ResolutionRecords.tsx` + `RecordDetail.tsx`, data via `lib/useCloudRecords.ts`, mock rows in `lib/cloudMockRecords.ts`): History-ledger-style table (REC, OBJECT PAIR with real satellite names, MISS KM, RISK, ROUNDS, OUTCOME chip, ΔV, LATENCY, `open →`), 10 rows, newest first, polled every 2 s and frozen while the pointer/focus is inside the table. Critical rows = amber left rule + amber square + CRITICAL chip + amber risk/latency, never a filled row. Click opens `RecordDetail` below the table, which REUSES the Negotiate components `NegotiationConsole` (new optional prop `instant`: rows visible at once, no append animation) and `ResolutionCard` (new optional props `showTrajectoryLink={false}`, `residualAsDistance`: the engine's residual_risk is a km distance on approved resolutions). A `.cloud-legible` wrapper (globals.css) lifts their 8-12 px type to the page's 14 px floor without touching Negotiate. States: empty (left-rule callout), feed stale, loading, transcript expired (404, warning callout), unavailable (danger callout). Mock: `?mock=critical|dead` include critical rows and one pinned expired-transcript row.

**Control dock:** a sticky bottom bar (Storm 800 priority/fifo, Inject 3 critical, Reset with `window.confirm`), disabled while a request is in flight, last-action status line.

### About Page
*(static content, tech stack summary, roadmap)*
Built in `frontend/src/pages/About.tsx` — asymmetric system manifest with mission directive, four safety invariants, source/computation register, simulation-scope declaration, and phase roadmap. Uses the same border grid, telemetry typography, semantic status squares, and restrained scan motion as Landing.

---

## Shared Primitives

### Button
`frontend/src/components/shared/Button.tsx` — thin wrapper over `<button>` (spreads all native `ButtonHTMLAttributes`, so `onClick`/`type`/`disabled` etc. pass through untouched). `variant?: 'primary' | 'secondary'`, default `'primary'`.
- Base: `inline-flex items-center justify-center rounded-md px-4 py-2 text-sm font-medium transition duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent active:translate-y-px disabled:pointer-events-none disabled:opacity-50`
- Primary: `bg-accent text-accent-foreground hover:bg-accent-dark`
- Secondary/ghost: `border border-border bg-transparent text-text-secondary hover:border-border-light hover:text-text-primary`

### Card
`frontend/src/components/shared/Card.tsx` — thin wrapper over `<div>` (spreads native `HTMLAttributes<HTMLDivElement>`).
- Recipe: `bg-surface border border-border rounded-lg p-6`
- No colored backgrounds by design — introduce color via a `Badge`/status dot/small accent bar placed inside, never by tinting the card itself (the one hard visual rule in `ui-rules.md`).

### Badge
`frontend/src/components/shared/Badge.tsx` — thin wrapper over `<span>`. Required `status: 'active' | 'danger' | 'warning' | 'success'`.
- Base: `inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium`
- `active` → `bg-accent-muted text-accent-light`
- `danger` → `bg-danger-muted text-danger-light`
- `warning` → `bg-warning-muted text-warning-light`
- `success` → `bg-success-muted text-success-light`

All three accept `className` for one-off overrides and are otherwise deliberately unstyled beyond the above — build screen-specific layout (flex/grid, gaps, spacing) at the call site, not inside the primitive.

**Verified against the dark theme:** screenshotted at `/monitor` (Vite dev server + headless Chromium) — primary/secondary buttons, a card, and all four badge statuses render with correct token colors and no console errors.

---

## Data Layer (store / lib)

Not a visual component, but logged here per the usual pattern since every screen depends on it.

### WebSocket Store
`frontend/src/store/useNegotiationStore.ts` — the single Zustand store for all live state (per code-standards.md: one store, not split per feature). Holds `trackedObjects`, `activeConjunctionAlert`, `connectionStatus: 'connecting' | 'connected' | 'reconnecting' | 'disconnected'`. All WebSocket handling funnels through one `updateFromSocket(envelope)` action that switches on `envelope.type` (`snapshot` replaces `trackedObjects`/`activeConjunctionAlert` wholesale; `conjunction_alert` sets `activeConjunctionAlert`; `negotiation_message`/`resolution` are accepted but no-op until Phase 2 defines what they update; `error` logs). Also exports the `EventType`/`WebSocketEnvelope` types mirroring `schemas/websocket.py`.

### WebSocket Client
`frontend/src/lib/websocket.ts` — `useAegisSocket()` hook, named to match `architecture.md`'s Client Pattern snippet. Connects to Dev B's live `/ws/monitor`, mounted once in `App.tsx` (not per-page) so the connection and its status survive route changes — components read live state via the store, not by calling this hook themselves. Enforces: snapshot must be the first message per connection (anything else first is logged and dropped, not applied); messages with `sequence <= last seen` are discarded; on close, auto-reconnects forever at a fixed 2s interval with no retry cap, setting `connectionStatus` straight to `'reconnecting'` and holding it there for the whole outage — never `'disconnected'`, which is reserved for a possible future explicit/user-initiated disconnect and isn't reachable from the retry loop (demo-safety requirement: a backend blip during a live demo must never look like the connection gave up); every fresh connection resets local sequence-tracking state so a stale message from a dead socket can never be compared against the new one's sequence (architecture.md invariant 6 — never assume delta continuity, trust whatever snapshot arrives next).

**Consumers:** `NavBar.tsx`'s live-status pill now reads real `connectionStatus` (4-state color/label map: connecting=warning, connected=success "Live", reconnecting=warning+pulse, disconnected=danger) instead of being static. `Monitor.tsx` reads live `trackedObjects`/`activeConjunctionAlert`; falls back to D4's `mockTrackedObjects.ts` whenever the live array is empty (true today, since the backend snapshot doesn't carry tracked-object state yet) so the globe stays populated either way.

**Verified end-to-end:** with the real backend running, the globe correctly received B3's live `conjunction_alert` and switched to `'conjunction'` mode with a hazard ring on the matching mock satellites. Killing the backend mid-session showed `Reconnecting` (not a freeze); restarting it produced a fresh `Live` state with the alert re-applied from the new snapshot — confirmed via headless-Chromium screenshots at each stage.

**`useAegisSocket()` options (added for the Conjunction Details screen):** now takes an options object — `{ url?, enabled?, onEnvelope?, updateStore? }` — all optional, so the existing no-arg call in `App.tsx` is unaffected. `enabled` (default `true`) lets a connection be created on a user action instead of on mount — pass `false` until then, since hooks must still be called unconditionally on every render. `updateStore` (default `true`) controls whether this connection writes into the shared store at all; the one persistent `/ws/monitor` connection in `App.tsx` keeps `updateStore: true` (it owns `connectionStatus` for NavBar's pill), while a secondary connection — e.g. `Negotiate.tsx`'s per-negotiation socket to `/ws/negotiation/{id}` — passes `updateStore: false` so it can't cross-talk with the global store or make NavBar's status flicker based on an unrelated connection. `onEnvelope` receives every in-order, post-snapshot envelope regardless of `updateStore`; it's held in a `ref` internally so passing a fresh inline arrow function each render doesn't tear down and reconnect the socket. Also added `buildNegotiationWsUrl(conjunctionId)`, which derives the negotiation URL from wherever `VITE_WS_URL`/the hardcoded default points the monitor socket, so both routes share one source of truth for host/port.

---

## Patterns & Conventions

Pre-populated starting points from `ui-tokens.md` / `ui-rules.md` — refine as you go:

- **Page layout:** `ops-grid`, max-width ~1440px, square border grids, compact 16–32px page padding; globe screens may go full-bleed beneath overlays
- **Typography:** IBM Plex Sans for prose/headlines; IBM Plex Mono for navigation, labels, IDs, timestamps, statuses, and every number. Large display text is tight uppercase; operational labels are 8–10px with wide tracking.
- **Operations surfaces:** prefer contiguous `border-border` grids and `bg-surface-muted/70–90` over collections of rounded Cards. No card shadows. No full semantic-color backgrounds except narrow action controls.
- **Status flags:** rectangular borders + muted semantic tint; no pills. Status lights are 6–8px squares with restrained semantic glow.
- **Interaction:** 200ms transitions, visible `focus-visible` outline, 1px pressed translation. Respect the global reduced-motion media query.
- **Responsive:** design desktop-first for the demo (judges will view on a laptop/projector); a basic responsive pass is a nice-to-have, not a priority
- **Status color legend (consistent everywhere):** accent/blue = active, danger/red = debris/hazard, warning/amber = selected/pending, success/green = resolved

*(Add any custom classes or patterns that emerge across multiple components here as you build.)*
