import { useRef, useState } from 'react';

import { ClusterConstellation } from '../components/cloud/ClusterConstellation';
import { CornerBrackets } from '../components/cloud/CornerBrackets';
import { LeftRuleCallout } from '../components/cloud/LeftRuleCallout';
import { PanelHeader } from '../components/cloud/PanelHeader';
import { Sparkline } from '../components/cloud/Sparkline';
import { StatCell } from '../components/cloud/StatCell';
import { StatusChip } from '../components/cloud/StatusChip';
import { TONE_TEXT, type Tone } from '../components/cloud/tone';
import { postCritical, postReset, postStorm, type Result, type StormPolicy } from '../lib/cloudApi';
import { useAnimeEntry } from '../lib/useAnimeEntry';
import { useAnimeStagger } from '../lib/useAnimeStagger';
import { useCloudStats } from '../lib/useCloudStats';

const pad = (n: number, w: number) => String(Math.max(0, Math.round(n))).padStart(w, '0');
const DASH = '--';

/** Hide pre-animation content only when motion is allowed (reduced-motion users get the final state). */
const MOTION_OK = typeof window !== 'undefined' && !window.matchMedia('(prefers-reduced-motion: reduce)').matches;
const HIDDEN = MOTION_OK ? { opacity: 0 } : undefined;

type Action = { label: string; run: () => Promise<Result<unknown>>; confirm?: string };

