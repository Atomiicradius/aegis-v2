import { useState } from 'react';

import type { CloudRecentRow } from '../../lib/cloudApi';
import type { MockScenario } from '../../lib/cloudMock';
import { useCloudRecent } from '../../lib/useCloudRecords';
import { PanelHeader } from './PanelHeader';
import { RecordDetail } from './RecordDetail';
import { StatusChip } from './StatusChip';

const pad = (n: number, w: number) => String(n).padStart(w, '0');
const latency = (ms: number) => (ms < 1000 ? `${ms} ms` : `${(ms / 1000).toFixed(1)} s`);
const outcomeTone = (status: string) => (status.startsWith('approved') ? 'success' : 'danger');

const TH = 'border-r border-border px-3 py-3 font-medium';

function Row({ row, index, selected, onToggle }: { row: CloudRecentRow; index: number; selected: boolean; onToggle: () => void }) {
  // Critical rows get the amber left rule + dot (never a filled background); the open row gets a quiet tint.
  const rule = row.is_critical ? 'border-l-warning' : selected ? 'border-l-accent' : 'border-l-transparent';
  return (
    <tr
      onClick={onToggle}
      className={`cursor-pointer align-middle transition-colors duration-200 hover:bg-surface-secondary/40 ${selected ? 'bg-surface-secondary/50' : ''}`}
    >
      <td className={`border-r border-l-2 border-border px-3 py-3 font-mono text-[14px] ${rule} ${row.is_critical ? 'text-warning-light' : 'text-text-muted'}`}>
        <span className="flex items-center gap-2">
          {row.is_critical && <span aria-hidden="true" className="h-2 w-2 bg-warning shadow-[0_0_8px_var(--color-warning)]" />}
          {pad(index + 1, 3)}
        </span>
      </td>
      <td className="border-r border-border px-3 py-3">
        <span className="block font-mono text-[14px] font-semibold leading-snug">
          <span className="inline-block whitespace-nowrap text-accent-light">{row.primary_name}</span>
          <span className="mx-2 text-text-muted">/</span>
          <span className="inline-block whitespace-nowrap text-success-light">{row.secondary_name}</span>
        </span>
        <span className="mt-0.5 block truncate font-mono text-[14px] text-text-muted">NORAD {row.primary_id} / {row.secondary_id}</span>
      </td>
      <td className="border-r border-border px-3 py-3 text-right font-mono text-[14px] tabular-nums text-text-primary">{row.miss_km.toFixed(2)}</td>
      <td className={`border-r border-border px-3 py-3 text-right font-mono text-[14px] tabular-nums ${row.is_critical ? 'font-semibold text-warning-light' : 'text-text-primary'}`}>{row.risk.toFixed(2)}</td>
      <td className="border-r border-border px-3 py-3 text-right font-mono text-[14px] tabular-nums text-text-primary">{pad(row.rounds, 2)}</td>
      <td className="border-r border-border px-3 py-3">
        <span className="flex flex-wrap items-center gap-2">
          <StatusChip tone={outcomeTone(row.resolution_status)} label={row.resolution_status === 'no_safe_maneuver_found' ? 'no safe maneuver' : row.resolution_status.replace(/_/g, ' ')} />
          {row.is_critical && <StatusChip tone="warning" label="critical" />}
        </span>
      </td>
      <td className="border-r border-border px-3 py-3 text-right font-mono text-[14px] tabular-nums text-text-primary">{row.delta_v_mps > 0 ? `${row.delta_v_mps.toFixed(1)} m/s` : '--'}</td>
      <td className={`border-r border-border px-3 py-3 text-right font-mono text-[14px] tabular-nums ${row.is_critical ? 'text-warning-light' : 'text-text-secondary'}`}>{latency(row.latency_ms)}</td>
      <td className="px-3 py-3 text-right">
        <button
          type="button"
          aria-expanded={selected}
          onClick={(e) => { e.stopPropagation(); onToggle(); }}
          className="border-b border-border-light pb-1 font-mono text-[14px] uppercase tracking-[0.1em] text-text-secondary transition-colors hover:border-accent hover:text-accent-light focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
        >
          {selected ? 'close ×' : 'open →'}
        </button>
      </td>
    </tr>
  );
}

