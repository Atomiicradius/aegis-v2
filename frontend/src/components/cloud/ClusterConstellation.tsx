import { useEffect, useMemo, useRef, useState } from 'react';

import type { CloudStats } from '../../lib/cloudApi';
import { ScopeBase } from './ScopeBase';
import {
  C, CORE_R, GATE_R, MAX_SLOTS, VB, clamp01, easeInOut, easeOut, lerpPt, nodePos, polar, type Pt,
} from './scopeGeometry';

/**
 * CLUSTER SCOPE: the hero visualisation of /cloud.
 *   core        = the event queue (depth numeral + a swirl of queued-job particles)
 *   8 slots     = worker satellites on 3 orbit rings; the autoscaler lights them one by one
 *   blue dots   = jobs flowing core -> worker (rate follows throughput)
 *   green ticks = resolutions leaving the worker
 *   amber diamond with long trail = a CRITICAL job cutting through the queue
 *   red node + broken ring + X = dead worker (stays visible); square packet = its job re-claimed
 *
 * Performance: the SVG is rendered by React once per 1 Hz poll. Everything that
 * moves is a pooled element mutated (transform/opacity only) by one requestAnimationFrame
 * loop throttled to ~30 fps, paused while the tab is hidden, and replaced by a single
 * static frame under prefers-reduced-motion.
 */

const N_QDOTS = 80;
const N_JOBS = 14;
const N_OUT = 10;
const N_CRIT = 4;
const N_RECLAIM = 2;
const GLOW_MS = 700;

type SlotState = 'off' | 'alive' | 'dead';
type SlotView = { state: SlotState };

type Props = { stats: CloudStats | null; linkOk: boolean };

type Packet = { active: boolean; t0: number; dur: number; slot: number; from: Pt };
type QDot = { a: number; w: number; rho: number; op: number };

const newPackets = (n: number): Packet[] =>
  Array.from({ length: n }, () => ({ active: false, t0: 0, dur: 1, slot: 0, from: { x: C, y: C } }));

/** Workers keep their slot for as long as they are registered (dead ones included). */
function assignSlots(stats: CloudStats | null, map: Map<string, number>): SlotView[] {
  const views: SlotView[] = Array.from({ length: MAX_SLOTS }, () => ({ state: 'off' as SlotState }));
  if (!stats) return views;
  const ids = new Set(stats.workers.map((w) => w.id));
  for (const id of [...map.keys()]) if (!ids.has(id)) map.delete(id);
  for (const w of stats.workers) {
    if (!map.has(w.id)) {
      const used = new Set(map.values());
      const free = views.findIndex((_, i) => !used.has(i));
      if (free >= 0) map.set(w.id, free);
    }
    const slot = map.get(w.id);
    if (slot !== undefined) views[slot] = { state: w.alive ? 'alive' : 'dead' };
  }
  return views;
}

const unitAngle = (p: Pt) => Math.atan2(p.y - C, p.x - C);
const pad = (n: number, w: number) => String(Math.max(0, Math.round(n))).padStart(w, '0');

/** Position a pooled packet group: all children but the last are trail lines, the last is the head. */
function place(g: SVGGElement, tail: Pt, head: Pt, opacity: number): void {
  const kids = g.children;
  for (let i = 0; i < kids.length - 1; i += 1) {
    const line = kids[i];
    line.setAttribute('x1', tail.x.toFixed(1));
    line.setAttribute('y1', tail.y.toFixed(1));
    line.setAttribute('x2', head.x.toFixed(1));
    line.setAttribute('y2', head.y.toFixed(1));
  }
  kids[kids.length - 1].setAttribute('transform', `translate(${head.x.toFixed(1)} ${head.y.toFixed(1)})`);
  g.setAttribute('opacity', opacity.toFixed(2));
  g.style.display = '';
}

