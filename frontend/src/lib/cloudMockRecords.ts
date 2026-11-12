/**
 * QA-only scripted resolution records for /cloud?mock=<scenario> (companion to cloudMock.ts).
 * Rows are a pure function of (scenario, elapsed seconds); a transcript is a pure function of the row id,
 * so a record opened in the panel stays openable while its row scrolls out of the table, exactly like the
 * real API. The `critical` and `dead` scenarios also pin one old critical row whose transcript is
 * EXPIRED (404), to exercise that state.
 */
import type { NegotiationMessage, Resolution } from '../components/globe/types';
import { EXPIRED, type CloudRecentRow, type CloudResolution, type CloudTrace, type DomainBlock, type RaceLane, type RacePair, type RaceResult, type Result } from './cloudApi';
import type { MockScenario } from './cloudMock';

const RATE = 2; // rows finished per second
const SATS: [string, string][] = [
  ['STARLINK-1007', '44713'], ['ONEWEB-0149', '45131'], ['IRIDIUM 106', '41917'], ['KUIPER-00110', '63041'],
  ['STARLINK-30331', '58214'], ['IRIDIUM 128', '43573'], ['ONEWEB-0693', '48094'], ['FLOCK 4P-23', '47511'],
  ['STARLINK-2226', '48382'], ['GLOBALSTAR M089', '39075'], ['LEMUR-2-KAITLYN', '44414'], ['QIANFAN-79', '60418'],
];
const EXPIRED_ID = 'e0000000-0000-4000-8000-000000000001';
const idOf = (seq: number) => `00000000-0000-4000-8000-${String(seq).padStart(12, '0')}`;
const seqOf = (id: string) => parseInt(id.slice(-12), 10);
/** Cheap deterministic 0..1 value from (seq, salt). */
const rnd = (seq: number, salt: number) => ((Math.sin(seq * 12.9898 + salt * 78.233) * 43758.5453) % 1 + 1) % 1;

function isCritical(scenario: MockScenario, seq: number): boolean {
  if (scenario === 'critical') return seq % 6 === 3 || seq % 6 === 4;
  if (scenario === 'dead') return seq % 8 === 3;
  return false;
}

function rowFor(scenario: MockScenario, seq: number, ageS: number): CloudRecentRow {
  const critical = isCritical(scenario, seq);
  const [pn, pid] = SATS[seq % SATS.length];
  const [sn, sid] = SATS[(seq * 5 + 3) % SATS.length];
  const failed = !critical && seq % 17 === 5;
  const twoRounds = !critical && seq % 9 === 4;
  const miss = critical ? 0.3 + rnd(seq, 1) * 0.3 : 0.6 + rnd(seq, 1) * 4.2;
  const risk = critical ? 0.82 + rnd(seq, 2) * 0.09 : 0.14 + rnd(seq, 2) * 0.42;
  // seq % 9 === 4 → twoRounds (needs a retry); seq % 7 === 2 → also retried (extra variety)
  const retried = twoRounds || (!critical && seq % 7 === 2);
  // tca_h: hours from enqueue to TCA — varies 1-24 h, deterministic per seq
  const tca_h = Math.round((1 + rnd(seq, 10) * 23) * 100) / 100;
  return {
    event_id: idOf(seq), primary_id: pid, secondary_id: sid, primary_name: pn, secondary_name: sn,
    miss_km: Math.round(miss * 100) / 100, risk: Math.round(risk * 1000) / 1000, is_critical: critical,
    rounds: failed ? 3 : twoRounds ? 2 : 1,
    resolution_status: failed ? 'no_safe_maneuver_found' : 'approved',
    maneuvering_agent: failed ? 'none' : seq % 2 ? 'operator_A' : 'operator_B',
    delta_v_mps: failed ? 0 : Math.round((1.5 + rnd(seq, 3) * 2.5) * 10) / 10,
    latency_ms: critical ? Math.round(500 + rnd(seq, 4) * 400) : Math.round(2500 + rnd(seq, 4) * 21000),
    finished_at: new Date(Date.now() - ageS * 1000).toISOString(),
    attempts: retried ? 2 : 1,
    tca_h,
  };
}

