/**
 * SimClock — FIFO vs priority race simulated-time selector.
 * Multiplies REAL job latency for display only: lead-time consumption % and
 * TCA MISSED are computed from (real_ms * scale). Never writes to backend,
 * never feeds stats or storage.
 */

export type ClockScale = 1 | 60 | 300 | 600;
export const CLOCK_SCALES: ClockScale[] = [1, 60, 300, 600];
export const DEFAULT_SCALE: ClockScale = 60;

/** Scale a real millisecond value to simulated milliseconds. */
export function simMs(realMs: number, scale: ClockScale): number {
  return realMs * scale;
}

/** LEAD TIME CONSUMED %: simulated elapsed / lead window (clamped 0–100). */
export function leadConsumedPct(cumulativeBeforeMs: number, latencyMs: number, leadH: number, scale: ClockScale): number {
  const finishSim = simMs(cumulativeBeforeMs + latencyMs, scale);
  const leadMs = leadH * 3_600_000;
  return Math.min(100, Math.round((finishSim / leadMs) * 100));
}

/** Returns true when the simulated finish time exceeds the lead window. */
export function isTcaMissed(cumulativeBeforeMs: number, latencyMs: number, leadH: number, scale: ClockScale): boolean {
  return simMs(cumulativeBeforeMs + latencyMs, scale) > leadH * 3_600_000;
}

type Props = {
  value: ClockScale;
  onChange: (s: ClockScale) => void;
};

const LABEL: Record<ClockScale, string> = { 1: '×1', 60: '×60', 300: '×300', 600: '×600' };
const NOTE: Record<ClockScale, string> = {
  1:   'real time',
  60:  '1 s = 1 min',
  300: '1 s = 5 min',
  600: '1 s = 10 min',
};

export function SimClock({ value, onChange }: Props) {
  return (
    <div className="flex flex-wrap items-center gap-3 border border-border bg-surface-muted/75 px-4 py-3">
      <span className="font-mono text-[11px] uppercase tracking-[0.14em] text-text-muted shrink-0">
        Simulated clock
      </span>
      <div className="flex gap-1" role="group" aria-label="Simulated clock scale">
        {CLOCK_SCALES.map((s) => (
          <button
            key={s}
            type="button"
            onClick={() => onChange(s)}
            aria-pressed={s === value}
            className={`px-3 py-1 font-mono text-[13px] font-semibold uppercase tracking-[0.1em] transition-colors focus-visible:outline-2 focus-visible:outline-offset-1 focus-visible:outline-accent ${
              s === value
                ? 'border border-accent bg-accent/15 text-accent-light'
                : 'border border-border text-text-muted hover:border-border-light hover:text-text-secondary'
            }`}
          >
            {LABEL[s]}
          </button>
        ))}
      </div>
      <span className="font-mono text-[11px] uppercase tracking-[0.1em] text-accent-light shrink-0">
        {NOTE[value]}
      </span>
      <span className="ml-auto font-mono text-[10px] uppercase tracking-[0.09em] text-text-muted opacity-70">
        display only — does not affect stats or storage
      </span>
    </div>
  );
}
