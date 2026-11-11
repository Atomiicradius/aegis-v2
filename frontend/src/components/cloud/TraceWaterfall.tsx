/**
 * TraceWaterfall — per-attempt timing chart for a finished job.
 *
 * One bar per attempt.  For attempts that completed normally the bar spans the
 * full process_end_ms and is labelled "NEGOTIATE + VALIDATE".  A thin tick
 * marks where negotiation ended (negotiate_ms), making the split visible
 * without implying that the VALIDATE phase has its own independent timer
 * (it is derived as process_end_ms − negotiate_ms).
 *
 * Failure markers (LEASE EXPIRED, ERROR, DUPLICATE DISCARDED) are driven by
 * the attempt's outcome field, not by whether process_end_ms is zero.
 *
 * All colours are CSS custom properties sourced from globals.css (ui-tokens.md).
 * The chart is pure inline SVG — no new npm dependencies.
 *
 * A RETRIED ×N badge shows when attempts.length > 1.
 */
import type { CloudTrace, TraceAttempt } from '../../lib/cloudApi';

// ── SVG geometry ──────────────────────────────────────────────────────────────
const ROW_H = 30;
const LABEL_W = 116;        // left column: "ATT N  <worker>"
const OUTCOME_W = 68;       // outcome chip lives inside LABEL_W
const AXIS_H = 22;
const PAD = { top: 8, right: 20, bottom: 4 };
const VIEW_W = 340;         // SVG viewBox width (scales to container)
const CHART_W = VIEW_W - LABEL_W - PAD.right;

/** Outcomes that mean the attempt never ran to completion. */
const FAILED_OUTCOMES = new Set(['lease_expired', 'error', 'duplicate_discarded', 'dead_lettered', 'pending']);

function failedLabel(outcome: string): string {
  if (outcome === 'lease_expired') return 'LEASE EXPIRED';
  if (outcome === 'error') return 'ERROR';
  if (outcome === 'duplicate_discarded') return 'DUPLICATE';
  if (outcome === 'dead_lettered') return 'DEAD-LETTERED';
  if (outcome === 'pending') return 'PENDING';
  return outcome.replace(/_/g, ' ').toUpperCase();
}

type Props = { trace: CloudTrace };

export function TraceWaterfall({ trace }: Props) {
  const { attempts } = trace;
  const n = attempts.length;
  const retried = n > 1;

  // Scale x-axis by the max completed process_end_ms (ignore failed rows for scale)
  const maxMs = Math.max(
    ...attempts.map((a) => (!FAILED_OUTCOMES.has(a.outcome) ? a.process_end_ms : 0)),
    1,
  );

  const svgH = PAD.top + n * ROW_H + (n - 1) * 6 + AXIS_H + PAD.bottom;

  return (
    <div className="mt-3">
      {/* header */}
      <div className="mb-2 flex flex-wrap items-center gap-3">
        <span className="font-mono text-[11px] uppercase tracking-[0.18em] text-text-muted">
          Execution trace
        </span>
        {retried && (
          <span
            style={{
              background: 'var(--color-danger-muted)',
              color: 'var(--color-danger)',
              border: '1px solid var(--color-danger)',
              fontSize: 10,
              fontFamily: 'var(--font-mono)',
              letterSpacing: '0.14em',
              padding: '1px 7px',
              textTransform: 'uppercase',
            }}
          >
            Retried ×{n - 1}
          </span>
        )}
      </div>

      {/* waterfall */}
      <svg
        width="100%"
        viewBox={`0 0 ${VIEW_W} ${svgH}`}
        preserveAspectRatio="xMinYMin meet"
        style={{ overflow: 'visible', display: 'block', maxWidth: 680 }}
        aria-label={`Execution waterfall: ${n} attempt${n !== 1 ? 's' : ''}`}
      >
        {attempts.map((att, idx) => (
          <AttemptRow
            key={att.n}
            attempt={att}
            y={PAD.top + idx * (ROW_H + 6)}
            maxMs={maxMs}
          />
        ))}
        <XAxis y={PAD.top + n * (ROW_H + 6) - 6 + 4} maxMs={maxMs} />
      </svg>

      {/* legend */}
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
        <LegendDot color="var(--color-accent)" fill="var(--color-accent-muted)" label="NEGOTIATE + VALIDATE" />
        <LegendDot color="var(--color-border-light)" fill="none" label="negotiate boundary" dashed />
        <LegendDot color="var(--color-danger)" fill="var(--color-danger-muted)" label="failed attempt" />
      </div>
    </div>
  );
}

// ── AttemptRow ─────────────────────────────────────────────────────────────────

