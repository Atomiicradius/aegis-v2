import { useEffect, useState } from 'react';

import { fetchCloudTrace, type CloudTrace, type Result } from './cloudApi';
import type { MockScenario } from './cloudMock';
import { mockTrace } from './cloudMockRecords';

export type TraceState = { status: 'idle' | 'loading' | 'done'; result: Result<CloudTrace> | null };

/** Fetches the execution trace for one job. Returns mock data for any mock scenario. */
export function useTrace(eventId: string | null, mock: MockScenario | null): TraceState {
  const [state, setState] = useState<TraceState>({ status: 'idle', result: null });

  useEffect(() => {
    if (!eventId) {
      setState({ status: 'idle', result: null });
      return;
    }
    let cancelled = false;
    const controller = new AbortController();
    setState({ status: 'loading', result: null });
    const load = mock
      ? Promise.resolve(mockTrace(eventId))
      : fetchCloudTrace(eventId, controller.signal);
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
