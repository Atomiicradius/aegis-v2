import type { ReactNode } from 'react';

import { TONE_TEXT, type Tone } from './tone';

const RULE: Record<Tone, string> = {
  accent: 'border-accent',
  success: 'border-success',
  warning: 'border-warning',
  danger: 'border-danger',
  muted: 'border-border-light',
};

/** "Audit contract" callout: coloured left rule, tracked label, short bold statement. */
export function LeftRuleCallout({ tone, label, children }: { tone: Tone; label: string; children: ReactNode }) {
  return (
    <div className={`border-l-2 pl-4 ${RULE[tone]}`}>
      <p className={`font-mono text-[14px] uppercase tracking-[0.14em] ${TONE_TEXT[tone]}`}>{label}</p>
      <div className="mt-2 text-[15px] font-medium leading-6 text-text-primary">{children}</div>
    </div>
  );
}
