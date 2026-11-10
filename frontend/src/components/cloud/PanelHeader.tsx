import type { ReactNode } from 'react';

import { TONE_TEXT, type Tone } from './tone';

/** Panel header pattern from Monitor: small tracked eyebrow above a bold title, optional right-hand slot. */
export function PanelHeader({ eyebrow, title, tone = 'accent', right }: { eyebrow: string; title: string; tone?: Tone; right?: ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-4 border-b border-border px-5 py-4">
      <div>
        <p className={`font-mono text-[14px] uppercase tracking-[0.18em] ${TONE_TEXT[tone]}`}>{eyebrow}</p>
        <h2 className="mt-1 text-lg font-semibold text-text-primary">{title}</h2>
      </div>
      {right}
    </div>
  );
}
