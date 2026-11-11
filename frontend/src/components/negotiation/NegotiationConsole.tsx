import { animate } from 'animejs';
import { useEffect, useRef } from 'react';

import { useAnimeAppend } from '../../lib/useAnimeStagger';
import type { NegotiationMessage } from '../globe/types';

const AGENT_LABELS = {
  operator_A: { node: 'OP-A', label: 'Primary operator', tone: 'text-accent-light' },
  operator_B: { node: 'OP-B', label: 'Secondary operator', tone: 'text-success-light' },
  validation: { node: 'VAL', label: 'Safety gate', tone: 'text-warning-light' },
} as const;

const ACTION_TONES = {
  maneuver: 'border-accent/40 bg-accent-muted/35 text-accent-light',
  stand_down: 'border-border-light bg-surface-tertiary text-text-secondary',
  reject: 'border-danger/40 bg-danger-muted/35 text-danger-light',
  approve: 'border-success/40 bg-success-muted/35 text-success-light',
} as const;

function formatUtc(timestamp: string): string {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return '-';
  return `${date.toISOString().slice(11, 19)}Z`;
}

/** Animates a numeric counter from its current displayed value to `to`. */
function useCountUp(ref: React.RefObject<HTMLElement | null>, to: number) {
  useEffect(() => {
    if (!ref.current) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) {
      ref.current.textContent = String(to).padStart(2, '0');
      return;
    }
    const from = parseInt(ref.current.textContent ?? '0', 10) || 0;
    if (from === to) return;
    const el = ref.current;
    animate(
      { value: from },
      {
        value: to,
        duration: 440,
        easing: 'easeOutCubic',
        onUpdate: (anim) => {
          const v = Math.round((anim.targets[0] as { value: number }).value);
          el.textContent = String(v).padStart(2, '0');
        },
      },
    );
  }, [to]); // eslint-disable-line react-hooks/exhaustive-deps
}

/**
 * instant: render every row visible at once (no append animation). Negotiate streams one message at a
 * time and animates the newest row in; /cloud mounts a finished transcript in one go, where that
 * animation would leave all but the last row hidden. Default false = Negotiate behaviour unchanged.
 */
