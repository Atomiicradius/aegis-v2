/**
 * RacePanel — Block 5 FIFO vs priority race visualization.
 * Shows two lanes side by side: each with named critical pairs, TCA +h (from lead_h),
 * LEAD TIME CONSUMED % and TCA MISSED driven by the SimClock (display only).
 */
import { useEffect, useRef, useState } from 'react';

import { fetchRaceResult, postRaceReset, postRaceStart, type RaceLane, type RacePair, type RaceResult, type RaceRunning } from '../../lib/cloudApi';
import type { MockScenario } from '../../lib/cloudMock';
import { mockRaceResult } from '../../lib/cloudMockRecords';
import { PanelHeader } from './PanelHeader';
import { StatusChip } from './StatusChip';
import { DEFAULT_SCALE, isTcaMissed, leadConsumedPct, SimClock, type ClockScale } from './SimClock';

// ---- shared token helpers --------------------------------------------------
const MONO = 'font-mono text-[12px] uppercase tracking-[0.1em]';
const DASH = '--';

// ---- TCA gauge --------------------------------------------------------------
function LeadGauge({ pct, missed }: { pct: number; missed: boolean }) {
  const color = missed ? 'var(--color-danger)' : pct > 75 ? 'var(--color-warning)' : 'var(--color-success)';
  return (
    <div className="mt-1 h-1.5 w-full rounded-full bg-border overflow-hidden">
      <div
        className="h-full rounded-full transition-all duration-300"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

// ---- one critical pair row --------------------------------------------------
function PairRow({ pair, scale }: { pair: RacePair; scale: ClockScale }) {
  const lead = pair.lead_h ?? 24;
  const pct = leadConsumedPct(pair.resolution_ms, lead, scale);
  const missed = isTcaMissed(pair.resolution_ms, lead, scale);

  return (
    <li className={`px-4 py-2.5 border-b border-border last:border-0 ${missed ? 'border-l-2 border-l-danger' : ''}`}>
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate font-mono text-[13px] font-semibold text-text-primary leading-tight">
            {pair.primary_name}
          </p>
          <p className="truncate font-mono text-[11px] text-text-muted leading-tight">
            × {pair.secondary_name}
          </p>
        </div>
        <div className="flex shrink-0 flex-col items-end gap-0.5">
          <span className={`${MONO} ${missed ? 'text-danger-light' : 'text-text-muted'}`}>
            {missed ? 'TCA MISSED' : `TCA +${lead.toFixed(1)}h`}
          </span>
          <span className={`${MONO} text-warning-light`}>risk {pair.risk.toFixed(2)}</span>
        </div>
      </div>
      <div className="mt-1.5 flex items-center gap-2">
        <span className={`${MONO} text-text-muted shrink-0`}>{pct}%</span>
        <LeadGauge pct={pct} missed={missed} />
        <span className={`${MONO} shrink-0 ${missed ? 'text-danger-light' : 'text-text-muted'}`}>
          {pair.resolution_ms}ms
        </span>
      </div>
    </li>
  );
}

// ---- lane panel -------------------------------------------------------------
function LanePanel({ lane, scale }: { lane: RaceLane; scale: ClockScale }) {
  const missed = lane.critical_pairs.filter(
    (p) => isTcaMissed(p.resolution_ms, p.lead_h ?? 24, scale),
  ).length;

  const tone = lane.policy === 'priority' ? 'warning' : 'accent';
  const toneText = lane.policy === 'priority' ? 'text-warning-light' : 'text-accent-light';
  const toneBorder = lane.policy === 'priority' ? 'border-warning/40' : 'border-accent/40';

  return (
    <div className={`flex flex-col border ${toneBorder} bg-surface-muted/75`}>
      <div className={`flex items-center justify-between gap-3 border-b border-border px-5 py-3`}>
        <div>
          <p className={`font-mono text-[13px] uppercase tracking-[0.18em] ${toneText}`}>
            {lane.policy === 'fifo' ? 'FIFO — first-in, first-out' : 'PRIORITY — risk-first'}
          </p>
          <p className="mt-0.5 font-mono text-[11px] uppercase tracking-[0.1em] text-text-muted">
            {lane.total_jobs} jobs · {lane.critical_jobs} critical
          </p>
        </div>
        <StatusChip
          tone={missed > 0 ? 'danger' : 'success'}
          label={missed > 0 ? `${missed} TCA MISSED` : 'all resolved'}
        />
      </div>

      {/* Stats row */}
      <dl className="grid grid-cols-3 divide-x divide-border border-b border-border">
        <div className="px-4 py-3">
          <dt className={`${MONO} text-text-muted`}>Crit res avg</dt>
          <dd className={`mt-0.5 font-mono text-[1.25rem] font-semibold tabular-nums ${toneText}`}>
            {lane.critical_resolution_ms.avg}<span className="ml-1 text-sm text-text-muted">ms</span>
          </dd>
        </div>
        <div className="px-4 py-3">
          <dt className={`${MONO} text-text-muted`}>Drain</dt>
          <dd className={`mt-0.5 font-mono text-[1.25rem] font-semibold tabular-nums text-text-primary`}>
            {lane.drain_ms.toLocaleString()}<span className="ml-1 text-sm text-text-muted">ms</span>
          </dd>
        </div>
        <div className="px-4 py-3">
          <dt className={`${MONO} text-text-muted`}>TCA missed</dt>
          <dd className={`mt-0.5 font-mono text-[1.25rem] font-semibold tabular-nums ${missed > 0 ? 'text-danger-light' : 'text-success'}`}>
            {missed}
          </dd>
        </div>
      </dl>

      {/* Critical pairs list */}
      <div className="flex-1">
        <p className={`${MONO} border-b border-border px-4 py-2 text-text-muted`}>
          Critical conjunctions — lead time consumed at ×{scale} scale
        </p>
        {lane.critical_pairs.length === 0 ? (
          <p className={`${MONO} px-4 py-6 text-text-muted`}>no critical pairs</p>
        ) : (
          <ol>
            {lane.critical_pairs.map((p) => (
              <PairRow key={p.event_id} pair={p} scale={scale} />
            ))}
          </ol>
        )}
      </div>

      {/* Footer */}
      <div className="border-t border-border px-4 py-2 flex gap-4">
        <span className={`${MONO} text-text-muted`}>
          drain {lane.drain_ms.toLocaleString()} ms wall-clock
        </span>
        {lane.failed_jobs > 0 && (
          <span className={`${MONO} text-danger-light`}>{lane.failed_jobs} failed</span>
        )}
      </div>
    </div>
  );
}

// ---- advantage badge --------------------------------------------------------
function AdvantBadge({ result }: { result: RaceResult }) {
  const fifoAvg = result.fifo.critical_resolution_ms.avg;
  const prioAvg = result.priority.critical_resolution_ms.avg;
  if (prioAvg === 0 || fifoAvg === 0) return null;
  const factor = fifoAvg / prioAvg;
  return (
    <div className="border border-warning/40 bg-warning-muted/15 px-4 py-2 font-mono text-[13px] uppercase tracking-[0.12em]">
      <span className="text-text-muted">Priority advantage </span>
      <span className="text-warning-light font-semibold">
        {factor >= 10 ? Math.round(factor) : factor.toFixed(1)}×
      </span>
      <span className="ml-3 text-text-muted text-[11px]">faster critical processing</span>
    </div>
  );
}

// ---- live progress bar during running ----------------------------------------
function ProgressBar({ done, total, label }: { done: number; total: number; label: string }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 0;
  return (
    <div className="flex items-center gap-3 py-1">
      <span className={`${MONO} w-24 shrink-0 text-text-muted`}>{label}</span>
      <div className="flex-1 h-1.5 rounded-full bg-border overflow-hidden">
        <div className="h-full rounded-full bg-accent transition-all duration-500" style={{ width: `${pct}%` }} />
      </div>
      <span className={`${MONO} shrink-0 text-text-muted`}>{done}/{total}</span>
    </div>
  );
}

// ---- main component ---------------------------------------------------------
type Props = { mock: MockScenario | null };

export function RacePanel({ mock }: Props) {
  const [scale, setScale] = useState<ClockScale>(DEFAULT_SCALE);
  const [result, setResult] = useState<RaceResult | null>(null);
  const [partial, setPartial] = useState<RaceRunning | null>(null);
  const [status, setStatus] = useState<'idle' | 'running' | 'done' | 'error'>('idle');
  const [busy, setBusy] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // In mock mode, seed the result immediately
  useEffect(() => {
    if (mock === 'race') {
      setResult(mockRaceResult());
      setStatus('done');
    }
  }, [mock]);

  // Poll for result while running — also surfaces partial lane data
  useEffect(() => {
    if (status !== 'running' || mock) return;
    pollRef.current = setInterval(async () => {
      const r = await fetchRaceResult();
      if (!r.ok) return;
      if ('status' in r.data && r.data.status === 'running') {
        setPartial(r.data as RaceRunning);
        return;
      }
      setPartial(null);
      setResult(r.data as RaceResult);
      setStatus('done');
      if (pollRef.current) clearInterval(pollRef.current);
    }, 2000);
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [status, mock]);

  async function startRace() {
    if (mock) return;
    setBusy(true);
    setResult(null);
    setPartial(null);
    setStatus('running');
    await postRaceStart({ n: 300, critical: 3, seed: 42, critical_offset_s: 2 });
    setBusy(false);
  }

  async function resetRace() {
    if (mock) return;
    setBusy(true);
    await postRaceReset();
    setResult(null);
    setPartial(null);
    setStatus('idle');
    setBusy(false);
  }

  return (
    <section className="border border-border bg-surface-muted/80" aria-label="FIFO vs priority race">
      <PanelHeader
        eyebrow="Block 5 — scheduling race"
        title="FIFO vs priority"
        right={
          <div className="flex gap-2">
            {!mock && (
              <>
                <button
                  type="button"
                  disabled={busy || status === 'running'}
                  onClick={() => void startRace()}
                  className="border border-warning/50 px-4 py-2 font-mono text-[13px] font-semibold uppercase tracking-[0.1em] text-warning-light hover:border-warning hover:bg-warning-muted/30 disabled:pointer-events-none disabled:opacity-45 transition-colors"
                >
                  {status === 'running' ? 'running…' : 'start race'}
                </button>
                {result && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void resetRace()}
                    className="border border-border px-4 py-2 font-mono text-[13px] uppercase tracking-[0.1em] text-text-muted hover:border-border-light hover:text-text-secondary disabled:pointer-events-none disabled:opacity-45 transition-colors"
                  >
                    clear
                  </button>
                )}
              </>
            )}
          </div>
        }
      />

      <SimClock value={scale} onChange={setScale} />

      {status === 'idle' && !result && (
        <p className={`${MONO} px-5 py-8 text-text-muted`}>
          {mock ? 'no race scenario active' : 'press START RACE to run 300 jobs (3 critical, seed 42) through both policies simultaneously'}
        </p>
      )}

      {status === 'running' && (
        <div className="border-b border-border px-5 py-4">
          <p className={`${MONO} animate-pulse text-text-muted mb-3`}>
            race running — 300 jobs × 2 real worker lanes…
          </p>
          {partial && (
            <div className="space-y-1">
              <ProgressBar done={partial.fifo_done} total={partial.total} label="FIFO" />
              <ProgressBar done={partial.prio_done} total={partial.total} label="PRIORITY" />
            </div>
          )}
        </div>
      )}

      {result && (
        <>
          <AdvantBadge result={result} />
          <div className="grid gap-px border-t border-border bg-border lg:grid-cols-2">
            <LanePanel lane={result.fifo} scale={scale} />
            <LanePanel lane={result.priority} scale={scale} />
          </div>
          <div className="border-t border-border px-5 py-2 font-mono text-[11px] uppercase tracking-[0.1em] text-text-muted">
            {result.n} total jobs · {result.critical} critical · seed {result.seed}
            {result.start_skew_ms != null && <> · start skew {result.start_skew_ms} ms</>}
            {' · '}race completed {new Date(result.enqueued_at).toLocaleTimeString()}
          </div>
        </>
      )}

      {status === 'running' && partial?.fifo && partial?.priority && (
        <div className="grid gap-px border-t border-border bg-border lg:grid-cols-2">
          <LanePanel lane={partial.fifo} scale={scale} />
          <LanePanel lane={partial.priority} scale={scale} />
        </div>
      )}
    </section>
  );
}
