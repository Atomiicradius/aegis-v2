type Tone = 'accent-light' | 'success' | 'warning-light';

type Props = {
  label: string;
  data: number[];
  tone: Tone;
  current: string;
  format?: (n: number) => string;
};

const STROKE: Record<Tone, string> = { 'accent-light': 'stroke-accent-light', success: 'stroke-success', 'warning-light': 'stroke-warning-light' };
const FILL: Record<Tone, string> = { 'accent-light': 'fill-accent-light/10', success: 'fill-success/10', 'warning-light': 'fill-warning-light/10' };
const DOT: Record<Tone, string> = {
  'accent-light': 'bg-accent-light shadow-[0_0_10px_var(--color-accent-light)]',
  success: 'bg-success shadow-[0_0_10px_var(--color-success)]',
  'warning-light': 'bg-warning-light shadow-[0_0_10px_var(--color-warning-light)]',
};

const W = 400;
const H = 96;
const SAMPLES = 120;

/** Inline-SVG trace of the last <=120 samples: baseline, min/max readout, dot on the latest value. */
export function Sparkline({ label, data, tone, current, format = (n) => String(Math.round(n * 10) / 10) }: Props) {
  const max = Math.max(1, ...data);
  const min = data.length ? Math.min(...data) : 0;
  const step = W / (SAMPLES - 1);
  // newest sample is pinned to the right edge; history grows leftwards
  const xs = data.map((_, i) => W - (data.length - 1 - i) * step);
  const ys = data.map((v) => H - 6 - (v / max) * (H - 14));
  const line = data.map((_, i) => `${xs[i].toFixed(1)},${ys[i].toFixed(1)}`).join(' ');
  const lastY = data.length ? (ys[ys.length - 1] / H) * 100 : 100;

  return (
    <div className="border border-border bg-surface-muted/75 px-5 py-4">
      <div className="flex items-baseline justify-between gap-3">
        <p className="font-mono text-[14px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
        <p className="font-mono text-2xl font-semibold tabular-nums text-text-primary">{current}</p>
      </div>
      <div className="relative mt-3">
        <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" className="h-24 w-full overflow-visible" aria-hidden="true">
          <line x1="0" y1={H - 6} x2={W} y2={H - 6} className="stroke-border-light" strokeWidth="1" vectorEffect="non-scaling-stroke" />
          <line x1="0" y1="8" x2={W} y2="8" className="stroke-border" strokeWidth="1" strokeDasharray="2 6" vectorEffect="non-scaling-stroke" />
          {data.length > 1 && (
            <>
              <polygon points={`${xs[0].toFixed(1)},${H - 6} ${line} ${W},${H - 6}`} className={FILL[tone]} />
              <polyline points={line} fill="none" className={STROKE[tone]} strokeWidth="2" vectorEffect="non-scaling-stroke" strokeLinejoin="round" />
            </>
          )}
        </svg>
        {data.length > 0 && (
          <span aria-hidden="true" className={`absolute right-0 h-2.5 w-2.5 -translate-y-1/2 translate-x-1/2 ${DOT[tone]}`} style={{ top: `${lastY}%` }} />
        )}
      </div>
      <div className="mt-2 flex justify-between font-mono text-[14px] uppercase tracking-[0.1em] text-text-muted">
        <span>min {format(min)}</span>
        <span>{data.length}s</span>
        <span>max {format(data.length ? Math.max(...data) : 0)}</span>
      </div>
    </div>
  );
}
