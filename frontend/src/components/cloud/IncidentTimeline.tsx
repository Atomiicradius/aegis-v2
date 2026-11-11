/**
 * IncidentTimeline — inline SVG timeline of system events, newest at the top.
 * Seven readouts: total events, jobs lost, conjunctions unresolved, fault→first-retry,
 * fault→recovered, critical conjunctions affected, est. extra delay.
 * Throughput sparkline below markers. Tooltips on SVG markers (hover).
 */
import { useEffect, useState } from 'react';

import { fetchIntegrity, type TimelineEvent } from '../../lib/cloudApi';
import { PanelHeader } from './PanelHeader';

// ---- glyph config -----------------------------------------------------------
type GlyphSpec = { symbol: string; color: string };

const GLYPHS: Record<string, GlyphSpec> = {
  redis_down:        { symbol: '▼', color: 'var(--color-danger)' },
  redis_up:          { symbol: '▲', color: 'var(--color-success)' },
  chaos_fault:       { symbol: '✕', color: 'var(--color-danger)' },
  chaos_recover:     { symbol: '◎', color: 'var(--color-success)' },
  chaos_inject:      { symbol: '✕', color: 'var(--color-danger)' },
  scale_up:          { symbol: '+', color: 'var(--color-accent-light)' },
  scale_down:        { symbol: '−', color: 'var(--color-warning-light)' },
  storm_started:     { symbol: '⚡', color: 'var(--color-warning-light)' },
  reset:             { symbol: '↺', color: 'var(--color-text-muted)' },
  worker_started:    { symbol: '●', color: 'var(--color-success)' },
  worker_stopped:    { symbol: '○', color: 'var(--color-text-muted)' },
  lease_expired:     { symbol: '⏱', color: 'var(--color-warning)' },
  job_requeued:      { symbol: '↩', color: 'var(--color-accent-light)' },
  job_failed:        { symbol: '✗', color: 'var(--color-danger)' },
  chaos_redis_kill:  { symbol: '▼', color: 'var(--color-danger)' },
  chaos_worker_kill: { symbol: '✕', color: 'var(--color-danger)' },
};
const FALLBACK: GlyphSpec = { symbol: '◆', color: 'var(--color-text-muted)' };

function glyphFor(type: string): GlyphSpec {
  return GLYPHS[type] ?? FALLBACK;
}

function relTime(ts: number): string {
  const d = Date.now() / 1000 - ts;
  if (d < 60) return `${Math.round(d)}s ago`;
  if (d < 3600) return `${Math.round(d / 60)}m ago`;
  return `${Math.round(d / 3600)}h ago`;
}

const FAULT_TYPES = new Set(['chaos_fault', 'chaos_redis_kill', 'chaos_worker_kill', 'redis_down']);
const RETRY_TYPES = new Set(['lease_expired', 'job_requeued']);
const RECOVER_TYPES = new Set(['redis_up', 'chaos_recover']);
const CONJ_TYPES = new Set(['lease_expired', 'job_requeued', 'job_failed']);

interface RecoveryTimers {
  faultToFirstRetry: number | null;  // seconds
  faultToRecovered: number | null;   // seconds
}

function computeRecoveryTimers(events: TimelineEvent[]): RecoveryTimers {
  const asc = [...events].sort((a, b) => a.ts - b.ts);

  let lastFault: TimelineEvent | null = null;
  for (const ev of asc) {
    if (FAULT_TYPES.has(ev.type)) lastFault = ev;
  }
  if (!lastFault) return { faultToFirstRetry: null, faultToRecovered: null };

  const faultTs = lastFault.ts;
  let firstRetry: number | null = null;
  let firstRecovery: number | null = null;

  for (const ev of asc) {
    if (ev.ts <= faultTs) continue;
    if (firstRetry === null && RETRY_TYPES.has(ev.type)) firstRetry = ev.ts - faultTs;
    if (firstRecovery === null && RECOVER_TYPES.has(ev.type)) firstRecovery = ev.ts - faultTs;
    if (firstRetry !== null && firstRecovery !== null) break;
  }

  return { faultToFirstRetry: firstRetry, faultToRecovered: firstRecovery };
}

function fmtTimer(val: number | null): string {
  if (val === null) return '--';
  return `${Math.round(val)}s`;
}

function autoscalerActive(events: TimelineEvent[]): boolean {
  const cutoff = Date.now() / 1000 - 120;
  return events.some((e) => (e.type === 'scale_up' || e.type === 'scale_down') && e.ts >= cutoff);
}

/** Count conjunction-related events (lease_expired, job_requeued, job_failed) in the stream. */
function countCriticalAffected(events: TimelineEvent[]): number {
  return events.filter((e) => CONJ_TYPES.has(e.type) && e.detail?.is_critical === true).length;
}

/** Estimate extra delay: each lease_expired adds ~LEASE_SECONDS of delay (default 30 s). */
function estExtraDelay(events: TimelineEvent[]): number | null {
  const expired = events.filter((e) => e.type === 'lease_expired').length;
  if (expired === 0) return null;
  return expired * 30; // 30 s = default LEASE_SECONDS
}

