import { useEffect, useRef, useState } from 'react';

import { TONE_TEXT, type Tone } from './tone';

type Props = {
  label: string;
  value: string;
  sub?: string;
  subTone?: Tone;
  tone?: Tone;
  size?: 'xl' | 'lg';
  /** when this changes (after the first render) the cell flashes for ~1 s */
  flashOn?: unknown;
};

/** Zero-padded numeral cell (History/Monitor stat style) scaled up for projectors. */
export function StatCell({ label, value, sub, subTone = 'muted', tone = 'muted', size = 'xl', flashOn }: Props) {
  const [flashing, setFlashing] = useState(false);
  const first = useRef(true);
  const last = useRef(flashOn);

  useEffect(() => {
    if (first.current) {
      first.current = false;
      return;
    }
    if (Object.is(last.current, flashOn)) return;
    last.current = flashOn;
    setFlashing(true);
    const t = window.setTimeout(() => setFlashing(false), 1000);
    return () => window.clearTimeout(t);
  }, [flashOn]);

  const numeral = size === 'xl' ? 'text-[clamp(2.6rem,4.4vw,4.6rem)]' : 'text-[clamp(1.9rem,2.8vw,2.9rem)]';
  return (
    <div className={`flex flex-col justify-center bg-surface-muted/90 px-5 py-4 ${flashing ? 'cloud-flash' : ''}`}>
      <p className="font-mono text-[14px] uppercase tracking-[0.16em] text-text-muted">{label}</p>
      <p className={`mt-1 font-mono font-semibold leading-none tabular-nums ${numeral} ${tone === 'muted' ? 'text-text-primary' : TONE_TEXT[tone]}`}>
        {value}
      </p>
      {sub !== undefined && (
        <p className={`mt-2 min-h-5 font-mono text-[14px] uppercase tracking-[0.12em] ${TONE_TEXT[subTone]}`}>{sub}</p>
      )}
    </div>
  );
}
