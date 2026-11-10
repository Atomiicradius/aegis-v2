/**
 * Typed client for the cloud stats/control API (backend/cloud/api.py, see CLOUD.md).
 * Every wrapper resolves to a Result and never throws, so the UI can render
 * a "link down" state instead of crashing.
 */

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

export const postStorm = (req: StormRequest) => post('/api/cloud/storm', req);
export const postCritical = (k: number) => post('/api/cloud/critical', { k });
export const postReset = () => post('/api/cloud/reset', {});
