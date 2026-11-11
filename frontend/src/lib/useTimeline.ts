/**
 * Polling hook for GET /api/cloud/timeline. Incremental: each poll passes the
 * last seen seq as `since_seq`, so only new events are fetched. The returned
 * array is always newest-first, capped at 200. In mock mode it returns scripted
 * events from cloudMockTimeline.ts instead of hitting the API.
 */
import { useEffect, useRef, useState } from 'react';

import { fetchTimeline, type TimelineEvent } from './cloudApi';
import { mockTimelineEvents } from './cloudMockTimeline';

const POLL_MS = 2000;
const MAX_KEEP = 200;

export function useTimeline(mock: string | null) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const lastSeqRef = useRef(0);
  const startRef = useRef(Date.now());

  useEffect(() => {
    lastSeqRef.current = 0;
    startRef.current = Date.now();
    setEvents([]);

    if (mock) {
      const id = setInterval(() => {
        const elapsed = (Date.now() - startRef.current) / 1000;
        setEvents(mockTimelineEvents(elapsed));
      }, POLL_MS);
      return () => clearInterval(id);
    }

    let cancelled = false;
    const ac = new AbortController();

    async function poll() {
      if (cancelled) return;
      const result = await fetchTimeline(lastSeqRef.current, ac.signal);
      if (cancelled) return;
      if (result.ok && result.data.events.length > 0) {
        lastSeqRef.current = result.data.last_seq;
        setEvents((prev) => {
          const merged = [...result.data.events].reverse().concat(prev);
          return merged.slice(0, MAX_KEEP);
        });
      }
    }

    void poll();
    const id = setInterval(() => void poll(), POLL_MS);
    return () => {
      cancelled = true;
      ac.abort();
      clearInterval(id);
    };
  }, [mock]);

  return events;
}