function AttemptRow({ attempt, y, maxMs }: { attempt: TraceAttempt; y: number; maxMs: number }) {
  const { n, worker_id, outcome, process_end_ms, negotiate_ms } = attempt;
  const failed = FAILED_OUTCOMES.has(outcome);
  const midY = y + ROW_H / 2;
  const short = worker_id.slice(0, 8);
  const barX = LABEL_W;

  // px widths
  const totalPx = !failed ? (process_end_ms / maxMs) * CHART_W : 0;
  const negPx = !failed ? (negotiate_ms / maxMs) * CHART_W : 0;

  // per-outcome styling
  const outcomeColor = outcome === 'completed'
    ? 'var(--color-success)'
    : failed
    ? 'var(--color-danger)'
    : 'var(--color-warning)';

  const outcomeBg = outcome === 'completed'
    ? 'var(--color-success-muted)'
    : failed
    ? 'var(--color-danger-muted)'
    : 'var(--color-warning-muted)';

  const chipText = outcome === 'completed' ? 'DONE'
    : failedLabel(outcome).slice(0, 7);

  return (
    <g>
      {/* ATT N */}
      <text x={0} y={midY + 4} style={{ fill: 'var(--color-text-secondary)', fontSize: 9, fontFamily: 'var(--font-mono)', letterSpacing: '0.12em' }}>
        {`ATT ${n}`}
      </text>
      {/* worker id */}
      <text x={36} y={midY + 4} style={{ fill: 'var(--color-text-muted)', fontSize: 9, fontFamily: 'var(--font-mono)' }}>
        {short}
      </text>
      {/* outcome chip */}
      <rect
        x={LABEL_W - OUTCOME_W - 4}
        y={y + 7}
        width={OUTCOME_W}
        height={16}
        style={{ fill: outcomeBg }}
        rx={1}
      />
      <text
        x={LABEL_W - OUTCOME_W / 2 - 4}
        y={y + 18}
        textAnchor="middle"
        style={{ fill: outcomeColor, fontSize: 8, fontFamily: 'var(--font-mono)', letterSpacing: '0.1em' }}
      >
        {chipText}
      </text>

      {/* bar background */}
      <rect
        x={barX}
        y={y + 5}
        width={CHART_W}
        height={ROW_H - 10}
        style={{ fill: 'var(--color-surface-secondary)' }}
        rx={1}
      />

      {!failed ? (
        <>
          {/* NEGOTIATE + VALIDATE single bar */}
          <rect
            x={barX}
            y={y + 5}
            width={Math.max(0, totalPx)}
            height={ROW_H - 10}
            style={{ fill: 'var(--color-accent-muted)', stroke: 'var(--color-accent)', strokeWidth: 0.8 }}
            rx={1}
          />
          {/* negotiate boundary tick */}
          {negPx > 2 && negPx < totalPx - 2 && (
            <line
              x1={barX + negPx}
              y1={y + 5}
              x2={barX + negPx}
              y2={y + ROW_H - 5}
              style={{ stroke: 'var(--color-border-light)', strokeWidth: 1.2, strokeDasharray: '2 2' }}
            />
          )}
          {/* bar label: show when bar is wide enough */}
          {totalPx >= 60 && (
            <text
              x={barX + totalPx / 2}
              y={midY + 3}
              textAnchor="middle"
              style={{ fill: 'var(--color-accent-light)', fontSize: 8, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em' }}
            >
              NEG + VAL
            </text>
          )}
          {/* duration */}
          <text
            x={barX + totalPx + 4}
            y={midY + 3}
            style={{ fill: 'var(--color-text-muted)', fontSize: 9, fontFamily: 'var(--font-mono)' }}
          >
            {`${process_end_ms.toFixed(1)}ms`}
          </text>
        </>
      ) : (
        /* failed attempt: dashed bar + outcome label */
        <>
          <rect
            x={barX}
            y={y + 5}
            width={44}
            height={ROW_H - 10}
            style={{
              fill: 'var(--color-danger-muted)',
              stroke: 'var(--color-danger)',
              strokeWidth: 0.8,
              strokeDasharray: '3 2',
            }}
            rx={1}
          />
          <text
            x={barX + 52}
            y={midY + 3}
            style={{ fill: 'var(--color-text-muted)', fontSize: 8, fontFamily: 'var(--font-mono)', letterSpacing: '0.08em' }}
          >
            {`${failedLabel(outcome)} · ${short}…`}
          </text>
        </>
      )}
    </g>
  );
}

// ── XAxis ─────────────────────────────────────────────────────────────────────

function XAxis({ y, maxMs }: { y: number; maxMs: number }) {
  const ticks = 4;
  return (
    <g>
      <line
        x1={LABEL_W} y1={y} x2={LABEL_W + CHART_W} y2={y}
        style={{ stroke: 'var(--color-border)', strokeWidth: 0.8 }}
      />
      {Array.from({ length: ticks + 1 }, (_, i) => {
        const frac = i / ticks;
        const x = LABEL_W + frac * CHART_W;
        const ms = Math.round(frac * maxMs);
        return (
          <g key={i}>
            <line x1={x} y1={y} x2={x} y2={y + 4} style={{ stroke: 'var(--color-border)', strokeWidth: 0.8 }} />
            <text
              x={x}
              y={y + AXIS_H - 4}
              textAnchor="middle"
              style={{ fill: 'var(--color-text-muted)', fontSize: 8, fontFamily: 'var(--font-mono)' }}
            >
              {`${ms}ms`}
            </text>
          </g>
        );
      })}
    </g>
  );
}

// ── LegendDot ─────────────────────────────────────────────────────────────────

function LegendDot({ color, fill, label, dashed }: { color: string; fill: string; label: string; dashed?: boolean }) {
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
      <svg width={10} height={10} viewBox="0 0 10 10">
        <rect
          x={0} y={0} width={10} height={10}
          rx={1}
          style={{
            fill: fill === 'none' ? 'transparent' : fill,
            stroke: color,
            strokeWidth: 1,
            strokeDasharray: dashed ? '2 1' : 'none',
          }}
        />
      </svg>
      <span style={{ color: 'var(--color-text-muted)', fontSize: 9, fontFamily: 'var(--font-mono)', letterSpacing: '0.12em', textTransform: 'uppercase' }}>
        {label}
      </span>
    </span>
  );
}