export function ClusterConstellation({ stats, linkOk }: Props) {
  const slotMap = useRef(new Map<string, number>());
  const slots = useMemo(() => assignSlots(stats, slotMap.current), [stats]);

  /* ---- boot animation: newly lit slots reveal in sequence (260 ms apart) ---- */
  const [boot, setBoot] = useState(() => Array.from({ length: MAX_SLOTS }, () => ({ key: 0, delay: 0 })));
  const prevStates = useRef<SlotState[]>(slots.map(() => 'off' as SlotState));
  useEffect(() => {
    const fresh = slots.flatMap((s, i) => (s.state === 'alive' && prevStates.current[i] !== 'alive' ? [i] : []));
    prevStates.current = slots.map((s) => s.state);
    if (fresh.length === 0) return;
    setBoot((b) => b.map((x, i) => (fresh.includes(i) ? { key: x.key + 1, delay: fresh.indexOf(i) * 260 } : x)));
  }, [slots]);

  /* ---- latest data for the animation loop (no re-render per frame) ---- */
  const data = useRef({ depth: 0, throughput: 0, alive: [] as number[], dead: [] as number[], linkOk: true });
  data.current = {
    depth: stats?.queue_depth ?? 0,
    throughput: stats?.throughput_per_s ?? 0,
    alive: slots.flatMap((s, i) => (s.state === 'alive' ? [i] : [])),
    dead: slots.flatMap((s, i) => (s.state === 'dead' ? [i] : [])),
    linkOk,
  };

  /* ---- events derived from stat deltas: ingest burst, critical cut-ahead, re-claim ---- */
  const events = useRef({ ingestAt: -1e9, critPending: 0, reclaimPending: 0, glowAt: -1e9 });
  const prev = useRef<{ depth: number; retries: number; crit: number } | null>(null);
  const [critBanner, setCritBanner] = useState(0);
  useEffect(() => {
    if (!stats) return;
    const before = prev.current;
    prev.current = { depth: stats.queue_depth, retries: stats.retries_total, crit: stats.critical_latency_ms.count };
    if (!before) return;
    if (stats.queue_depth - before.depth > 30) events.current.ingestAt = performance.now();
    const dCrit = stats.critical_latency_ms.count - before.crit;
    if (dCrit > 0) {
      events.current.critPending += Math.min(dCrit, 3);
      setCritBanner(Math.min(dCrit, 99));
    }
    const dRetry = stats.retries_total - before.retries;
    if (dRetry > 0) events.current.reclaimPending += Math.min(dRetry, 2);
  }, [stats]);
  useEffect(() => {
    if (critBanner === 0) return;
    const t = window.setTimeout(() => setCritBanner(0), 2800);
    return () => window.clearTimeout(t);
  }, [critBanner]);

  /* ---- pooled animated elements ---- */
  const nodeEls = useRef<(SVGGElement | null)[]>([]);
  const pulseEls = useRef<(SVGCircleElement | null)[]>([]);
  const dotEls = useRef<(SVGCircleElement | null)[]>([]);
  const jobEls = useRef<(SVGGElement | null)[]>([]);
  const outEls = useRef<(SVGGElement | null)[]>([]);
  const critEls = useRef<(SVGGElement | null)[]>([]);
  const reclaimEls = useRef<(SVGGElement | null)[]>([]);
  const haloEl = useRef<SVGCircleElement>(null);
  const ingestEl = useRef<SVGCircleElement>(null);
  const glowEl = useRef<SVGCircleElement>(null);

  useEffect(() => {
    const reduced = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    const jobs = newPackets(N_JOBS);
    const outs = newPackets(N_OUT);
    const crits = newPackets(N_CRIT);
    const reclaims = newPackets(N_RECLAIM);
    const dots: QDot[] = Array.from({ length: N_QDOTS }, (_, j) => ({
      a: j * 137.5,                                   // golden angle spread
      w: (j % 2 ? 1 : -1) * (14 + (j % 7) * 3),       // deg/s, both directions
      rho: 82 + ((j * 53) % 58),
      op: 0,
    }));
    const pulses = new Float64Array(MAX_SLOTS).fill(-1e9);
    let haloR = 78;
    let spawnAcc = 0;
    let rr = 0;
    const free = (pool: Packet[]) => pool.find((p) => !p.active);
    const rand = (a: number, b: number) => a + Math.random() * (b - a);

    const spawnOut = (slot: number, T: number, now: number) => {
      const p = free(outs);
      if (!p) return;
      Object.assign(p, { active: true, t0: now, dur: 520, slot, from: nodePos(slot, T) });
    };

    const step = (T: number, now: number, dt: number, snap: boolean) => {
      const d = data.current;
      const ev = events.current;

      for (let i = 0; i < MAX_SLOTS; i += 1) {
        const el = nodeEls.current[i];
        if (el) {
          const p = nodePos(i, T);
          el.setAttribute('transform', `translate(${p.x.toFixed(1)} ${p.y.toFixed(1)})`);
        }
        const pulse = pulseEls.current[i];
        if (pulse) {
          const age = now - pulses[i];
          pulse.setAttribute('opacity', age < 700 ? (0.9 * (1 - age / 700)).toFixed(2) : '0');
          pulse.setAttribute('r', (24 + clamp01(age / 700) * 28).toFixed(1));
        }
      }

      // queue swirl: more particles as depth grows
      const want = d.depth <= 0 ? 0 : Math.min(N_QDOTS, 6 + Math.round(74 * Math.min(1, d.depth / 500)));
      for (let j = 0; j < N_QDOTS; j += 1) {
        const q = dots[j];
        const target = j < want ? 1 : 0;
        q.op = snap ? target : q.op + (target - q.op) * Math.min(1, dt * 3);
        q.a += q.w * dt;
        const el = dotEls.current[j];
        if (!el) continue;
        const p = polar(q.rho + haloR - 78, q.a);
        el.setAttribute('cx', p.x.toFixed(1));
        el.setAttribute('cy', p.y.toFixed(1));
        el.setAttribute('opacity', (q.op * 0.85).toFixed(2));
      }
      haloR += (74 + 18 * Math.min(1, d.depth / 800) - haloR) * Math.min(1, dt * 2);
      haloEl.current?.setAttribute('r', haloR.toFixed(1));

      if (reduced) return;                                // static frame: no packets

      // blue job packets: rate follows throughput
      if (d.alive.length && d.throughput > 0 && d.linkOk) {
        spawnAcc += Math.max(0.6, Math.min(12, d.throughput * 0.45)) * dt;
        while (spawnAcc >= 1) {
          spawnAcc -= 1;
          const p = free(jobs);
          if (!p) break;
          const slot = d.alive[rr++ % d.alive.length];
          const np = nodePos(slot, T);
          const ang = unitAngle(np);
          Object.assign(p, {
            active: true, t0: now, dur: rand(900, 1400), slot,
            from: { x: C + Math.cos(ang) * haloR, y: C + Math.sin(ang) * haloR },
          });
        }
      }
      // critical cut-ahead: from the far gate straight through the queue to a worker
      while (ev.critPending > 0 && d.alive.length) {
        const p = free(crits);
        if (!p) break;
        ev.critPending -= 1;
        const slot = d.alive[Math.floor(Math.random() * d.alive.length)];
        const ang = (unitAngle(nodePos(slot, T)) * 180) / Math.PI + 180;
        Object.assign(p, { active: true, t0: now + (N_CRIT - 1 - ev.critPending) * 120, dur: 720, slot, from: polar(GATE_R, ang) });
        ev.glowAt = now + 360;
      }
      ev.critPending = d.alive.length ? ev.critPending : 0;
      // re-claim: dead node -> live node
      while (ev.reclaimPending > 0) {
        const p = free(reclaims);
        if (!p || !d.dead.length || !d.alive.length) { ev.reclaimPending = 0; break; }
        ev.reclaimPending -= 1;
        Object.assign(p, { active: true, t0: now, dur: 1400, slot: d.alive[rr++ % d.alive.length], from: { x: d.dead[0], y: 0 } });
      }

      for (const p of jobs) {
        const g = jobEls.current[jobs.indexOf(p)];
        if (!g || !p.active) { if (g) g.style.display = 'none'; continue; }
        const u = (now - p.t0) / p.dur;
        if (u >= 1) { p.active = false; pulses[p.slot] = now; spawnOut(p.slot, T, now); g.style.display = 'none'; continue; }
        const to = nodePos(p.slot, T);
        place(g, lerpPt(p.from, to, easeOut(Math.max(0, u - 0.2))), lerpPt(p.from, to, easeOut(u)), Math.min(1, u * 6));
      }
      for (const p of outs) {
        const g = outEls.current[outs.indexOf(p)];
        if (!g || !p.active) { if (g) g.style.display = 'none'; continue; }
        const u = (now - p.t0) / p.dur;
        if (u >= 1) { p.active = false; g.style.display = 'none'; continue; }
        const ang = (unitAngle(p.from) * 180) / Math.PI;
        const to = polar(GATE_R, ang);
        place(g, lerpPt(p.from, to, Math.max(0, u - 0.35)), lerpPt(p.from, to, u), 1 - u * 0.7);
      }
      for (const p of crits) {
        const g = critEls.current[crits.indexOf(p)];
        if (!g || !p.active) { if (g) g.style.display = 'none'; continue; }
        const u = (now - p.t0) / p.dur;
        if (u < 0) { g.style.display = 'none'; continue; }
        if (u >= 1) { p.active = false; pulses[p.slot] = now; spawnOut(p.slot, T, now); g.style.display = 'none'; continue; }
        const to = nodePos(p.slot, T);
        place(g, lerpPt(p.from, to, easeInOut(Math.max(0, u - 0.34))), lerpPt(p.from, to, easeInOut(u)), 1);
      }
      for (const p of reclaims) {
        const g = reclaimEls.current[reclaims.indexOf(p)];
        if (!g || !p.active) { if (g) g.style.display = 'none'; continue; }
        const u = (now - p.t0) / p.dur;
        if (u >= 1) { p.active = false; pulses[p.slot] = now; g.style.display = 'none'; continue; }
        const a = nodePos(p.from.x, T);               // from.x carries the dead slot index
        const b = nodePos(p.slot, T);
        const mid = { x: (a.x + b.x) / 2 + (C - (a.x + b.x) / 2) * 0.6, y: (a.y + b.y) / 2 + (C - (a.y + b.y) / 2) * 0.6 };
        const bez = (t: number): Pt => ({
          x: (1 - t) ** 2 * a.x + 2 * (1 - t) * t * mid.x + t * t * b.x,
          y: (1 - t) ** 2 * a.y + 2 * (1 - t) * t * mid.y + t * t * b.y,
        });
        place(g, bez(Math.max(0, u - 0.2)), bez(easeInOut(u)), 1);
      }

      // ingest ring (queue just jumped) and core flash (critical passing through)
      const ia = (now - ev.ingestAt) / 800;
      if (ingestEl.current) {
        ingestEl.current.setAttribute('opacity', ia >= 0 && ia < 1 ? (0.7 * (1 - ia)).toFixed(2) : '0');
        ingestEl.current.setAttribute('r', (GATE_R - clamp01(ia) * (GATE_R - 120)).toFixed(1));
      }
      const ga = (now - ev.glowAt) / GLOW_MS;
      if (glowEl.current) {
        glowEl.current.setAttribute('opacity', ga >= 0 && ga < 1 ? (0.95 * (1 - ga)).toFixed(2) : '0');
        glowEl.current.setAttribute('r', (haloR + clamp01(ga) * 36).toFixed(1));
      }
    };

    if (reduced) {
      step(0, performance.now(), 0.016, true);
      return;
    }
    let raf = 0;
    let frame = 0;
    const t0 = performance.now();
    let last = t0;
    const loop = (now: number) => {
      raf = requestAnimationFrame(loop);
      frame += 1;
      if (frame % 2 || document.hidden) return;          // ~30 fps, nothing while hidden
      const dt = Math.min(0.1, (now - last) / 1000);
      last = now;
      step((now - t0) / 1000, now, dt, false);
    };
    raf = requestAnimationFrame(loop);
    return () => cancelAnimationFrame(raf);
  }, []);

  const alive = data.current.alive.length;
  const dead = data.current.dead.length;
  const depth = stats?.queue_depth ?? 0;

  return (
    <svg
      viewBox={`0 0 ${VB} ${VB}`}
      className="h-full w-full"
      role="img"
      aria-label={`Cluster scope: ${alive} workers online, ${dead} dead, queue depth ${depth}`}
    >
      <ScopeBase />

      <g className={linkOk ? '' : 'opacity-40'}>
        {/* ingest ring + core */}
        <circle ref={ingestEl} cx={C} cy={C} r={GATE_R} fill="none" className="stroke-accent-light" strokeWidth="2" opacity="0" />
        <circle cx={C} cy={C} r="160" fill="url(#cloud-core-glow)" />
        <circle ref={haloEl} cx={C} cy={C} r="78" fill="none" className="stroke-accent-light/45" strokeWidth="1.5" strokeDasharray="3 6" />
        <circle ref={glowEl} cx={C} cy={C} r="78" fill="none" className="stroke-warning" strokeWidth="3" opacity="0" />
        {Array.from({ length: N_QDOTS }, (_, j) => (
          <circle key={j} ref={(el) => { dotEls.current[j] = el; }} cx={C} cy={C} r={j % 4 === 0 ? 4 : 2.8} className="fill-accent-light" opacity="0" />
        ))}
        <circle cx={C} cy={C} r={CORE_R} className="fill-surface-muted stroke-accent-light" strokeWidth="2" />
        <circle cx={C} cy={C} r={CORE_R - 8} fill="none" className="stroke-accent/35" strokeWidth="1" />
        <text x={C} y={C + 12} textAnchor="middle" className="fill-text-primary font-mono font-semibold" fontSize={depth >= 10000 ? 34 : 42}>
          {pad(depth, 4)}
        </text>
        <text x={C} y={C + 34} textAnchor="middle" className="fill-text-muted font-mono" fontSize="14" letterSpacing="4">
          QUEUE
        </text>

        {/* worker satellites */}
        {slots.map((slot, i) => (
          <g key={i} ref={(el) => { nodeEls.current[i] = el; }} transform={`translate(${nodePos(i, 0).x.toFixed(1)} ${nodePos(i, 0).y.toFixed(1)})`}>
            <WorkerNode
              index={i}
              state={slot.state}
              bootKey={boot[i].key}
              bootDelay={boot[i].delay}
              pulseRef={(el) => { pulseEls.current[i] = el; }}
            />
          </g>
        ))}

        {/* packets (pooled) */}
        {Array.from({ length: N_JOBS }, (_, k) => (
          <g key={`j${k}`} ref={(el) => { jobEls.current[k] = el; }} style={{ display: 'none' }}>
            <line className="stroke-accent-light" strokeWidth="2" strokeLinecap="round" opacity="0.7" />
            <circle r="4.5" className="fill-accent-light" />
          </g>
        ))}
        {Array.from({ length: N_OUT }, (_, k) => (
          <g key={`o${k}`} ref={(el) => { outEls.current[k] = el; }} style={{ display: 'none' }}>
            <line className="stroke-success" strokeWidth="1.5" strokeLinecap="round" opacity="0.6" />
            <rect x="-3.5" y="-3.5" width="7" height="7" className="fill-success" />
          </g>
        ))}
        {Array.from({ length: N_CRIT }, (_, k) => (
          <g key={`c${k}`} ref={(el) => { critEls.current[k] = el; }} style={{ display: 'none' }}>
            <line className="stroke-warning" strokeWidth="9" strokeLinecap="round" opacity="0.22" />
            <line className="stroke-warning-light" strokeWidth="3" strokeLinecap="round" />
            <polygon points="0,-10 10,0 0,10 -10,0" className="fill-warning-light stroke-warning" strokeWidth="1.5" />
          </g>
        ))}
        {Array.from({ length: N_RECLAIM }, (_, k) => (
          <g key={`r${k}`} ref={(el) => { reclaimEls.current[k] = el; }} style={{ display: 'none' }}>
            <line className="stroke-danger-light" strokeWidth="2" strokeDasharray="4 5" />
            <rect x="-5" y="-5" width="10" height="10" className="fill-none stroke-danger-light" strokeWidth="2" />
          </g>
        ))}
      </g>

      {/* HUD corners */}
      <g className="font-mono" fontSize="18" letterSpacing="3" stroke="#0a0e17" strokeWidth="6" paintOrder="stroke" strokeLinejoin="round">
        <text x="30" y="52" className="fill-text-muted">WORKERS <tspan className="fill-text-primary">{pad(alive, 2)}/{pad(MAX_SLOTS, 2)}</tspan></text>
        <text x="30" y={VB - 32} className="fill-text-muted">FLOW <tspan className="fill-accent-light">{(stats?.throughput_per_s ?? 0).toFixed(1)}</tspan> JOB/S</text>
        <text x={VB - 30} y={VB - 32} textAnchor="end" className="fill-text-muted">DEAD <tspan className={dead > 0 ? 'fill-danger-light' : 'fill-text-primary'}>{pad(dead, 2)}</tspan></text>
        <text x={VB - 30} y="52" textAnchor="end" className="fill-text-muted">POLICY <tspan className="fill-text-primary">{(stats?.policy ?? '--').toUpperCase()}</tspan></text>
        {critBanner > 0 && (
          <text x={VB - 30} y="80" textAnchor="end" className="fill-warning-light" fontWeight="600">
            {'◆'} PRIORITY LANE +{critBanner} CUT AHEAD
          </text>
        )}
        {!linkOk && (
          <text x={C} y={C + 175} textAnchor="middle" className="fill-danger-light cloud-flicker" fontWeight="600">NO SIGNAL</text>
        )}
      </g>
    </svg>
  );
}