export function NegotiationConsole({ messages, instant = false }: { messages: NegotiationMessage[]; instant?: boolean }) {
  const latestRound = messages.reduce((highest, message) => Math.max(highest, message.round), 0);
  const validationCount = messages.filter((message) => message.agent_id === 'validation').length;

  /* ── counter refs ── */
  const recordsRef    = useRef<HTMLElement>(null);
  const roundRef      = useRef<HTMLElement>(null);
  const gatesRef      = useRef<HTMLElement>(null);

  useCountUp(recordsRef, messages.length);
  useCountUp(roundRef, latestRound);
  useCountUp(gatesRef, validationCount);

  /* ── tbody ref: animate newly appended rows ── */
  const tbodyRef = useRef<HTMLTableSectionElement>(null);
  useAnimeAppend(tbodyRef, instant ? 0 : messages.length);

  return (
    <section className="mt-5 border border-border bg-surface-muted/75" aria-labelledby="negotiation-ledger-title">
      <header className="grid gap-4 border-b border-border px-4 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-end sm:px-5">
        <div>
          <div className="flex items-center gap-3">
            <span className={`h-2 w-2 ${messages.length > 0 ? 'bg-success shadow-[0_0_8px_var(--color-success)]' : 'bg-warning'}`} />
            <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-muted">Negotiation event stream</p>
          </div>
          <h3 id="negotiation-ledger-title" className="mt-2 text-lg font-semibold tracking-tight text-text-primary">
            Decision ledger
          </h3>
        </div>
        <dl className="grid grid-cols-3 divide-x divide-border border border-border font-mono text-[10px] uppercase tracking-wider">
          <div className="px-3 py-2">
            <dt className="text-text-muted">records</dt>
            <dd ref={recordsRef} className="mt-1 text-sm text-text-primary">{String(messages.length).padStart(2, '0')}</dd>
          </div>
          <div className="px-3 py-2">
            <dt className="text-text-muted">round</dt>
            <dd ref={roundRef} className="mt-1 text-sm text-text-primary">{String(latestRound).padStart(2, '0')}</dd>
          </div>
          <div className="px-3 py-2">
            <dt className="text-text-muted">gates</dt>
            <dd ref={gatesRef} className="mt-1 text-sm text-warning-light">{String(validationCount).padStart(2, '0')}</dd>
          </div>
        </dl>
      </header>

      <div className="overflow-x-auto">
        <table className="w-full min-w-[980px] table-fixed border-collapse text-left">
          <thead className="bg-surface-secondary/55 font-mono text-[9px] uppercase tracking-[0.16em] text-text-muted">
            <tr>
              <th className="w-14 border-r border-border px-3 py-2.5 font-medium">evt</th>
              <th className="w-24 border-r border-border px-3 py-2.5 font-medium">utc</th>
              <th className="w-16 border-r border-border px-3 py-2.5 font-medium">rnd</th>
              <th className="w-40 border-r border-border px-3 py-2.5 font-medium">origin</th>
              <th className="w-32 border-r border-border px-3 py-2.5 font-medium">decision</th>
              <th className="w-28 border-r border-border px-3 py-2.5 text-right font-medium">yield</th>
              <th className="px-4 py-2.5 font-medium">evidence / narration</th>
            </tr>
          </thead>
          <tbody ref={tbodyRef} className="divide-y divide-border">
            {messages.length === 0 ? (
              <tr>
                <td colSpan={7} className="h-36 px-5 py-8">
                  <div className="flex items-center gap-4">
                    <span className="h-8 w-px bg-warning" />
                    <div>
                      <p className="font-mono text-xs uppercase tracking-wider text-warning-light">Stream armed / no frames</p>
                      <p className="mt-1 text-xs text-text-muted">Start the operator session to populate the immutable decision record.</p>
                    </div>
                  </div>
                </td>
              </tr>
            ) : (
              messages.map((message, index) => {
                const agent = AGENT_LABELS[message.agent_id];
                const isValidation = message.agent_id === 'validation';

                return (
                  <tr
                    key={message.id}
                    className={`align-top transition-colors duration-200 hover:bg-surface-secondary/40 ${isValidation ? 'bg-warning-muted/10' : ''}`}
                    style={instant ? undefined : { opacity: 0 }}
                  >
                    <td className={`border-r border-border px-3 py-4 font-mono text-[10px] ${isValidation ? 'border-l-2 border-l-warning text-warning-light' : 'text-text-muted'}`}>
                      {String(index + 1).padStart(3, '0')}
                    </td>
                    <td className="border-r border-border px-3 py-4 font-mono text-[10px] tabular-nums text-text-secondary">
                      {formatUtc(message.created_at)}
                    </td>
                    <td className="border-r border-border px-3 py-4 font-mono text-xs tabular-nums text-text-primary">
                      {String(message.round).padStart(2, '0')}
                    </td>
                    <td className="border-r border-border px-3 py-4">
                      <span className={`font-mono text-[10px] font-semibold uppercase tracking-wider ${agent.tone}`}>{agent.node}</span>
                      <span className="mt-1 block text-[11px] text-text-muted">{agent.label}</span>
                    </td>
                    <td className="border-r border-border px-3 py-4">
                      <span className={`inline-block border px-2 py-1 font-mono text-[9px] font-semibold uppercase tracking-wider ${ACTION_TONES[message.proposed_action]}`}>
                        {message.proposed_action.replace('_', ' ')}
                      </span>
                    </td>
                    <td className="border-r border-border px-3 py-4 text-right">
                      {message.yield_score === null ? (
                        <span className="font-mono text-xs text-text-muted">N/A</span>
                      ) : (
                        <>
                          <span className="block font-mono text-sm font-semibold tabular-nums text-text-primary">
                            {message.yield_score.toFixed(4)}
                          </span>
                          <span className="mt-1 block font-mono text-[8px] uppercase tracking-wider text-success-light">computed</span>
                        </>
                      )}
                    </td>
                    <td className="px-4 py-4">
                      <p className="max-w-[78ch] text-xs leading-5 text-text-secondary">{message.justification_text}</p>
                      <div className="mt-2 flex items-center gap-3 font-mono text-[8px] uppercase tracking-wider text-text-muted">
                        <span>{isValidation ? 'deterministic safety check' : 'narrative layer'}</span>
                        <span aria-hidden="true" className="h-px w-5 bg-border-light" />
                        <span className="truncate">{message.id}</span>
                      </div>
                    </td>
                  </tr>
                );
              })
            )}
          </tbody>
        </table>
      </div>

      <footer className="flex flex-wrap items-center justify-between gap-2 border-t border-border px-4 py-2.5 font-mono text-[8px] uppercase tracking-[0.15em] text-text-muted sm:px-5">
        <span>append-only event record</span>
        <span>yield scores supplied by cost_functions.py</span>
      </footer>
    </section>
  );
}