// ---- sparkline --------------------------------------------------------------
const SPARK_H = 36;
const SPARK_PAD = 4;

function ThroughputSparkline({ data }: { data: number[] }) {
  if (data.length < 2) return null;
  const max = Math.max(...data, 0.1);
  const w = 640;
  const step = w / (data.length - 1);
  const pts = data
    .map((v, i) => `${i * step},${SPARK_H - SPARK_PAD - ((v / max) * (SPARK_H - SPARK_PAD * 2))}`)
    .join(' ');

  return (
    <div className="border-t border-border px-2 pb-1">
      <p className="px-1 pb-0.5 font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted">throughput</p>
      <svg width="100%" height={SPARK_H} viewBox={`0 0 ${w} ${SPARK_H}`} preserveAspectRatio="none" aria-label="Throughput sparkline">
        <polyline
          points={pts}
          fill="none"
          stroke="var(--color-accent-light)"
          strokeWidth={1.5}
          strokeLinejoin="round"
        />
      </svg>
    </div>
  );
}

// ---- tooltip ----------------------------------------------------------------

interface TooltipState { x: number; y: number; ev: TimelineEvent }

function MarkerTooltip({ tip }: { tip: TooltipState }) {
  const { ev } = tip;
  const pair = typeof ev.detail?.pair === 'string' ? ev.detail.pair as string : null;
  const risk = typeof ev.detail?.risk === 'number' ? (ev.detail.risk as number).toFixed(3) : null;
  const isCrit = ev.detail?.is_critical === true;
  return (
    <foreignObject x={Math.min(tip.x, 420)} y={Math.max(tip.y - 70, 0)} width={200} height={80} style={{ overflow: 'visible' }}>
      <div
        className="border border-border bg-surface px-2 py-1.5 shadow-lg"
        style={{ fontSize: 11, fontFamily: 'var(--font-mono)', color: 'var(--color-text-primary)', lineHeight: 1.5, whiteSpace: 'nowrap' }}
      >
        <div style={{ color: 'var(--color-text-muted)', textTransform: 'uppercase', letterSpacing: '0.1em' }}>{ev.type}</div>
        {pair && <div>{pair}</div>}
        {risk && <div>RISK {risk}{isCrit && <span style={{ color: 'var(--color-warning-light)', marginLeft: 6 }}>CRITICAL</span>}</div>}
        <div style={{ color: 'var(--color-text-muted)' }}>{relTime(ev.ts)}</div>
      </div>
    </foreignObject>
  );
}

// ---- component --------------------------------------------------------------

const ROW_H = 36;
const LEFT = 56;
const DOT_R = 5;
const MAX_ROWS = 12;

interface Props {
  events: TimelineEvent[];
  throughputHistory?: number[];
}

