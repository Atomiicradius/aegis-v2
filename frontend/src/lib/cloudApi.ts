/**
 * Typed client for the cloud stats/control API (backend/cloud/api.py, see CLOUD.md).
 * Every wrapper resolves to a Result and never throws, so the UI can render
 * a "link down" state instead of crashing.
 */

import type { NegotiationMessage, Resolution } from '../components/globe/types';

export const CLOUD_API: string = (
  import.meta.env.VITE_CLOUD_API ?? 'http://localhost:8010'
).replace(/\/$/, '');

/** Mirrors GET /api/cloud/stats exactly (keys are a fixed contract). */
export type CloudWorker = {
  id: string;
  last_heartbeat_age_s: number;
  alive: boolean;
  jobs_done: number;
};

export type CloudStats = {
  queue_depth: number;
  processing: number;
  completed_total: number;
  failed_total: number;
  retries_total: number;
  workers: CloudWorker[];
  throughput_per_s: number;
  latency_ms: { avg: number; p95: number; p99: number };
  critical_latency_ms: { avg: number; max: number; count: number };
  policy: string;
  llm_mode: string;
  stub_ms: number;
  server_time: string;
  /** real satellites the storm draws from; absent on an older API */
  pool_size?: number;
  pool_groups?: Record<string, number>;
};

/** One row of GET /api/cloud/recent (a finished job; summaries only, no transcript). */
export type CloudRecentRow = {
  event_id: string;
  primary_id: string;
  secondary_id: string;
  primary_name: string;
  secondary_name: string;
  miss_km: number;
  risk: number;
  is_critical: boolean;
  rounds: number;
  resolution_status: string;
  maneuvering_agent: string;
  delta_v_mps: number;
  latency_ms: number;
  finished_at: string;
  /** retries + 1; present on the /api/cloud/recent response from recent_summaries_ext */
  attempts?: number;
};

/** One attempt inside a trace (from GET /api/cloud/trace/{id}). */
export type TraceAttempt = {
  n: number;
  worker_id: string;
  leased_at: number;
  lease_deadline: number;
  outcome: string;
  /** ms from perf_counter start to end of job processing (0 when lease_expired with no data) */
  process_end_ms: number;
  /** ms spent in engine.run_negotiation() — the NEGOTIATE segment */
  negotiate_ms: number;
};

/** Full trace from GET /api/cloud/trace/{id} (trace.py structure). */
export type CloudTrace = {
  event_id: string;
  enqueued_at: string;
  risk: number;
  is_critical: boolean;
  attempts: TraceAttempt[];
  completed_at: string;
  final_status: string;
};

/** GET /api/cloud/resolution/{id}: same shapes the Negotiate page renders. */
export type CloudResolution = {
  event_id: string;
  summary: CloudRecentRow;
  transcript: NegotiationMessage[];
  resolution: Resolution;
};

/** Error string of a 404 from /resolution: the transcript expired (2 h TTL) or never existed. */
export const EXPIRED = 'HTTP 404';

export type StormPolicy = 'fifo' | 'priority';

export type StormRequest = {
  n: number;
  critical: number;
  critical_after_s: number;
  policy: StormPolicy;
};

export type Result<T> = { ok: true; data: T } | { ok: false; error: string };

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null;
}

/** Cheap structural check so a wrong/partial payload becomes an error state, not a render crash. */
function isCloudStats(value: unknown): value is CloudStats {
  if (!isRecord(value)) return false;
  return (
    typeof value.queue_depth === 'number' &&
    typeof value.processing === 'number' &&
    typeof value.completed_total === 'number' &&
    Array.isArray(value.workers) &&
    isRecord(value.latency_ms) &&
    isRecord(value.critical_latency_ms)
  );
}

async function request(path: string, init?: RequestInit): Promise<Result<unknown>> {
  try {
    const response = await fetch(`${CLOUD_API}${path}`, { cache: 'no-store', ...init });
    if (!response.ok) return { ok: false, error: `HTTP ${response.status}` };
    return { ok: true, data: (await response.json()) as unknown };
  } catch (error) {
    return { ok: false, error: error instanceof Error ? error.message : 'network error' };
  }
}

function post(path: string, body: unknown): Promise<Result<unknown>> {
  return request(path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
}

export async function fetchCloudStats(signal?: AbortSignal): Promise<Result<CloudStats>> {
  const result = await request('/api/cloud/stats', { signal });
  if (!result.ok) return result;
  return isCloudStats(result.data)
    ? { ok: true, data: result.data }
    : { ok: false, error: 'unexpected stats payload' };
}

export async function fetchCloudRecent(limit = 10, signal?: AbortSignal): Promise<Result<CloudRecentRow[]>> {
  const result = await request(`/api/cloud/recent?limit=${limit}`, { signal });
  if (!result.ok) return result;
  return Array.isArray(result.data)
    ? { ok: true, data: result.data as CloudRecentRow[] }
    : { ok: false, error: 'unexpected recent payload' };
}

export async function fetchCloudResolution(eventId: string, signal?: AbortSignal): Promise<Result<CloudResolution>> {
  const result = await request(`/api/cloud/resolution/${encodeURIComponent(eventId)}`, { signal });
  if (!result.ok) return result;
  const d = result.data;
  return isRecord(d) && Array.isArray(d.transcript) && isRecord(d.resolution)
    ? { ok: true, data: d as unknown as CloudResolution }
    : { ok: false, error: 'unexpected resolution payload' };
}

export async function fetchCloudTrace(eventId: string, signal?: AbortSignal): Promise<Result<CloudTrace>> {
  const result = await request(`/api/cloud/trace/${encodeURIComponent(eventId)}`, { signal });
  if (!result.ok) return result;
  const d = result.data;
  return isRecord(d) && Array.isArray((d as Record<string, unknown>).attempts)
    ? { ok: true, data: d as unknown as CloudTrace }
    : { ok: false, error: 'unexpected trace payload' };
}

export const postStorm = (req: StormRequest) => post('/api/cloud/storm', req);
export const postCritical = (k: number) => post('/api/cloud/critical', { k });
export const postReset = () => post('/api/cloud/reset', {});
