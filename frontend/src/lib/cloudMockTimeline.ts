/**
 * Scripted timeline events for ?mock=chaos (and any other mock scenario that
 * needs a timeline). Pure function of elapsed seconds; loops every 90 s.
 */
import type { TimelineEvent } from './cloudApi';

function ev(seq: number, tsOffset: number, type: string, source: string, detail: Record<string, unknown>): TimelineEvent {
  return { seq, ts: Date.now() / 1000 - tsOffset, type, source, detail };
}

/** Scripted timeline events for the chaos mock scenario. Newest first, capped at 20. */
export function mockTimelineEvents(elapsedSeconds: number): TimelineEvent[] {
  const t = elapsedSeconds % 90;
  const events: TimelineEvent[] = [];

  // Always: storm started at t=0 of this loop
  events.push(ev(1, Math.max(0, t), 'storm_started', 'api', { n: 500, policy: 'priority', critical: 0 }));

  if (t >= 2) events.push(ev(2, t - 2, 'scale_up', 'autoscaler', { from: 2, to: 4, depth: 500, processing: 2 }));
  if (t >= 5) events.push(ev(3, t - 5, 'chaos_fault', 'chaos_agent', { scenario: 'kill2', label: 'kill 2 of 4 workers', kind: 'worker_kill' }));
  if (t >= 7) events.push(ev(4, t - 7, 'lease_expired', 'worker', { event_id: '00000000-0000-4000-8000-000000000042' }));
  if (t >= 8) events.push(ev(5, t - 8, 'job_requeued', 'worker', { event_id: '00000000-0000-4000-8000-000000000042', attempt: 2 }));
  if (t >= 12) events.push(ev(6, t - 12, 'chaos_recover', 'chaos_agent', { scenario: 'kill2', label: 'kill 2 of 4 workers' }));
  if (t >= 25) events.push(ev(7, t - 25, 'chaos_fault', 'chaos_agent', { scenario: 'redis_kill', label: 'hard redis kill', kind: 'redis_kill' }));
  if (t >= 25) events.push(ev(8, t - 25, 'redis_down', 'worker', {}));
  if (t >= 27) events.push(ev(9, t - 27, 'redis_up', 'worker', { outage_started_ts: Date.now() / 1000 - (t - 25) }));
  if (t >= 28) events.push(ev(10, t - 28, 'chaos_recover', 'chaos_agent', { scenario: 'redis_kill' }));
  if (t >= 35) events.push(ev(11, t - 35, 'scale_up', 'autoscaler', { from: 2, to: 4, depth: 200, processing: 2 }));
  if (t >= 60) events.push(ev(12, t - 60, 'scale_down', 'autoscaler', { from: 4, to: 3, waited_s: 10.2, depth: 50 }));
  if (t >= 70) events.push(ev(13, t - 70, 'scale_down', 'autoscaler', { from: 3, to: 2, waited_s: 10.1, depth: 0 }));

  // Sort newest-first by seq desc, return up to 20
  return events.sort((a, b) => b.seq - a.seq).slice(0, 20);
}
