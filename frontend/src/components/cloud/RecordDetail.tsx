import { useEffect, useRef } from 'react';

import { EXPIRED, type CloudRecentRow, type DomainBlock } from '../../lib/cloudApi';
import type { MockScenario } from '../../lib/cloudMock';
import { useCloudResolution } from '../../lib/useCloudRecords';
import { useTrace } from '../../lib/useTrace';
import { NegotiationConsole } from '../negotiation/NegotiationConsole';
import { ResolutionCard } from '../negotiation/ResolutionCard';
import { LeftRuleCallout } from './LeftRuleCallout';
import { TraceWaterfall } from './TraceWaterfall';

// ---- domain header ----------------------------------------------------------

function RiskBar({ label, value }: { label: string; value: number }) {
  const pct = Math.round(value * 100);
  return (
    <div className="flex items-center gap-2">
      <span className="w-16 font-mono text-[11px] uppercase tracking-[0.1em] text-text-muted">{label}</span>
      <div className="h-1.5 flex-1 bg-surface-tertiary">
        <div className="h-full bg-accent-light" style={{ width: `${pct}%` }} />
      </div>
      <span className="w-8 text-right font-mono text-[11px] text-text-secondary">{pct}%</span>
    </div>
  );
}

function DomainHeader({ domain }: { domain: DomainBlock }) {
  const { a, b, miss_km, lead_h, risk, risk_parts, is_critical } = domain;
  const aName = a?.name ?? `NORAD ${a?.norad ?? '?'}`;
  const bName = b?.name ?? `NORAD ${b?.norad ?? '?'}`;
  return (
    <div className="mb-4 border border-border bg-surface-muted/60 px-4 py-3">
      {/* NAME × NAME / MISS km / TCA +h / RISK / CRITICAL */}
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[13px]">
        <span className="font-semibold text-accent-light">{aName}</span>
        <span className="text-text-muted">×</span>
        <span className="font-semibold text-success-light">{bName}</span>
        <span className="ml-2 text-text-muted">|</span>
        <span className="text-text-secondary">MISS <span className="text-text-primary">{miss_km.toFixed(2)} km</span></span>
        <span className="text-text-muted">|</span>
        <span className="text-text-secondary">
          TCA <span className="text-text-primary">+{lead_h != null ? lead_h.toFixed(1) : '--'} h</span>
        </span>
        <span className="text-text-muted">|</span>
        <span className="text-text-secondary">
          RISK <span className={`font-semibold ${risk >= 0.7 ? 'text-warning-light' : 'text-text-primary'}`}>{risk.toFixed(3)}</span>
        </span>
        {is_critical && (
          <span className="border border-warning/60 px-2 py-0.5 font-mono text-[11px] uppercase tracking-[0.12em] text-warning-light">
            CRITICAL
          </span>
        )}
      </div>

      {/* Risk breakdown */}
      {risk_parts && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          <RiskBar label="close" value={risk_parts.closeness} />
          <RiskBar label="urgency" value={risk_parts.urgency} />
          <RiskBar label="mission" value={risk_parts.mission} />
        </div>
      )}

      {/* Operator row */}
      {(a || b) && (
        <div className="mt-2 flex flex-wrap gap-x-6 gap-y-0.5 font-mono text-[11px] text-text-muted">
          {a && <span><span className="text-accent-light">{a.norad}</span> · {a.operator}</span>}
          {b && <span><span className="text-success-light">{b.norad}</span> · {b.operator}</span>}
        </div>
      )}
    </div>
  );
}