/** What GET /api/cloud/recent?limit=10 would return `elapsedSeconds` after the page opened. */
export function mockRecent(scenario: MockScenario, elapsedSeconds: number): CloudRecentRow[] {
  if (scenario === 'idle' || scenario === 'down') return [];
  const newest = Math.floor(elapsedSeconds * RATE) + 12;
  const rows = Array.from({ length: 10 }, (_, i) => rowFor(scenario, newest - i, i / RATE));
  if (scenario === 'critical' || scenario === 'dead') {
    // the real API pins older critical rows at the bottom; this one's transcript has expired
    rows[9] = { ...rowFor('critical', 3, 300), event_id: EXPIRED_ID, primary_name: 'IRIDIUM 112', secondary_name: 'STARLINK-37372' };
  }
  return rows;
}

function message(row: CloudRecentRow, agent: NegotiationMessage['agent_id'], round: number, action: NegotiationMessage['proposed_action'],
                 yieldScore: number | null, text: string, k: number): NegotiationMessage {
  return {
    id: `${row.event_id.slice(0, 8)}-msg-${k}`, conjunction_id: row.event_id, agent_id: agent, round, yield_score: yieldScore,
    justification_text: text, proposed_action: action, created_at: new Date(Date.parse(row.finished_at) - (9 - k) * 12).toISOString(),
  };
}

/** Transcript + resolution for a mock row (same shapes as the real endpoint). */
export function mockResolution(scenario: MockScenario, id: string): Result<CloudResolution> {
  if (id === EXPIRED_ID) return { ok: false, error: EXPIRED };
  const row = id === EXPIRED_ID ? rowFor('critical', 3, 300) : rowFor(scenario, seqOf(id), 0);
  const seq = seqOf(id);
  const yA = Math.round((0.45 + rnd(seq, 5) * 0.4) * 1e4) / 1e4;
  const yB = Math.round((0.45 + rnd(seq, 6) * 0.4) * 1e4) / 1e4;
  const mover = row.maneuvering_agent === 'operator_A' ? row.primary_name : row.secondary_name;
  const msgs: NegotiationMessage[] = [];
  const dv = row.delta_v_mps.toFixed(1);
  msgs.push(message(row, 'operator_A', 1, 'maneuver', yA, `${row.primary_name} (MVI 0.4) is prepared to execute the avoidance maneuver. With a fuel margin of 63% and an estimated Δv of ${dv} m/s, the computed yield score of ${yA.toFixed(2)} supports this operator yielding.`, 1));
  msgs.push(message(row, 'operator_B', 1, 'stand_down', yB, `${row.secondary_name} (MVI 0.5) is holding position and requests the counterpart to maneuver. Current fuel margin is 67%.`, 2));
  if (row.rounds >= 2) {
    msgs.push(message(row, 'validation', 1, 'reject', null, `Secondary conjunction risk: ${SATS[(seq + 4) % SATS.length][0]} comes within 3.21 km (5.0 km threshold) at T+40 min after maneuver.`, 3));
    msgs.push(message(row, 'operator_B', 2, 'maneuver', yB, `${row.secondary_name} agrees to maneuver in round 2 under the secondary-conjunction constraint (Δv ${dv} m/s).`, 4));
  }
  const ok = row.resolution_status === 'approved';
  msgs.push(message(row, 'validation', row.rounds, ok ? 'approve' : 'reject', null, ok
    ? `Maneuver validated. No secondary conjunctions found in the 6-hour lookahead window. Closest third-party object: ${SATS[(seq + 7) % SATS.length][0]} at 472.26 km (threshold: 5.0 km). Expected post-maneuver miss distance: ${(row.miss_km + 3).toFixed(2)} km.`
    : 'No maneuver passes the safety gate within the negotiation round limit. Escalating to operator review.', 5));
  const resolution: Resolution = {
    id: `${row.event_id.slice(0, 8)}-res`, conjunction_id: row.event_id, maneuvering_agent: row.maneuvering_agent,
    maneuver_type: ok ? 'prograde_burn' : 'none', delta_v_mps: row.delta_v_mps,
    execution_time_utc: new Date(Date.parse(row.finished_at) + 6 * 3600e3).toISOString().replace(/\.\d+Z$/, '+00:00'),
    expected_min_distance_km: Math.round((row.miss_km + 3) * 1000) / 1000,
    residual_risk: Math.round((row.miss_km + 3) * 1000) / 1000,
    rationale_text: ok ? `${mover} agreed to maneuver in round ${row.rounds} (yield_score ${yB.toFixed(2)}). Validation agent confirmed no secondary risks in 6h lookahead.`
      : 'No safe maneuver found after the maximum number of negotiation rounds.',
    status: ok ? 'approved' : 'no_safe_maneuver_found',
  };
  return { ok: true, data: { event_id: row.event_id, summary: row, transcript: msgs, resolution } };
}

