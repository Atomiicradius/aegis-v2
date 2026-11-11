/**
 * IncidentTimeline — inline SVG timeline of system events, newest at the top.
 * Three readouts: total events seen, last event type, most common type.
 * Glyph+label markers per event type.
 */
import type { TimelineEvent } from '../../lib/cloudApi';
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

function mostCommon(events: TimelineEvent[]): string {
  if (events.length === 0) return '--';
  const freq: Record<string, number> = {};
  for (const e of events) freq[e.type] = (freq[e.type] ?? 0) + 1;
  return Object.entries(freq).sort((a, b) => b[1] - a[1])[0][0];
}

// ---- component --------------------------------------------------------------

const ROW_H = 36;
const LEFT = 56;
const DOT_R = 5;
const MAX_ROWS = 12;

interface Props {
  events: TimelineEvent[];
}

export function IncidentTimeline({ events }: Props) {
  const visible = events.slice(0, MAX_ROWS);
  const totalSeen = events.length;
  const lastType = events[0]?.type ?? '--';
  const mostType = mostCommon(events);

  const svgH = Math.max(ROW_H, visible.length * ROW_H);

  return (
    <section className="border border-border bg-surface-muted/75">
      <PanelHeader
        eyebrow="System timeline"
        title="Incident log"
        right={
          <span className="font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">
            {totalSeen} event{totalSeen !== 1 ? 's' : ''}
          </span>
        }
      />

      {/* Three readouts */}
      <dl className="grid grid-cols-3 divide-x divide-border border-b border-border">
        <Readout label="Total events" value={String(totalSeen)} />
        <Readout label="Last event" value={lastType} mono />
        <Readout label="Most common" value={mostType} mono />
      </dl>

      {/* Timeline SVG */}
      {events.length === 0 ? (
        <p className="px-5 py-8 font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">no events yet</p>
      ) : (
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
      )}
    </section>
  );
}

function Readout({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div className="px-5 py-4">
      <dt className="font-mono text-[13px] uppercase tracking-[0.14em] text-text-muted">{label}</dt>
      <dd className={`mt-1 truncate text-[clamp(1.1rem,1.6vw,1.5rem)] font-semibold tabular-nums text-text-primary ${mono ? 'font-mono' : ''}`}>
        {value}
      </dd>
    </div>
  );
}