type NodeProps = {
  index: number;
  state: SlotState;
  bootKey: number;
  bootDelay: number;
  pulseRef: (el: SVGCircleElement | null) => void;
};

function WorkerNode({ index, state, bootKey, bootDelay, pulseRef }: NodeProps) {
  const label = `W${pad(index + 1, 2)}`;

  if (state === 'off') {
    return (
      <g>
        <circle r="18" fill="none" className="stroke-text-muted/50" strokeWidth="1" strokeDasharray="2 5" />
        <path d="M-5 0H5M0 -5V5" className="stroke-text-muted/50" strokeWidth="1" />
        <text y="44" textAnchor="middle" className="fill-text-muted/60 font-mono" fontSize="19" letterSpacing="2">{label}</text>
      </g>
    );
  }

  if (state === 'dead') {
    return (
      <g>
        <circle r="26" fill="none" className="stroke-danger cloud-flicker" strokeWidth="2" strokeDasharray="7 11" />
        <path d="M0 -12L12 0L0 12L-12 0Z" className="fill-surface-muted stroke-danger-light" strokeWidth="2" />
        <path d="M-6 -6L6 6M6 -6L-6 6" className="stroke-danger-light" strokeWidth="2.5" strokeLinecap="round" />
        <text y="48" textAnchor="middle" className="fill-danger-light font-mono" fontSize="19" letterSpacing="2" fontWeight="600">{label}</text>
        <text y="68" textAnchor="middle" className="fill-danger-light font-mono cloud-flicker" fontSize="17" letterSpacing="3">DEAD</text>
      </g>
    );
  }

  return (
    <g>
      {/* empty-slot marker stays under the node so the slot is visible until its boot animation starts */}
      <circle r="18" fill="none" className="stroke-text-muted/50" strokeWidth="1" strokeDasharray="2 5" />
    <g
      key={bootKey}
      className="cloud-node-boot"
      style={{ animationDelay: `${bootDelay}ms`, opacity: bootKey === 0 ? 0 : undefined }}
    >
      <circle ref={pulseRef} r="24" fill="none" className="stroke-accent-light" strokeWidth="2" opacity="0" />
      <circle r="24" fill="none" className="cloud-boot-ring stroke-success" strokeWidth="2" style={{ animationDelay: `${bootDelay}ms` }} />
      <circle r="24" fill="none" className="stroke-accent/60" strokeWidth="1.5" />
      <path d="M0 -12L12 0L0 12L-12 0Z" className="fill-surface-muted stroke-accent-light" strokeWidth="2" />
      <circle r="3" className="fill-accent-light" />
      <rect x="13" y="-19" width="7" height="7" className="fill-success" />
      <text y="48" textAnchor="middle" className="fill-text-primary font-mono" fontSize="19" letterSpacing="2" fontWeight="600">{label}</text>
    </g>
    </g>
  );
}