function mockDomain(seq: number, row: CloudRecentRow): DomainBlock {
  const tca_h = row.tca_h ?? (1 + rnd(seq, 10) * 23);
  const miss = row.miss_km;
  const closeness = Math.round(Math.max(0, Math.min(1, 1 - miss / 5)) * 1000) / 1000;
  const urgency = Math.round(Math.max(0, Math.min(1, 1 - tca_h / 24)) * 1000) / 1000;
  const mission = Math.round((0.4 + rnd(seq, 11) * 0.5) * 1000) / 1000;
  const [pn, pid] = SATS[seq % SATS.length];
  const [sn, sid] = SATS[(seq * 5 + 3) % SATS.length];
  return {
    a: { norad: pid, name: pn, group: 'starlink', operator: 'SpaceX Starlink' },
    b: { norad: sid, name: sn, group: 'oneweb', operator: 'OneWeb / Eutelsat' },
    miss_km: miss,
    tca_utc: new Date(Date.now() + tca_h * 3600e3).toISOString(),
    lead_h: Math.round(tca_h * 100) / 100,
    risk: row.risk,
    risk_parts: { closeness, urgency, mission },
    is_critical: row.is_critical,
    maneuvering_norad: row.maneuvering_agent === 'operator_A' ? pid : sid,
    delta_v_mps: row.delta_v_mps,
    residual_km: Math.round((miss + 3) * 100) / 100,
    validation_clear: row.resolution_status === 'approved',
  };
}

/**
 * Mock trace for any mock scenario.
 * - Rows whose seq matches the retried pattern → 2 attempts (lease_expired + completed).
 * - All other rows → 1 attempt (clean, completed).
 * This matches the attempts field set by rowFor() so the table badge and waterfall agree.
 */
export function mockTrace(eventId: string): Result<CloudTrace> {
  const now = Date.now() / 1000;
  const seq = seqOf(eventId);
  const twoRounds = seq % 9 === 4;
  const retriedExtra = seq % 7 === 2;
  const retried = twoRounds || retriedExtra;

  const negotiateMs = 38 + rnd(seq, 8) * 20;    // 38–58 ms
  const processMs = negotiateMs + 7 + rnd(seq, 9) * 6;  // validate = 7–13 ms

  const cleanAttempt = {
    n: retried ? 2 : 1,
    worker_id: `cafef00d${String(seq).padStart(4, '0')}`,
    leased_at: now - 15,
    lease_deadline: now - 5,
    outcome: 'completed' as const,
    process_end_ms: Math.round(processMs * 10) / 10,
    negotiate_ms: Math.round(negotiateMs * 10) / 10,
  };

  const mockRow = rowFor('storm', seq, 0);
  return {
    ok: true,
    data: {
      event_id: eventId,
      enqueued_at: new Date((now - 30) * 1000).toISOString(),
      risk: Math.round((0.4 + rnd(seq, 7) * 0.5) * 100) / 100,
      is_critical: false,
      attempts: retried
        ? [
            {
              n: 1,
              worker_id: `deadbeef${String(seq).padStart(4, '0')}`,
              leased_at: now - 28,
              lease_deadline: now - 18,
              outcome: 'lease_expired' as const,
              process_end_ms: 0,
              negotiate_ms: 0,
            },
            cleanAttempt,
          ]
        : [cleanAttempt],
      completed_at: new Date((now - 15 + processMs / 1000) * 1000).toISOString(),
      final_status: 'approved',
      domain: mockDomain(seq, mockRow),
    },
  };
}

