/**
 * IncidentTimeline — inline SVG timeline of system events, newest at the top.
 * Five readouts: total events, jobs lost (from /integrity), fault->first-retry,
 * fault->recovered, autoscaler active hint. Throughput sparkline below markers.
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

interface RecoveryTimers {
  faultToFirstRetry: number | null;  // seconds
  faultToRecovered: number | null;   // seconds
}

function computeRecoveryTimers(events: TimelineEvent[]): RecoveryTimers {
  const asc = [...events].sort((a, b) => a.ts - b.ts);

  // Find the last fault event
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

      {/* Five readouts */}
      <dl className="grid grid-cols-5 divide-x divide-border border-b border-border">
        <Readout label="Total events" value={String(totalSeen)} />
        <Readout label="Jobs lost" value={jobsLostVal} valueColor={jobsLostColor} />
        <Readout label="Fault to retry" value={fmtTimer(faultToFirstRetry)} mono />
        <Readout label="Fault to recovered" value={fmtTimer(faultToRecovered)} mono />
        <Readout label="Last event" value={events[0]?.type ?? '--'} mono />
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
                  <g key={`${ev.seq}-${i}`}>
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

function Readout({ label, value, mono, valueColor }: { label: string; value: string; mono?: boolean; valueColor?: string }) {
  return (
    <div className="px-3 py-4">
      <dt className="font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted leading-tight">{label}</dt>
      <dd className={`mt-1 truncate text-[clamp(0.95rem,1.3vw,1.3rem)] font-semibold tabular-nums ${valueColor ?? 'text-text-primary'} ${mono ? 'font-mono' : ''}`}>
        {value}
      </dd>
    </div>
  );
}
