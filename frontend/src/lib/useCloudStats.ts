import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';

import { fetchCloudStats, type CloudStats, type Result } from './cloudApi';
import { mockStats, parseMockScenario, type MockScenario } from './cloudMock';

const POLL_MS = 1000;
const HISTORY_LEN = 120;

/** One poll, reduced to what the sparklines need. */
export type CloudSample = {
  t: number;
  depth: number;
  throughput: number;
  live: number;
  processing: number;
};

export type CloudStatsState = {
  stats: CloudStats | null;
  history: CloudSample[];
  /** false after a failed poll; flips back on the next success */
  linkOk: boolean;
  lastError: string | null;
  /** non-null only when the URL has ?mock=<scenario> */
  mock: MockScenario | null;
};

const EMPTY: Omit<CloudStatsState, 'mock'> = { stats: null, history: [], linkOk: false, lastError: null };

/**
 * Polls GET /api/cloud/stats once a second (paused while the tab is hidden) and
 * keeps the last 120 samples. Cloud state is page-local on purpose: it is not
 * part of the negotiation store. `?mock=<scenario>` swaps the fetch for scripted
 * data (QA/rehearsal only); without that param the mock code path never runs.
 */
export function useCloudStats(): CloudStatsState {
  const [params] = useSearchParams();
  const mock = parseMockScenario(params.get('mock'));
  const [state, setState] = useState<Omit<CloudStatsState, 'mock'>>(EMPTY);

  useEffect(() => {
    let cancelled = false;
    let busy = false;
    const started = Date.now();
    const controller = new AbortController();
    setState(EMPTY);

    const apply = (result: Result<CloudStats>) => {
      if (cancelled) return;
      setState((prev) => {
        if (!result.ok) return { ...prev, linkOk: false, lastError: result.error };
        const s = result.data;
        const sample: CloudSample = {
          t: Date.now(),
          depth: s.queue_depth,
          throughput: s.throughput_per_s,
          live: s.workers.filter((w) => w.alive).length,
          processing: s.processing,
        };
        return {
          stats: s,
          history: [...prev.history, sample].slice(-HISTORY_LEN),
          linkOk: true,
          lastError: null,
        };
      });
    };

    const tick = async () => {
      if (document.hidden || busy) return;
      busy = true;
      try {
        if (mock === 'down') apply({ ok: false, error: 'mock: link down' });
        else if (mock) apply({ ok: true, data: mockStats(mock, (Date.now() - started) / 1000) });
        else apply(await fetchCloudStats(controller.signal));
      } finally {
        busy = false;
      }
    };

    void tick();
    const timer = window.setInterval(() => void tick(), POLL_MS);
    const onVisible = () => {
      if (!document.hidden) void tick();
    };
    document.addEventListener('visibilitychange', onVisible);

    return () => {
      cancelled = true;
      controller.abort();
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', onVisible);
    };
  }, [mock]);

  return { ...state, mock };
}