export function IncidentTimeline({ events, throughputHistory }: Props) {
  const [leaked, setLeaked] = useState<number | null>(null);
  const [tip, setTip] = useState<TooltipState | null>(null);

  useEffect(() => {
    let cancelled = false;
    const ac = new AbortController();

    async function poll() {
      if (cancelled) return;
      const result = await fetchIntegrity(ac.signal);
      if (!cancelled && result.ok) setLeaked(result.data.leaked);
    }

    void poll();
    const id = setInterval(() => void poll(), 5000);
    return () => {
      cancelled = true;
      ac.abort();
      clearInterval(id);
    };
  }, []);

  const visible = events.slice(0, MAX_ROWS);
  const totalSeen = events.length;
  const { faultToFirstRetry, faultToRecovered } = computeRecoveryTimers(events);
  const scaler = autoscalerActive(events);
  const critAffected = countCriticalAffected(events);
  const extraDelay = estExtraDelay(events);

  const svgH = Math.max(ROW_H, visible.length * ROW_H);

  const jobsLostVal = leaked === null ? '--' : String(leaked);
  const jobsLostColor = leaked !== null && leaked > 0 ? 'text-danger-light' : 'text-text-primary';

  return (
    <section className="border border-border bg-surface-muted/75">
      <PanelHeader
        eyebrow="System timeline"
        title="Incident log"
        right={
          <div className="flex items-center gap-3">
            {scaler && (
              <span className="font-mono text-[12px] uppercase tracking-[0.12em] text-accent-light">
                autoscaler active
              </span>
            )}
            <span className="font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">
              {totalSeen} event{totalSeen !== 1 ? 's' : ''}
            </span>
          </div>
        }
      />

      {/* Seven readouts */}
      <dl className="grid grid-cols-4 divide-x divide-border border-b border-border sm:grid-cols-7">
        <Readout label="Total events" value={String(totalSeen)} />
        <div className="px-3 py-4">
          <dt className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted leading-tight">Jobs lost</dt>
          <dd className={`mt-1 truncate text-[clamp(0.95rem,1.3vw,1.3rem)] font-semibold tabular-nums font-mono ${jobsLostColor}`}>
            {jobsLostVal}
          </dd>
          <dd className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-text-muted">
            0 conj. unresolved
          </dd>
        </div>
        <Readout label="Fault to retry" value={fmtTimer(faultToFirstRetry)} mono />
        <Readout label="Fault recovered" value={fmtTimer(faultToRecovered)} mono />
        <Readout label="Last event" value={events[0]?.type ?? '--'} mono />
        <Readout label="Critical affected" value={critAffected > 0 ? String(critAffected) : '--'} valueColor={critAffected > 0 ? 'text-warning-light' : undefined} />
        <Readout label="Est. extra delay" value={extraDelay != null ? `${extraDelay}s` : '--'} mono note="estimate" />
      </dl>

      {/* Timeline SVG */}
      {events.length === 0 ? (
        <p className="px-5 py-8 font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">no events yet</p>
      ) : (
        <>
          <div className="overflow-hidden px-2 py-2">
            <svg
              width="100%"
              height={svgH}
              viewBox={`0 0 640 ${svgH}`}
              preserveAspectRatio="xMinYMin meet"
              aria-label="Incident timeline"
              onMouseLeave={() => setTip(null)}
            >
              {/* Vertical axis line */}
              <line
                x1={LEFT}
                y1={ROW_H / 2}
                x2={LEFT}
                y2={svgH - ROW_H / 2}
                stroke="var(--color-border)"
                strokeWidth={1}
              />

              {visible.map((ev, i) => {
                const cy = i * ROW_H + ROW_H / 2;
                const { symbol, color } = glyphFor(ev.type);
                return (
                  <g
                    key={`${ev.seq}-${i}`}
                    onMouseEnter={(e) => {
                      const svg = (e.currentTarget as SVGElement).closest('svg');
                      if (!svg) return;
                      const rect = svg.getBoundingClientRect();
                      const vw = 640;
                      const vh = svgH;
                      const sx = ((e.clientX - rect.left) / rect.width) * vw;
                      const sy = ((e.clientY - rect.top) / rect.height) * vh;
                      setTip({ x: sx, y: sy, ev });
                    }}
                    style={{ cursor: 'default' }}
                  >
                    {/* Connector tick */}
                    <line
                      x1={LEFT}
                      y1={cy}
                      x2={LEFT + 18}
                      y2={cy}
                      stroke="var(--color-border-light)"
                      strokeWidth={1}
                    />
                    {/* Dot */}
                    <circle cx={LEFT} cy={cy} r={DOT_R} fill={color} />
                    {/* Glyph */}
                    <text
                      x={LEFT - 14}
                      y={cy + 4}
                      textAnchor="middle"
                      fontSize={11}
                      fill={color}
                      fontFamily="var(--font-mono)"
                    >
                      {symbol}
                    </text>
                    {/* Type label */}
                    <text
                      x={LEFT + 24}
                      y={cy + 4}
                      fontSize={12}
                      fill="var(--color-text-primary)"
                      fontFamily="var(--font-mono)"
                    >
                      {ev.type}
                    </text>
                    {/* Pair from detail (if present) */}
                    {typeof ev.detail?.pair === 'string' && (
                      <text
                        x={LEFT + 24}
                        y={cy + 15}
                        fontSize={10}
                        fill="var(--color-text-muted)"
                        fontFamily="var(--font-mono)"
                      >
                        {(ev.detail.pair as string).slice(0, 36)}
                      </text>
                    )}
                    {/* Source */}
                    <text
                      x={340}
                      y={cy + 4}
                      fontSize={11}
                      fill="var(--color-text-muted)"
                      fontFamily="var(--font-mono)"
                    >
                      {ev.source}
                    </text>
                    {/* Relative time */}
                    <text
                      x={630}
                      y={cy + 4}
                      textAnchor="end"
                      fontSize={11}
                      fill="var(--color-text-muted)"
                      fontFamily="var(--font-mono)"
                    >
                      {relTime(ev.ts)}
                    </text>
                  </g>
                );
              })}

              {/* Tooltip overlay */}
              {tip && <MarkerTooltip tip={tip} />}
            </svg>
            {events.length > MAX_ROWS && (
              <p className="px-3 pb-2 font-mono text-[12px] uppercase tracking-[0.1em] text-text-muted">
                +{events.length - MAX_ROWS} older events
              </p>
            )}
          </div>

          {/* Throughput sparkline */}
          {throughputHistory && throughputHistory.length >= 2 && (
            <ThroughputSparkline data={throughputHistory} />
          )}
        </>
      )}
    </section>
  );
}

function Readout({ label, value, mono, valueColor, note }: { label: string; value: string; mono?: boolean; valueColor?: string; note?: string }) {
  return (
    <div className="px-3 py-4">
      <dt className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted leading-tight">{label}</dt>
      <dd className={`mt-1 truncate text-[clamp(0.95rem,1.3vw,1.3rem)] font-semibold tabular-nums ${valueColor ?? 'text-text-primary'} ${mono ? 'font-mono' : ''}`}>
        {value}
      </dd>
      {note && <dd className="mt-0.5 font-mono text-[10px] uppercase tracking-[0.1em] text-text-muted">{note}</dd>}
    </div>
  );
}