function CompactOutcome({ domain }: { domain: DomainBlock }) {
  const { maneuvering_norad, delta_v_mps, residual_km, validation_clear } = domain;
  return (
    <div className="border border-border bg-surface-muted/40 px-4 py-3">
      <p className="mb-2 font-mono text-[11px] uppercase tracking-[0.12em] text-text-muted">compact outcome</p>
      <dl className="grid grid-cols-2 gap-x-6 gap-y-1.5 font-mono text-[13px]">
        <div>
          <dt className="text-text-muted">maneuvering</dt>
          <dd className="text-text-primary">{maneuvering_norad ?? '--'}</dd>
        </div>
        <div>
          <dt className="text-text-muted">Δv</dt>
          <dd className="text-text-primary">{delta_v_mps != null ? `${delta_v_mps.toFixed(1)} m/s` : '--'}</dd>
        </div>
        <div>
          <dt className="text-text-muted">residual</dt>
          <dd className="text-text-primary">{residual_km != null ? `${residual_km.toFixed(3)} km` : '--'}</dd>
        </div>
        <div>
          <dt className="text-text-muted">validation</dt>
          <dd className={validation_clear ? 'text-success-light' : 'text-danger-light'}>
            {validation_clear == null ? '--' : validation_clear ? 'clear' : 'failed'}
          </dd>
        </div>
      </dl>
      <p className="mt-2 font-mono text-[11px] uppercase tracking-[0.1em] text-text-muted">transcript not retained (critical + last 30 only)</p>
    </div>
  );
}

// ---- component --------------------------------------------------------------

/**
 * Transcript + resolution of one record, drawn by the ORIGINAL Negotiate components
 * (NegotiationConsole, ResolutionCard); only a wrapper class lifts their micro type to /cloud's 14 px floor.
 * Distinct states: loading, expired (404), unreachable, ready.
 */
export function RecordDetail({ row, mock, onClose }: { row: CloudRecentRow; mock: MockScenario | null; onClose: () => void }) {
  const { status, result } = useCloudResolution(row.event_id, mock);
  const traceState = useTrace(row.event_id, mock);
  const ref = useRef<HTMLDivElement>(null);
  const domain = traceState.status === 'done' && traceState.result?.ok
    ? traceState.result.data.domain
    : undefined;

  // bring the opened record into view (it renders below the table, possibly under the fold)
  useEffect(() => {
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
    ref.current?.scrollIntoView({ block: 'nearest', behavior: reduce ? 'auto' : 'smooth' });
  }, [row.event_id]);

  return (
    <div ref={ref} className="border-t border-border px-5 pb-6 pt-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <p className="font-mono text-[14px] uppercase tracking-[0.16em] text-text-muted">
          record / <span className="text-text-primary">{row.event_id.slice(0, 8)}</span>
          <span className="mx-3 text-border-light">|</span>
          <span className="text-accent-light">{row.primary_name}</span> <span className="text-text-muted">/</span>{' '}
          <span className="text-success-light">{row.secondary_name}</span>
        </p>
        <button
          type="button"
          onClick={onClose}
          className="border border-border-light px-3 py-1.5 font-mono text-[14px] uppercase tracking-[0.12em] text-text-secondary transition-colors hover:border-accent hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent"
        >
          close ×
        </button>
      </div>

      {/* Domain header — shown whenever trace data has domain */}
      {domain && <DomainHeader domain={domain} />}

      {status !== 'done' || !result ? (
        <p className="mt-5 font-mono text-[14px] uppercase tracking-[0.14em] text-text-muted">loading transcript...</p>
      ) : !result.ok ? (
        <div className="mt-5">
          {result.error === EXPIRED ? (
            <>
              <LeftRuleCallout tone="warning" label="Transcript expired">
                Full transcripts are kept for 2 hours (and only for critical jobs plus the last 30 finished). This record has aged out; the summary row above is all that remains.
              </LeftRuleCallout>
              {domain && (
                <div className="mt-4">
                  <CompactOutcome domain={domain} />
                </div>
              )}
            </>
          ) : (
            <LeftRuleCallout tone="danger" label="Transcript unavailable">
              The cluster API did not return this record ({result.error}). The summary row is unaffected; try again in a moment.
            </LeftRuleCallout>
          )}
        </div>
      ) : (
        <div className="cloud-legible">
          {/* Two-column layout: waterfall left, negotiation right (when transcript available) */}
          <div className="flex flex-col gap-5 lg:flex-row lg:items-start">
            <div className="min-w-0 flex-1">
              {traceState.status === 'done' && traceState.result?.ok && (
                <TraceWaterfall trace={traceState.result.data} />
              )}
            </div>
            <div className="min-w-0 flex-1">
              <NegotiationConsole messages={result.data.transcript} instant />
              <ResolutionCard key={row.event_id} resolution={result.data.resolution} showTrajectoryLink={false} residualAsDistance />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
