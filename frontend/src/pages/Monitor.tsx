import { animate } from 'animejs';
import { motion } from 'framer-motion';
import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';

import { useGlobeStage } from '../components/globe/GlobeStage';
import { MOCK_TRACKED_OBJECTS } from '../components/globe/mockTrackedObjects';
import { useAnimeSlideIn } from '../lib/useAnimeEntry';
import { useAnimeStagger } from '../lib/useAnimeStagger';
import { useTrafficSelection } from '../lib/useTrafficSelection';
import { useNegotiationStore } from '../store/useNegotiationStore';

const CONNECTION_TONE = {
  connecting: 'text-warning-light',
  connected: 'text-success-light',
  reconnecting: 'text-warning-light',
  disconnected: 'text-danger-light',
} as const;

function shortUtc(timestamp: string | undefined): string {
  if (!timestamp) return '-';
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? timestamp : `${date.toISOString().slice(0, 19)}Z`;
}

export default function Monitor() {
  const liveTrackedObjects = useNegotiationStore((state) => state.trackedObjects);
  const activeConjunctionAlert = useNegotiationStore((state) => state.activeConjunctionAlert);
  const connectionStatus = useNegotiationStore((state) => state.connectionStatus);
  const { setGlobeStage, phase, isMonitorChromeVisible } = useGlobeStage();

  const trackedObjects = liveTrackedObjects.length > 0 ? liveTrackedObjects : MOCK_TRACKED_OBJECTS;
  const usingLiveData = liveTrackedObjects.length > 0;
  const flaggedIds = activeConjunctionAlert
    ? new Set([activeConjunctionAlert.primary_id, activeConjunctionAlert.secondary_id])
    : new Set<string>();
  const hazardCount = trackedObjects.filter((object) => flaggedIds.has(object.norad_id)).length;
  const activeCount = trackedObjects.length - hazardCount;
  // Display-only background traffic (real LEO satellites drawn on the globe, never screened or registered).
  const traffic = useTrafficSelection(trackedObjects.map((object) => object.norad_id), true);

  useEffect(() => {
    if (phase === 'monitor-enter') return;
    setGlobeStage({
      mode: 'monitor',
      conjunctionAlert: activeConjunctionAlert ?? undefined,
    });
  }, [activeConjunctionAlert, phase, setGlobeStage]);

  /* ── conjunction panel slide-in from right ── */
  const conjunctionPanelRef = useRef<HTMLElement>(null);
  useAnimeSlideIn(conjunctionPanelRef, !!activeConjunctionAlert, { duration: 380, translateX: 24 });

  /* ── miss-distance count-up when alert appears ── */
  const missDistanceRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!activeConjunctionAlert || !missDistanceRef.current) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const target = activeConjunctionAlert.miss_distance_km;
    const el = missDistanceRef.current;

    animate(
      { value: 0 },
      {
        value: target,
        duration: 900,
        easing: 'easeOutCubic',
        onUpdate: (anim) => {
          const current = (anim.targets[0] as { value: number }).value;
          el.textContent = `${current.toFixed(3)} km`;
        },
      },
    );
  }, [activeConjunctionAlert?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── tracking register list stagger on mount ── */
  const registerListRef = useRef<HTMLOListElement>(null);
  useAnimeStagger(registerListRef, [trackedObjects.length]);

  return (
    <main className="pointer-events-none relative min-h-[calc(100dvh-4rem)] overflow-hidden">
      <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,rgba(10,14,23,0.94)_0%,rgba(10,14,23,0.32)_28%,transparent_50%,rgba(10,14,23,0.18)_76%,rgba(10,14,23,0.78)_100%)]" />

      <motion.div
        initial={{ opacity: 0, y: 10 }}
        animate={{ opacity: isMonitorChromeVisible ? 1 : 0, y: isMonitorChromeVisible ? 0 : 10 }}
        transition={{ duration: 0.32, ease: [0.22, 1, 0.36, 1] }}
        className="pointer-events-none relative z-10 mx-auto grid min-h-[calc(100dvh-4rem)] max-w-[1600px] grid-rows-[auto_1fr_auto] p-4 sm:p-6"
      >
        <header className="pointer-events-auto grid gap-3 border border-border bg-background/88 px-4 py-3 backdrop-blur-sm sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center sm:px-5">
          <div className="flex flex-wrap items-center gap-x-4 gap-y-2">
            <h1 className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-text-primary">Orbital watch floor</h1>
            <span className="hidden h-3 w-px bg-border sm:block" />
            <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-text-muted">sector / LEO-curated</span>
            <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-text-muted">propagator / SGP4</span>
            {traffic.sats.length > 0 && (
              <span className="font-mono text-[9px] uppercase tracking-[0.16em] text-text-muted">
                tracked {trackedObjects.length} / background traffic {traffic.sats.length} / display only, not screened
              </span>
            )}
          </div>
          <div className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-[0.16em]">
            <span className={`h-1.5 w-1.5 ${connectionStatus === 'connected' ? 'bg-success shadow-[0_0_8px_var(--color-success)]' : connectionStatus === 'disconnected' ? 'bg-danger' : 'bg-warning animate-pulse'}`} />
            <span className={CONNECTION_TONE[connectionStatus]}>link / {connectionStatus}</span>
            <span className="ml-2 border-l border-border pl-3 text-text-muted">source / {usingLiveData ? 'LIVE WS' : 'CACHED FALLBACK'}</span>
          </div>
        </header>

        <div className="grid items-start gap-4 py-4 lg:grid-cols-[18rem_minmax(0,1fr)_21rem]">
          <aside className="pointer-events-auto border border-border bg-background/88 backdrop-blur-sm">
            <div className="border-b border-border px-4 py-3">
              <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-accent-light">Tracking register</p>
              <p className="mt-1 text-sm font-semibold text-text-primary">Curated object set</p>
            </div>
            <dl className="grid grid-cols-2 gap-px bg-border">
              <div className="bg-surface-muted/95 p-4">
                <dt className="font-mono text-[8px] uppercase tracking-wider text-text-muted">Nominal</dt>
                <dd className="mt-1 font-mono text-2xl font-semibold tabular-nums text-accent-light">{String(activeCount).padStart(2, '0')}</dd>
              </div>
              <div className="bg-surface-muted/95 p-4">
                <dt className="font-mono text-[8px] uppercase tracking-wider text-text-muted">Flagged</dt>
                <dd className="mt-1 font-mono text-2xl font-semibold tabular-nums text-danger-light">{String(hazardCount).padStart(2, '0')}</dd>
              </div>
            </dl>
            <ol ref={registerListRef} className="max-h-[16rem] divide-y divide-border overflow-y-auto">
              {trackedObjects.map((object, index) => {
                const flagged = flaggedIds.has(object.norad_id);
                return (
                  <li key={object.norad_id} className="grid grid-cols-[2rem_minmax(0,1fr)_auto] items-center gap-2 px-4 py-2.5" style={{ opacity: 0 }}>
                    <span className="font-mono text-[8px] text-text-muted">{String(index + 1).padStart(2, '0')}</span>
                    <span className="truncate text-xs text-text-secondary">{object.name}</span>
                    <span className={`font-mono text-[9px] ${flagged ? 'text-danger-light' : 'text-text-muted'}`}>{object.norad_id}</span>
                  </li>
                );
              })}
            </ol>
          </aside>

          <div className="hidden min-h-[30rem] lg:block" aria-hidden="true" />

          <aside
            ref={conjunctionPanelRef}
            className="pointer-events-auto self-start border border-border bg-background/90 backdrop-blur-sm"
            style={{ opacity: activeConjunctionAlert ? 0 : 1 }}
          >
            <div className="flex items-start justify-between border-b border-border px-4 py-3">
              <div>
                <p className="font-mono text-[9px] uppercase tracking-[0.18em] text-danger-light">Conjunction channel</p>
                <h2 className="mt-1 text-sm font-semibold text-text-primary">
                  {activeConjunctionAlert ? 'Priority alert' : 'No active alert'}
                </h2>
              </div>
              <span className={`h-2 w-2 ${activeConjunctionAlert ? 'bg-danger shadow-[0_0_9px_var(--color-danger)]' : 'bg-success'}`} />
            </div>

            {activeConjunctionAlert ? (
              <>
                <div className="grid grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-5">
                  <div>
                    <p className="font-mono text-[8px] uppercase tracking-wider text-text-muted">Primary</p>
                    <p className="mt-1 font-mono text-sm font-semibold text-text-primary">{activeConjunctionAlert.primary_id}</p>
                  </div>
                  <span className="h-px w-8 bg-danger/60" />
                  <div className="text-right">
                    <p className="font-mono text-[8px] uppercase tracking-wider text-text-muted">Secondary</p>
                    <p className="mt-1 font-mono text-sm font-semibold text-text-primary">{activeConjunctionAlert.secondary_id}</p>
                  </div>
                </div>
                <dl className="divide-y divide-border border-y border-border">
                  <div className="flex items-end justify-between gap-4 px-4 py-3">
                    <dt className="font-mono text-[8px] uppercase tracking-wider text-text-muted">Predicted miss</dt>
                    <dd ref={missDistanceRef} className="font-mono text-lg font-semibold tabular-nums text-danger-light">
                      {activeConjunctionAlert.miss_distance_km.toFixed(3)} km
                    </dd>
                  </div>
                  <div className="flex items-end justify-between gap-4 px-4 py-3">
                    <dt className="font-mono text-[8px] uppercase tracking-wider text-text-muted">Relative velocity</dt>
                    <dd className="font-mono text-xs tabular-nums text-text-primary">{activeConjunctionAlert.relative_velocity_kmps.toFixed(3)} km/s</dd>
                  </div>
                  <div className="px-4 py-3">
                    <dt className="font-mono text-[8px] uppercase tracking-wider text-text-muted">TCA / UTC</dt>
                    <dd className="mt-1 font-mono text-[10px] text-text-secondary">{shortUtc(activeConjunctionAlert.tca_utc)}</dd>
                  </div>
                </dl>
                <Link
                  to={`/negotiate/${activeConjunctionAlert.id}`}
                  className="block bg-danger px-4 py-3 text-center font-mono text-[10px] font-semibold uppercase tracking-[0.14em] text-white transition duration-200 hover:brightness-110 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-danger active:translate-y-px"
                >
                  Open resolution desk
                </Link>
              </>
            ) : (
              <div className="px-4 py-8">
                <p className="font-mono text-[10px] uppercase tracking-wider text-success-light">Screening nominal</p>
                <p className="mt-2 text-xs leading-5 text-text-muted">The monitor will promote any sub-threshold pair into this channel.</p>
              </div>
            )}
          </aside>
        </div>

        <footer className="pointer-events-auto grid border border-border bg-background/90 backdrop-blur-sm sm:grid-cols-2 lg:grid-cols-4">
          {[
            ['Object epoch', shortUtc(trackedObjects[0]?.timestamp_utc)],
            ['Alert threshold', '5.000 km'],
            ['Display mode', activeConjunctionAlert ? 'CONJUNCTION' : 'LIVE'],
            ['Frame', 'ECI -> GEODETIC'],
          ].map(([label, value], index) => (
            <div key={label} className={`px-4 py-3 ${index > 0 ? 'border-t border-border sm:border-t-0 sm:border-l' : ''} ${index === 2 ? 'sm:border-t lg:border-t-0' : ''}`}>
              <p className="font-mono text-[8px] uppercase tracking-wider text-text-muted">{label}</p>
              <p className="mt-1 truncate font-mono text-[10px] text-text-primary">{value}</p>
            </div>
          ))}
        </footer>
      </motion.div>
    </main>
  );
}
