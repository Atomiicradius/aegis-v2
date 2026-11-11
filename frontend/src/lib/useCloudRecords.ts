import { useEffect, useRef, useState } from 'react';

import {
  fetchCloudRecent, fetchCloudResolution, type CloudRecentRow, type CloudResolution, type Result,
} from './cloudApi';
import type { MockScenario } from './cloudMock';
import { mockRecent, mockResolution } from './cloudMockRecords';

const POLL_MS = 2000;
const LIMIT = 10;

export type RecordsState = { rows: CloudRecentRow[]; ok: boolean };

/**
 * Polls GET /api/cloud/recent every 2 s (paused while the tab is hidden). A failed poll keeps the last rows
 * and flips `ok` to false; the next good poll flips it back. With `?mock=` the rows are scripted instead.
 */
export function useCloudRecent(mock: MockScenario | null, paused = false): RecordsState {
  const [state, setState] = useState<RecordsState>({ rows: [], ok: true });
  // While the user points at the table the rows must not move under the cursor: skip polls (resume on leave).
  const pausedRef = useRef(paused);
  pausedRef.current = paused;

  useEffect(() => {
    let cancelled = false;
    let busy = false;
    const started = Date.now();
    const controller = new AbortController();
    setState({ rows: [], ok: true });

    const tick = async () => {
      if (document.hidden || busy || pausedRef.current) return;
      busy = true;
      try {
        if (mock) {
          if (!cancelled) setState({ rows: mockRecent(mock, (Date.now() - started) / 1000), ok: mock !== 'down' });
          return;
        }
        const result = await fetchCloudRecent(LIMIT, controller.signal);
        if (cancelled) return;
        setState((prev) => (result.ok ? { rows: result.data, ok: true } : { rows: prev.rows, ok: false }));
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

  // refresh straight away when the pause ends, instead of waiting for the next 2 s tick
  const wasPaused = useRef(paused);
  useEffect(() => {
    if (wasPaused.current && !paused) document.dispatchEvent(new Event('visibilitychange'));
    wasPaused.current = paused;
  }, [paused]);

  return state;
}

export type ResolutionState = { status: 'idle' | 'loading' | 'done'; result: Result<CloudResolution> | null };

/** Fetches one transcript when `eventId` changes (once: a record never changes after it is stored). */
export function useCloudResolution(eventId: string | null, mock: MockScenario | null): ResolutionState {
  const [state, setState] = useState<ResolutionState>({ status: 'idle', result: null });

  useEffect(() => {
    if (!eventId) {
      setState({ status: 'idle', result: null });
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setState({ status: 'loading', result: null });
    const load = mock
      ? Promise.resolve(mockResolution(mock, eventId))
      : fetchCloudResolution(eventId, controller.signal);
    void load.then((result) => {
      if (!cancelled) setState({ status: 'done', result });
    });
    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [eventId, mock]);

  return state;
}
