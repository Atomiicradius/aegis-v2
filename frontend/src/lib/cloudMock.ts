/**
 * QA-only scripted stats for /cloud?mock=<scenario>. Never used unless that
 * query param is present. Each scenario is a pure function of elapsed seconds,
 * so every state can be screenshotted/rehearsed without the Docker stack.
 */
import type { CloudStats, CloudWorker } from './cloudApi';

export const MOCK_SCENARIOS = ['idle', 'storm', 'scaled', 'critical', 'dead', 'down', 'chaos'] as const;
export type MockScenario = (typeof MOCK_SCENARIOS)[number];

export function parseMockScenario(value: string | null): MockScenario | null {
  return MOCK_SCENARIOS.find((s) => s === value) ?? null;
}

const IDS = ['a1f3c9d20b11', '7be40c55d3a2', 'c90d1e6f4478', '3d8a52b7e019', 'f2706ac1b8d4', '58e1b39c0a6f', 'd4c7a8f2113e', '09b6e5d7c2a8'];
const PER_WORKER_JOBS_S = 3.2;   // stub 150 ms -> ~0.3 s/job
const STORM_N = 800;

type Shape = {
  t: number;
  alive: number;          // first `alive` workers are alive
  dead?: number[];        // indices of workers that are dead (silent > 10 s)
  depth: number;
  completed: number;
  throughput: number;
  retries?: number;
  critCount?: number;
  policy?: string;
};

function build(s: Shape): CloudStats {
  const dead = new Set(s.dead ?? []);
  const workers: CloudWorker[] = [];
  const n = s.alive + dead.size;
  for (let i = 0; i < n; i += 1) {
    const isDead = dead.has(i);
    workers.push({
      id: IDS[i],
      alive: !isDead,
      last_heartbeat_age_s: isDead ? 12 + (s.t % 60) : 0.4 + ((s.t * 7 + i * 3) % 17) / 10,
      jobs_done: Math.floor(s.completed / Math.max(1, n)) + (i % 3),
    });
  }
  const loaded = s.depth > 0;
  const avg = loaded ? Math.round(2500 + s.completed * 28) : 0;
  return {
    queue_depth: Math.max(0, Math.round(s.depth)),
    processing: Math.min(s.alive, loaded ? s.alive : 0),
    completed_total: Math.round(s.completed),
    failed_total: 0,
    retries_total: s.retries ?? 0,
    workers,
    throughput_per_s: Math.round(s.throughput * 100) / 100,
    latency_ms: { avg, p95: Math.round(avg * 1.9), p99: Math.round(avg * 2.05) },
    critical_latency_ms: {
      avg: s.critCount ? 508 : 0,
      max: s.critCount ? 884 : 0,
      count: s.critCount ?? 0,
    },
    policy: s.policy ?? 'priority',
    llm_mode: 'stub',
    stub_ms: 150,
    pool_size: 400,
    pool_groups: { starlink: 105, oneweb: 105, active: 104, 'iridium-NEXT': 80, stations: 6 },
    server_time: new Date().toISOString(),
  };
}

/** Queue drain with a worker count that can change over time (piecewise, 0.25 s steps). */
function drain(t: number, liveAt: (u: number) => number): { depth: number; completed: number; rate: number } {
  let depth = STORM_N;
  let completed = 0;
  for (let u = 2; u < t; u += 0.25) {
    const done = Math.min(depth, liveAt(u) * PER_WORKER_JOBS_S * 0.25);
    depth -= done;
    completed += done;
  }
  return { depth, completed, rate: depth > 0 ? liveAt(t) * PER_WORKER_JOBS_S : 0 };
}

/** Stats for `scenario` at `t` seconds after the page opened (loops every 90 s). */
export function mockStats(scenario: Exclude<MockScenario, 'down'>, elapsedSeconds: number): CloudStats {
  const t = elapsedSeconds % 90;
  switch (scenario as MockScenario) {
    case 'idle':
      return build({ t, alive: 2, depth: 0, completed: 0, throughput: 0 });

    case 'storm': {
      const burst = Math.min(STORM_N, 400 * t);                       // 800 events land over ~2 s
      const d = drain(Math.max(2, t), () => 2);
      return build({ t, alive: 2, depth: t < 2 ? burst : d.depth, completed: t < 2 ? 0 : d.completed, throughput: t < 1 ? 0 : d.rate });
    }

    case 'scaled': {
      // 2 workers until t=4 s, then one more every 0.6 s up to 8 (autoscaler reaction).
      const live = (u: number) => (u < 4 ? 2 : Math.min(8, 2 + Math.floor((u - 4) / 0.6) + 1));
      const d = drain(Math.max(2, t), live);
      return build({ t, alive: live(t), depth: t < 2 ? Math.min(STORM_N, 400 * t) : d.depth, completed: d.completed, throughput: t < 1 ? 0 : d.rate * 0.97 });
    }

    case 'critical': {
      // Steady 8-worker storm; a burst of critical jobs completes every 8 s.
      const bursts = Math.floor(t / 8) + 1;
      return build({ t, alive: 8, depth: 760 - 25.6 * (t % 30), completed: 40 + 25.6 * t, throughput: 25.4, critCount: bursts * 3 });
    }

    case 'dead': {
      // 4 workers; #2 is killed at t=4 s (dead badge), its job is re-claimed at t=12 s (retry).
      const killed = t >= 4;
      return build({
        t, alive: killed ? 3 : 4, dead: killed ? [3] : [],
        depth: Math.max(0, 640 - 9.6 * t), completed: 120 + 9.6 * t, throughput: 9.6,
        retries: t >= 12 ? 1 : 0, critCount: 2,
      });
    }

    case 'chaos': {
      // 4 workers; chaos_agent kills 2 at t=5 s, redis restarts at t=25 s, workers recover by t=35 s.
      const phase1 = t >= 5 && t < 25;    // 2 workers dead
      const phase2 = t >= 25 && t < 35;   // redis outage
      const alive = phase1 ? 2 : phase2 ? 2 : 4;
      const dead = phase1 ? [2, 3] : phase2 ? [2, 3] : [];
      return build({
        t, alive, dead,
        depth: Math.max(0, 500 - (phase1 ? 4.8 : phase2 ? 0 : 9.6) * t),
        completed: 60 + (phase1 ? 4.8 : phase2 ? 2 : 9.6) * Math.min(t, 50),
        throughput: phase2 ? 0 : alive * 2.4,
        retries: t >= 15 ? Math.floor((t - 15) / 8) + 1 : 0,
        critCount: t >= 10 ? 2 : 0,
        policy: 'priority',
      });
    }
  }
  // fallback (unreachable but satisfies TS exhaustiveness through the cast above)
  return build({ t, alive: 2, depth: 0, completed: 0, throughput: 0 });
}
