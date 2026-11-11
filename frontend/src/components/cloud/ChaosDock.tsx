/**
 * ChaosDock — fault-injection control panel.
 * Polls chaos_agent (localhost:8099) every 2 s for status and scenario list.
 * POST /run triggers a scenario; POST /abort cancels it.
 * Shows recent chaos events from the shared timeline prop.
 * When chaos_agent is unreachable the panel shows a "not running" notice.
 */
import { useEffect, useRef, useState } from 'react';

import type { TimelineEvent } from '../../lib/cloudApi';
import { PanelHeader } from './PanelHeader';
import { StatusChip } from './StatusChip';

const AGENT = 'http://127.0.0.1:8099';
const POLL_MS = 2000;

type AgentStatus = {
  enabled: boolean;
  api: string;
  running: string | null;
  host: string;
};

type Scenario = {
  name: string;
  label: string;
  kind: string;
  n: number | null;
};

function isChaosType(t: string) {
  return t.startsWith('chaos_') || t === 'redis_down' || t === 'redis_up';
}

async function agentFetch<T>(path: string, init?: RequestInit): Promise<T | null> {
  try {
    const r = await fetch(`${AGENT}${path}`, { ...init, signal: AbortSignal.timeout(2000) });
    if (!r.ok) return null;
    return (await r.json()) as T;
  } catch {
    return null;
  }
}

interface Props {
  events: TimelineEvent[];
  mock: string | null;
}

export function ChaosDock({ events, mock }: Props) {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [scenarios, setScenarios] = useState<Scenario[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [lastMsg, setLastMsg] = useState('');
  const startRef = useRef(Date.now());

  const chaosEvents = events.filter((e) => isChaosType(e.type)).slice(0, 6);
  const online = status !== null;

  useEffect(() => {
    if (mock) {
      // In mock mode, synthesize a fake online agent status
      setStatus({ enabled: true, api: 'http://localhost:8010', running: null, host: '127.0.0.1' });
      setScenarios([
        { name: 'kill1', label: 'kill 1 of 4 workers', kind: 'worker_kill', n: 1 },
        { name: 'kill2', label: 'kill 2 of 4 workers', kind: 'worker_kill', n: 2 },
        { name: 'kill3', label: 'kill 3 of 4 workers', kind: 'worker_kill', n: 3 },
        { name: 'redis_restart', label: 'graceful redis restart', kind: 'redis_restart', n: null },
        { name: 'redis_kill', label: 'hard redis kill', kind: 'redis_kill', n: null },
      ]);
      return;
    }

    let cancelled = false;
    async function poll() {
      if (cancelled) return;
      const s = await agentFetch<AgentStatus>('/status');
      if (!cancelled) setStatus(s);
      if (s && scenarios.length === 0) {
        const sc = await agentFetch<Scenario[]>('/scenarios');
        if (!cancelled && sc) setScenarios(sc);
      }
    }

    void poll();
    const id = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mock]);

  async function runScenario(name: string) {
    if (busy) return;
    if (mock) {
      setLastMsg(`[mock] would run: ${name}`);
      return;
    }
    setBusy(name);
    const result = await agentFetch<{ ok: boolean; error?: string }>('/run', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ scenario: name }),
    });
    setBusy(null);
    if (!result) {
      setLastMsg('agent unreachable');
    } else if (!result.ok) {
      setLastMsg(result.error ?? 'failed');
    } else {
      setLastMsg(`started: ${name}`);
    }
    startRef.current = Date.now();
  }

  async function abort() {
    if (mock) { setLastMsg('[mock] abort'); return; }
    await agentFetch('/abort', { method: 'POST' });
    setLastMsg('abort sent');
  }

  const kindStyle: Record<string, string> = {
    worker_kill: 'border-danger/50 text-danger-light hover:border-danger hover:bg-danger-muted/40',
    redis_restart: 'border-warning/50 text-warning-light hover:border-warning hover:bg-warning-muted/40',
    redis_kill: 'border-danger/70 text-danger-light hover:border-danger hover:bg-danger-muted/60',
  };

  return (
    <section className="border border-border bg-surface-muted/75">
      <PanelHeader
        eyebrow="Chaos dock"
        title="Fault injection"
        right={
          <StatusChip
            tone={online ? (status?.enabled ? 'danger' : 'warning') : 'muted'}
            label={online ? (status?.enabled ? 'armed' : 'read-only') : 'agent offline'}
          />
        }
      />

      {!online && !mock && (
        <div className="border-l-2 border-warning px-5 py-4 text-[14px] text-text-secondary">
          <span className="font-mono uppercase tracking-[0.12em] text-warning-light">chaos_agent not running</span>
          <p className="mt-1 font-mono text-[13px] text-text-muted">
            python scripts/chaos_agent.py --enable --api http://localhost:8010
          </p>
        </div>
      )}

      {(online || mock) && (
        <>
          {/* Status bar */}
          <div className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-border px-5 py-3 font-mono text-[13px] uppercase tracking-[0.1em]">
            <span className="text-text-muted">
              mode / <span className={status?.enabled ? 'text-danger-light' : 'text-warning-light'}>
                {status?.enabled ? 'armed' : 'read-only'}
              </span>
            </span>
            <span className="text-text-muted">
              running / <span className="text-text-primary">{status?.running ?? 'none'}</span>
            </span>
          </div>

          {/* Scenario buttons */}
          <div className="flex flex-wrap gap-3 px-5 py-4">
            {scenarios.map((sc) => (
              <button
                key={sc.name}
                type="button"
                disabled={busy !== null || !status?.enabled}
                onClick={() => void runScenario(sc.name)}
                className={`border px-4 py-2 font-mono text-[13px] uppercase tracking-[0.1em] transition duration-150 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent disabled:pointer-events-none disabled:opacity-40 ${kindStyle[sc.kind] ?? 'border-border-light text-text-secondary hover:border-accent hover:text-text-primary'}`}
              >
                {sc.label}
              </button>
            ))}
            {status?.running && (
              <button
                type="button"
                onClick={() => void abort()}
                className="border border-warning/50 px-4 py-2 font-mono text-[13px] uppercase tracking-[0.1em] text-warning-light transition hover:border-warning hover:bg-warning-muted/40"
              >
                Abort
              </button>
            )}
          </div>

          {lastMsg && (
            <p className="px-5 pb-3 font-mono text-[13px] uppercase tracking-[0.1em] text-text-muted">
              {lastMsg}
            </p>
          )}

          {/* Recent chaos events */}
          {chaosEvents.length > 0 && (
            <div className="border-t border-border px-5 py-3">
              <p className="mb-2 font-mono text-[13px] uppercase tracking-[0.12em] text-text-muted">recent chaos events</p>
              <ul className="space-y-1">
                {chaosEvents.map((e, i) => (
                  <li key={`${e.seq}-${i}`} className="flex items-center gap-3 font-mono text-[13px]">
                    <span className={e.type.includes('fault') || e.type === 'redis_down' ? 'text-danger-light' : 'text-success'}>
                      {e.type.includes('fault') || e.type === 'redis_down' ? '✕' : '◎'}
                    </span>
                    <span className="text-text-primary">{e.type}</span>
                    <span className="text-text-muted">{typeof e.detail.label === 'string' ? e.detail.label : e.source}</span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </>
      )}
    </section>
  );
}
