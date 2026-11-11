import { useEffect, useRef } from 'react';

import { EXPIRED, type CloudRecentRow } from '../../lib/cloudApi';
import type { MockScenario } from '../../lib/cloudMock';
import { useCloudResolution } from '../../lib/useCloudRecords';
import { NegotiationConsole } from '../negotiation/NegotiationConsole';
import { ResolutionCard } from '../negotiation/ResolutionCard';
import { LeftRuleCallout } from './LeftRuleCallout';

/**
 * Transcript + resolution of one record, drawn by the ORIGINAL Negotiate components
 * (NegotiationConsole, ResolutionCard); only a wrapper class lifts their micro type to /cloud's 14 px floor.
 * Distinct states: loading, expired (404), unreachable, ready.
 */
export function RecordDetail({ row, mock, onClose }: { row: CloudRecentRow; mock: MockScenario | null; onClose: () => void }) {
  const { status, result } = useCloudResolution(row.event_id, mock);
  const ref = useRef<HTMLDivElement>(null);

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

      {status !== 'done' || !result ? (
        <p className="mt-5 font-mono text-[14px] uppercase tracking-[0.14em] text-text-muted">loading transcript...</p>
      ) : !result.ok ? (
        <div className="mt-5">
          {result.error === EXPIRED ? (
            <LeftRuleCallout tone="warning" label="Transcript expired">
              Full transcripts are kept for 2 hours (and only for critical jobs plus the last 30 finished). This record has aged out; the summary row above is all that remains.
            </LeftRuleCallout>
          ) : (
            <LeftRuleCallout tone="danger" label="Transcript unavailable">
              The cluster API did not return this record ({result.error}). The summary row is unaffected; try again in a moment.
            </LeftRuleCallout>
          )}
        </div>
      ) : (
        <div className="cloud-legible">
          <NegotiationConsole messages={result.data.transcript} instant />
          <ResolutionCard key={row.event_id} resolution={result.data.resolution} showTrajectoryLink={false} residualAsDistance />
        </div>
      )}
    </div>
  );
}