export default function Cloud() {
  const { stats, history, linkOk, lastError, mock } = useCloudStats();
  const [busy, setBusy] = useState<string | null>(null);
  const [lastAction, setLastAction] = useState('no action yet');

  const leftRef = useRef<HTMLDivElement>(null);
  const scopeRef = useRef<HTMLElement>(null);
  const lowerRef = useRef<HTMLDivElement>(null);
  useAnimeStagger(leftRef, [], { translateY: 14, staggerDelay: 70 });
  useAnimeEntry(scopeRef, true, { duration: 520, delay: 160, translateY: 10 });
  useAnimeStagger(lowerRef, [], { staggerDelay: 90, duration: 420 });

  const s = stats;
  const alive = s ? s.workers.filter((w) => w.alive).length : 0;
  const dead = s ? s.workers.length - alive : 0;
  const adv =
    s && s.critical_latency_ms.count > 0 && s.critical_latency_ms.avg > 0 && s.latency_ms.avg > 0
      ? s.latency_ms.avg / s.critical_latency_ms.avg
      : null;
  const n = (v: number | undefined, w: number) => (s && v !== undefined ? pad(v, w) : DASH);

  async function run(action: Action) {
    if (busy) return;
    if (action.confirm && !window.confirm(action.confirm)) return;
    if (mock) {
      setLastAction(`${action.label} / mock mode, no request sent`);
      return;
    }
    setBusy(action.label);
    const result = await action.run();
    setBusy(null);
    const at = new Date().toISOString().slice(11, 19);
    setLastAction(`${action.label} / ${result.ok ? 'ok' : `failed (${result.error})`} / ${at}Z`);
  }

  const storm = (policy: StormPolicy): Action => ({
    label: `storm 800 ${policy}`,
    run: () => postStorm({ n: 800, critical: 0, critical_after_s: 8, policy }),
  });
  const actions: { key: string; action: Action; style: 'primary' | 'secondary' | 'warning' | 'danger'; text: string }[] = [
    { key: 'sp', action: storm('priority'), style: 'primary', text: 'Storm 800 / priority' },
    { key: 'sf', action: storm('fifo'), style: 'secondary', text: 'Storm 800 / fifo' },
    { key: 'ic', action: { label: 'inject 3 critical', run: () => postCritical(3) }, style: 'warning', text: 'Inject 3 critical' },
    { key: 'rs', action: { label: 'reset', run: postReset, confirm: 'Flush Redis and wipe all queue state?' }, style: 'danger', text: 'Reset' },
  ];
  const BTN = {
    primary: 'bg-accent text-white hover:bg-accent-dark',
    secondary: 'border border-border-light text-text-secondary hover:border-accent hover:text-text-primary',
    warning: 'border border-warning/50 text-warning-light hover:border-warning hover:bg-warning-muted/40',
    danger: 'border border-danger/45 text-danger-light hover:border-danger hover:bg-danger-muted/40',
  } as const;

  const linkTone: Tone = linkOk ? 'success' : 'danger';

  return (
    <main className="ops-grid min-h-[calc(100dvh-4rem)] bg-background px-4 py-5 sm:px-6 lg:px-8">
      <div className="relative mx-auto max-w-[1600px]">
        <div className="scan-beam" aria-hidden="true" />

        {/* ── header strip (Monitor style) ── */}
        <header className="relative grid gap-3 border border-border bg-background/88 px-5 py-3 backdrop-blur-sm lg:grid-cols-[minmax(0,1fr)_auto] lg:items-center">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[14px] uppercase tracking-[0.16em]">
            <span className="font-semibold text-text-primary">Cluster control room</span>
            <span className="hidden h-4 w-px bg-border 2xl:block" />
            <span className="hidden text-text-muted 2xl:inline">sector / single-host compose</span>
            <span className="hidden text-text-muted 2xl:inline">queue / redis</span>
            {mock && <span className="border border-warning bg-warning-muted/60 px-2.5 py-0.5 font-semibold text-warning-light animate-pulse">MOCK DATA / {mock}</span>}
          </div>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2 font-mono text-[14px] uppercase tracking-[0.14em]">
            <span className="text-text-muted">policy / <span className="text-text-primary">{s?.policy ?? DASH}</span></span>
            <span className="border-l border-border pl-4 text-text-muted">
              llm / <span className="text-text-primary">{s ? `${s.llm_mode}${s.llm_mode === 'stub' ? ` ${s.stub_ms} ms` : ''}` : DASH}</span>
            </span>
            <span className="border-l border-border pl-4">
              <StatusChip tone={linkTone} label={`link / ${linkOk ? 'online' : 'down'}`} pulse={!linkOk} />
            </span>
          </div>
        </header>

        {!linkOk && <LinkDown hasData={s !== null} error={lastError} />}

        {/* ── hero ── */}
        <section className="mt-5 grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]">
          <div ref={leftRef} className="flex min-w-0 flex-col gap-5">
            <div style={HIDDEN}>
              <div className="flex flex-wrap items-center gap-3 font-mono text-[14px] uppercase tracking-[0.18em] text-text-muted">
                <span className="text-accent-light">cluster / ops-02 / synthetic load</span>
                <span aria-hidden="true" className="h-px w-10 bg-border-light" />
                <span>workers 2 - 8</span>
              </div>
              <h1 className="mt-4 font-sans font-bold uppercase leading-[0.88] tracking-[-0.04em]" style={{ fontSize: 'clamp(2.7rem,min(5.6vw,10.5vh),6.2rem)' }}>
                <span className="block text-text-primary">Scale.</span>
                <span className="block text-text-primary">Prioritize.</span>
                <span className="block text-accent-light" style={{ textShadow: '0 0 48px rgba(96,165,250,0.35)' }}>Recover.</span>
              </h1>
              <p className="mt-5 max-w-[54ch] text-base leading-7 text-text-secondary">
                One queue, elastic workers, and a priority lane that critical conjunctions cut through when the storm hits.
              </p>
            </div>

            <div style={HIDDEN} className="flex flex-1 flex-col">
              <dl className="grid flex-[2] grid-cols-2 gap-px border border-border bg-border" aria-label="Primary telemetry">
                <StatCell label="Queue depth" value={n(s?.queue_depth, 4)} tone="accent" sub={s && s.processing > 0 ? `${s.processing} in flight` : 'drained'} />
                <StatCell label="Throughput" value={s ? s.throughput_per_s.toFixed(1) : DASH} sub="jobs / s" />
                <StatCell label="Live workers" value={n(alive, 2)} tone="success" flashOn={alive} sub={dead > 0 ? `${dead} dead` : 'all healthy'} subTone={dead > 0 ? 'danger' : 'muted'} />
                <StatCell label="Completed" value={n(s?.completed_total, 4)} sub="resolutions" />
              </dl>
              <dl className="mt-px grid flex-1 grid-cols-3 gap-px border-x border-b border-border bg-border" aria-label="Secondary telemetry">
                <StatCell size="lg" label="Processing" value={n(s?.processing, 2)} tone="accent" />
                <StatCell size="lg" label="Retries" value={n(s?.retries_total, 2)} tone={s && s.retries_total > 0 ? 'warning' : 'muted'} />
                <StatCell size="lg" label="Failed" value={n(s?.failed_total, 2)} tone={s && s.failed_total > 0 ? 'danger' : 'muted'} />
              </dl>
            </div>

          </div>

          <section ref={scopeRef} className="relative flex min-w-0 flex-col border border-border bg-surface-muted/80" style={HIDDEN} aria-label="Cluster constellation">
            <CornerBrackets />
            <PanelHeader
              eyebrow="Cluster constellation"
              title="Ground-station scope"
              right={<StatusChip tone={dead > 0 ? 'danger' : alive > 0 ? 'success' : 'muted'} label={dead > 0 ? `${dead} node down` : `${alive} node${alive === 1 ? '' : 's'} online`} />}
            />
            <div className="grid flex-1 place-items-center p-3">
              <div className="aspect-square w-full max-w-[max(36rem,calc(100dvh-18rem))]">
                <ClusterConstellation stats={s} linkOk={linkOk} />
              </div>
            </div>
            <ul className="flex flex-wrap gap-x-6 gap-y-2 border-t border-border px-5 py-3 font-mono text-[14px] uppercase tracking-[0.1em] text-text-secondary">
              <li><span className="text-accent-light">&#9679;</span> job</li>
              <li><span className="text-warning-light">&#9670;</span> critical</li>
              <li><span className="text-success">&#9632;</span> resolved</li>
              <li><span className="text-danger-light">&#10005;</span> dead node</li>
              <li><span className="text-danger-light">&#9633;</span> re-claim</li>
            </ul>
          </section>
        </section>

        {/* ── control dock: pinned to the bottom of the viewport so the operator never scrolls for it ── */}
        <div className="sticky bottom-4 z-30 mt-5 border border-border-light bg-background/92 backdrop-blur-sm" role="group" aria-label="Controls">
          <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 px-4 py-3">
            <div className="flex flex-wrap gap-3">
              {actions.map(({ key, action, style, text }) => (
                <button
                  key={key}
                  type="button"
                  disabled={busy !== null}
                  onClick={() => void run(action)}
                  className={`px-5 py-3 font-mono text-[14px] font-semibold uppercase tracking-[0.12em] transition duration-200 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent active:translate-y-px disabled:pointer-events-none disabled:opacity-45 ${BTN[style]}`}
                >
                  {text}
                </button>
              ))}
            </div>
            <p className="flex items-center gap-3 font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted" aria-live="polite">
              <span className={`h-2 w-2 ${busy ? 'animate-pulse bg-warning' : 'bg-text-muted'}`} />
              {busy ? `sending / ${busy}` : `last action / ${lastAction}`}
            </p>
          </div>
        </div>

        {/* ── latency + trace + register ── */}
        <div ref={lowerRef} className="mt-5 flex flex-col gap-5">
          <section className="grid gap-5 lg:grid-cols-[minmax(0,5fr)_minmax(0,6fr)]" aria-label="Latency" style={HIDDEN}>
            <div className="border border-border bg-surface-muted/75">
              <PanelHeader eyebrow="Latency" title="Enqueue to finish" right={<span className="font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">last 500 jobs</span>} />
              <dl className="grid grid-cols-3 divide-x divide-border">
                {([['avg', s?.latency_ms.avg], ['p95', s?.latency_ms.p95], ['p99', s?.latency_ms.p99]] as const).map(([k, v]) => (
                  <div key={k} className="px-5 py-5">
                    <dt className="font-mono text-[14px] uppercase tracking-[0.16em] text-text-muted">{k}</dt>
                    <dd className="mt-1 font-mono text-[clamp(1.8rem,2.8vw,2.8rem)] font-semibold tabular-nums text-text-primary">
                      {s && v !== undefined ? v : DASH}<span className="ml-1 text-base text-text-muted">ms</span>
                    </dd>
                  </div>
                ))}
              </dl>
            </div>

            <div className="relative border border-warning/40 border-l-2 border-l-warning bg-surface-muted/75">
              <CornerBrackets color="var(--color-warning)" />
              <PanelHeader eyebrow="Critical latency" title="Priority lane" tone="warning" right={<StatusChip tone="warning" label={`${s?.critical_latency_ms.count ?? 0} critical`} />} />
              <div className="grid sm:grid-cols-[minmax(0,1fr)_auto]">
                <dl className="grid grid-cols-3 divide-x divide-border">
                  {([['avg', s?.critical_latency_ms.avg], ['max', s?.critical_latency_ms.max], ['jobs', s?.critical_latency_ms.count]] as const).map(([k, v]) => (
                    <div key={k} className="px-5 py-5">
                      <dt className="font-mono text-[14px] uppercase tracking-[0.16em] text-text-muted">{k}</dt>
                      <dd className="mt-1 font-mono text-[clamp(1.8rem,2.8vw,2.8rem)] font-semibold tabular-nums text-warning-light">
                        {s && v !== undefined ? v : DASH}{k !== 'jobs' && <span className="ml-1 text-base text-text-muted">ms</span>}
                      </dd>
                    </div>
                  ))}
                </dl>
                <div className="border-t border-border px-6 py-5 sm:border-l sm:border-t-0">
                  <p className="font-mono text-[14px] uppercase tracking-[0.16em] text-text-muted">Priority advantage</p>
                  <p className="mt-1 font-mono text-[clamp(2rem,3.4vw,3.4rem)] font-semibold leading-none tabular-nums text-warning-light">
                    {adv === null ? DASH : `${adv >= 10 ? Math.round(adv) : adv.toFixed(1)}×`}
                  </p>
                  <p className="mt-2 max-w-[22ch] font-mono text-[14px] uppercase leading-5 tracking-[0.08em] text-text-muted">
                    {adv === null ? 'awaiting critical jobs' : 'overall avg / critical avg'}
                  </p>
                </div>
              </div>
            </div>
          </section>

          <section className="grid gap-5 md:grid-cols-3" aria-label="Traces" style={HIDDEN}>
            <Sparkline label="Queue depth" tone="accent-light" data={history.map((h) => h.depth)} current={n(s?.queue_depth, 4)} format={(v) => String(Math.round(v))} />
            <Sparkline label="Throughput jobs/s" tone="success" data={history.map((h) => h.throughput)} current={s ? s.throughput_per_s.toFixed(1) : DASH} />
            <Sparkline label="Live workers" tone="warning-light" data={history.map((h) => h.live)} current={n(alive, 2)} format={(v) => String(Math.round(v))} />
          </section>

          <section className="grid gap-5 lg:grid-cols-[minmax(0,7fr)_minmax(0,5fr)]" style={HIDDEN}>
            <div className="border border-border bg-surface-muted/75">
              <PanelHeader eyebrow="Worker register" title="Cluster nodes" right={<span className="font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">{s ? `${s.workers.length} registered` : DASH}</span>} />
              <ol className="divide-y divide-border">
                {!s || s.workers.length === 0 ? (
                  <li className="px-5 py-8 font-mono text-[14px] uppercase tracking-[0.12em] text-text-muted">no workers registered</li>
                ) : (
                  s.workers.map((w, i) => (
                    <li key={w.id} className={`grid grid-cols-[2.5rem_minmax(0,1fr)_auto_5.5rem_6rem] items-center gap-3 px-5 py-3 font-mono text-[14px] ${w.alive ? '' : 'border-l-2 border-danger'}`}>
                      <span className="text-text-muted">{pad(i + 1, 2)}</span>
                      <span className={`truncate tracking-[0.06em] ${w.alive ? 'text-text-primary' : 'text-danger-light'}`}>{w.id.slice(0, 12)}</span>
                      <StatusChip tone={w.alive ? 'success' : 'danger'} label={w.alive ? 'alive' : 'dead'} />
                      <span className={`text-right tabular-nums ${w.alive ? 'text-text-primary' : 'text-danger-light'}`}>{w.jobs_done}<span className="ml-1 text-text-muted">jobs</span></span>
                      <span className={`text-right tabular-nums ${w.alive ? 'text-text-secondary' : 'text-danger-light'}`}>{w.last_heartbeat_age_s.toFixed(1)} s</span>
                    </li>
                  ))
                )}
              </ol>
            </div>

            <aside className="flex flex-col gap-6 border border-border bg-surface-muted/75 p-6">
              <p className="font-mono text-[14px] uppercase tracking-[0.18em] text-text-muted">Audit contract</p>
              <LeftRuleCallout tone="success" label="Real machinery">
                Redis queue, leases, reaper, priority scheduler, worker processes and the SGP4 + deterministic negotiation are the real code.
              </LeftRuleCallout>
              <LeftRuleCallout tone="accent" label="Simulated load">
                Conjunction events are synthetic, and LLM latency is a {s ? `${s.stub_ms} ms` : 'fixed'} sleep, not an API call.
              </LeftRuleCallout>
              <div className="border border-warning/25 border-l-2 border-l-warning bg-warning-muted/15 px-5 py-4">
                <p className="font-mono text-[14px] uppercase tracking-[0.18em] text-warning-light">Scope declaration</p>
                <p className="mt-2 text-[15px] leading-7 text-text-secondary">
                  A single-host Docker Compose cluster. The numbers show scaling and scheduling behaviour, not production capacity.
                </p>
              </div>
            </aside>
          </section>
        </div>

        <footer className="mt-5 flex flex-col gap-2 border-t border-border py-5 font-mono text-[14px] uppercase tracking-[0.14em] text-text-muted sm:flex-row sm:items-center sm:justify-between">
          <span>synthetic events / simulated llm latency</span>
          <span>single-host compose cluster</span>
        </footer>
      </div>
    </main>
  );
}