/** "Resolution records": the last finished negotiations (History-ledger style) and, on click, their transcript. */
export function ResolutionRecords({ mock }: { mock: MockScenario | null }) {
  const [pointing, setPointing] = useState(false);
  const { rows, ok } = useCloudRecent(mock, pointing);
  const [openId, setOpenId] = useState<string | null>(null);
  // keep the detail even when its row scrolls out of the 10-row table (the transcript is still stored)
  const [openRow, setOpenRow] = useState<CloudRecentRow | null>(null);
  const toggle = (row: CloudRecentRow) => {
    if (openId === row.event_id) {
      setOpenId(null);
      setOpenRow(null);
    } else {
      setOpenId(row.event_id);
      setOpenRow(row);
    }
  };
  const critical = rows.filter((r) => r.is_critical).length;

  return (
    <section className="border border-border bg-surface-muted/75" aria-label="Resolution records">
      <PanelHeader
        eyebrow="Resolution records"
        title="What the cluster decided"
        right={
          <span className="flex flex-wrap items-center justify-end gap-3">
            {critical > 0 && <StatusChip tone="warning" label={`${critical} critical`} />}
            <StatusChip tone={pointing ? 'muted' : ok ? 'success' : 'warning'} label={pointing ? 'feed paused' : ok ? 'feed / 2 s' : 'feed stale'} pulse={!ok && !pointing} />
          </span>
        }
      />
      <div
        className="overflow-x-auto"
        onMouseEnter={() => setPointing(true)}
        onMouseLeave={() => setPointing(false)}
        onFocus={() => setPointing(true)}
        onBlur={() => setPointing(false)}
      >
        <table className="w-full min-w-[1080px] table-fixed border-collapse text-left">
          <thead className="bg-surface-secondary/55 font-mono text-[14px] uppercase tracking-[0.14em] text-text-muted">
            <tr>
              <th className={`${TH} w-[6rem]`}>rec</th>
              <th className={TH}>object pair</th>
              <th className={`${TH} w-[7rem] text-right`}>miss km</th>
              <th className={`${TH} w-[6rem] text-right`}>risk</th>
              <th className={`${TH} w-[7rem] text-right`}>rounds</th>
              <th className={`${TH} w-[16.5rem]`}>outcome</th>
              <th className={`${TH} w-[7.5rem] text-right`}>&Delta;v</th>
              <th className={`${TH} w-[8rem] text-right`}>latency</th>
              <th className="w-[7rem] px-3 py-3 text-right font-medium">record</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {rows.length === 0 ? (
              <tr>
                <td colSpan={9} className="px-5 py-10">
                  <div className="border-l-2 border-warning pl-4">
                    <p className="font-mono text-[14px] uppercase tracking-[0.14em] text-warning-light">{ok ? 'No records yet' : 'Records unavailable'}</p>
                    <p className="mt-1 text-[15px] leading-6 text-text-secondary">
                      {ok ? 'Run a storm or inject critical events: each finished negotiation appears here, newest first.' : 'The cluster API did not answer. Records return when the link does.'}
                    </p>
                  </div>
                </td>
              </tr>
            ) : (
              rows.map((row, i) => <Row key={row.event_id} row={row} index={i} selected={openId === row.event_id} onToggle={() => toggle(row)} />)
            )}
          </tbody>
        </table>
      </div>
      <p className="border-t border-border px-5 py-3 font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">
        last 10 finished jobs / newest critical rows stay pinned / transcripts kept 2 h / real satellites, synthetic close approaches
      </p>
      {openRow && <RecordDetail row={openRow} mock={mock} onClose={() => { setOpenId(null); setOpenRow(null); }} />}
    </section>
  );
}
