import type { Tone } from './tone';

const CHIP: Record<Tone, string> = {
  accent: 'border-accent/40 bg-accent-muted/40 text-accent-light',
  success: 'border-success/30 bg-success-muted/60 text-success-light',
  warning: 'border-warning/35 bg-warning-muted/60 text-warning-light',
  danger: 'border-danger/35 bg-danger-muted/60 text-danger-light',
  muted: 'border-border-light bg-surface-secondary/50 text-text-secondary',
};

const DOT: Record<Tone, string> = {
  accent: 'bg-accent shadow-[0_0_8px_var(--color-accent)]',
  success: 'bg-success shadow-[0_0_8px_var(--color-success)]',
  warning: 'bg-warning shadow-[0_0_8px_var(--color-warning)]',
  danger: 'bg-danger shadow-[0_0_8px_var(--color-danger)]',
  muted: 'bg-text-muted',
};

/** Landing-style status chip: mono caps, tinted border, status light. Shape + label, never colour alone. */
export function StatusChip({ tone, label, pulse = false }: { tone: Tone; label: string; pulse?: boolean }) {
  return (
    <span className={`inline-flex items-center gap-2 border px-2.5 py-1 font-mono text-[13px] uppercase tracking-[0.14em] ${CHIP[tone]}`}>
      <span aria-hidden="true" className={`h-2 w-2 ${DOT[tone]} ${pulse ? 'animate-pulse' : ''}`} />
      {label}
    </span>
  );
}