/** Link-down block in the History failure style: never a blank page. */
function LinkDown({ hasData, error }: { hasData: boolean; error: string | null }) {
  return (
    <section className="mt-5 border border-border bg-surface-muted/75" role="alert">
      <div className="flex flex-col gap-5 border-l-2 border-danger px-6 py-5 lg:flex-row lg:items-center lg:justify-between">
        <div>
          <p className={`font-mono text-[16px] font-semibold uppercase tracking-[0.16em] ${TONE_TEXT.danger}`}>Cluster link down</p>
          <p className="mt-1 text-[15px] text-text-secondary">
            {hasData ? 'Showing the last known values; the stats API stopped answering.' : 'The stats API did not answer. Live monitoring on other pages is unaffected.'}
          </p>
          <ul className="mt-3 space-y-1 font-mono text-[14px] tracking-[0.04em] text-text-muted">
            <li><span className="text-accent-light">1</span> docker compose ps (api + redis + worker up?)</li>
            <li><span className="text-accent-light">2</span> curl http://localhost:8010/api/cloud/stats</li>
            <li><span className="text-accent-light">3</span> VITE_CLOUD_API points at the cloud API (default :8010)</li>
          </ul>
        </div>
        <div className="font-mono text-[14px] uppercase tracking-[0.14em] text-text-muted lg:text-right">
          <p className="flex items-center gap-3 lg:justify-end"><span className="h-2 w-2 animate-pulse bg-warning" />auto-retry every 1 s</p>
          {error && <p className="mt-2 text-danger-light">last error / {error}</p>}
        </div>
      </div>
    </section>
  );
}