// ---------------------------------------------------------------------------
// Block 5 — FIFO vs priority race mock data
// ---------------------------------------------------------------------------

const RACE_PAIRS: [string, string][] = [
  ['STARLINK-6085', 'IRIDIUM 156'],
  ['ONEWEB-0149', 'FLOCK 4P-23'],
  ['KUIPER-00110', 'STARLINK-30331'],
  ['IRIDIUM 128', 'GLOBALSTAR M089'],
  ['LEMUR-2-KAITLYN', 'QIANFAN-79'],
];

function mockRacePair(idx: number, policy: 'fifo' | 'prio'): RacePair {
  const [pn, sn] = RACE_PAIRS[idx % RACE_PAIRS.length];
  const lead_h = Math.round((0.3 + rnd(idx, 11) * 0.9) * 100) / 100; // 0.3..1.2 h
  const latMs = Math.round(140 + rnd(idx, 12) * 60); // 140-200 ms (processing time)
  // resolution = wall-clock: fifo jobs wait behind ~200 normal jobs; prio jumps to front
  const resMs = policy === 'fifo'
    ? Math.round((200 + idx * 20) * latMs * 0.01 + latMs) // long queue wait
    : Math.round(idx * latMs * 0.3 + latMs);              // near-front
  return {
    event_id: idOf(1000 + idx),
    primary_name: pn,
    secondary_name: sn,
    risk: Math.round((0.82 + rnd(idx, 14) * 0.09) * 1000) / 1000,
    lead_h,
    resolution_ms: resMs,
    latency_ms: latMs,
    lane: policy,
  };
}

function mockRaceLane(policy: 'fifo' | 'prio'): RaceLane {
  const n = 300;
  const critical = 3;
  const jobMs = 150;
  const drainMs = policy === 'fifo'
    ? Math.round((n + critical) * jobMs / 2 + 1000) // 2 workers in parallel
    : Math.round((n + critical) * jobMs / 2 + 900);

  const pairs: RacePair[] = Array.from({ length: critical }, (_, i) => mockRacePair(i, policy));
  const critResAvg = Math.round(pairs.reduce((s, p) => s + p.resolution_ms, 0) / pairs.length);
  const critResMax = Math.max(...pairs.map((p) => p.resolution_ms));
  const critLatAvg = Math.round(pairs.reduce((s, p) => s + p.latency_ms, 0) / pairs.length);
  const critLatMax = Math.max(...pairs.map((p) => p.latency_ms));

  return {
    policy,
    total_jobs: n + critical,
    critical_jobs: critical,
    failed_jobs: 0,
    critical_resolution_ms: { avg: critResAvg, max: critResMax },
    critical_latency_ms: { avg: critLatAvg, max: critLatMax },
    critical_pairs: pairs,
    drain_ms: drainMs,
  };
}

/** Mock race result for ?mock=race. */
export function mockRaceResult(): RaceResult {
  return {
    fifo: mockRaceLane('fifo'),
    priority: mockRaceLane('prio'),
    n: 300,
    critical: 3,
    seed: 42,
    enqueued_at: new Date(Date.now() - 55_000).toISOString(),
    start_skew_ms: 12,
    total: 303,
  };
}
