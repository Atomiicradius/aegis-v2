import { animate } from 'animejs';
import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';

import { useAnimeEntry } from '../../lib/useAnimeEntry';
import type { Resolution } from '../globe/types';

/**
 * showTrajectoryLink: Negotiate-only deep link; the cloud page hides it.
 * residualAsDistance: the engine's `residual_risk` on APPROVED resolutions is really the minimum distance
 * in km (validation_agent: min(expected miss, closest third party)), not a probability. Negotiate keeps
 * printing it as a percent (unchanged); the cloud page passes true to print it as a distance.
 */
export function ResolutionCard({ resolution, showTrajectoryLink = true, residualAsDistance = false }: {
  resolution: Resolution | null;
  showTrajectoryLink?: boolean;
  residualAsDistance?: boolean;
}) {
  const cardRef = useRef<HTMLElement>(null);

  /* ── card slide-up + fade when resolution appears ── */
  useAnimeEntry(cardRef, !!resolution, { duration: 420, translateY: 14 });

  /* ── yield-score count-up ── */
  const yieldRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!resolution || !yieldRef.current || residualAsDistance) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const target = resolution.residual_risk * 100;
    const el = yieldRef.current;

    animate(
      { value: 0 },
      {
        value: target,
        duration: 1100,
        easing: 'easeOutCubic',
        onUpdate: (anim) => {
          const v = (anim.targets[0] as { value: number }).value;
          el.textContent = `${v.toExponential(2)}%`;
        },
      },
    );
  }, [resolution?.conjunction_id]); // eslint-disable-line react-hooks/exhaustive-deps

  /* ── delta-v count-up ── */
  const deltaVRef = useRef<HTMLElement>(null);
  useEffect(() => {
    if (!resolution || !deltaVRef.current) return;
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const target = resolution.delta_v_mps;
    const el = deltaVRef.current;

    animate(
      { value: 0 },
      {
        value: target,
        duration: 950,
        easing: 'easeOutCubic',
        onUpdate: (anim) => {
          const v = (anim.targets[0] as { value: number }).value;
          el.textContent = `${v.toFixed(3)} m/s`;
        },
      },
    );
  }, [resolution?.conjunction_id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!resolution) return null;

  const isSuccess = resolution.status.startsWith('approved');
  const tone = isSuccess
    ? 'border-success/40 bg-success-muted/20 text-success-light'
    : 'border-danger/40 bg-danger-muted/20 text-danger-light';

  return (
    <section
      ref={cardRef}
      className={`mt-5 border ${tone}`}
      aria-labelledby="resolution-title"
      style={{ opacity: 0 }}
    >
      <header className="grid gap-4 border-b border-current/20 px-5 py-4 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
        <div>
          <p className="font-mono text-[9px] uppercase tracking-[0.2em] opacity-80">Final decision record</p>
          <h3 id="resolution-title" className="mt-1 text-xl font-semibold tracking-tight text-text-primary">
            {resolution.status === 'no_safe_maneuver_found' ? 'No safe maneuver found' : 'Maneuver plan approved'}
          </h3>
        </div>
        <span className="w-fit border border-current/35 px-2.5 py-1.5 font-mono text-[9px] font-semibold uppercase tracking-wider">
          {resolution.status.replace(/_/g, ' ')}
        </span>
      </header>

      <div className="grid lg:grid-cols-[minmax(0,1fr)_2fr]">
        <div className="border-b border-current/20 p-5 lg:border-b-0 lg:border-r">
          <p className="font-mono text-[8px] uppercase tracking-wider opacity-70">Resolution rationale</p>
          <p className="mt-3 max-w-[60ch] text-sm leading-6 text-text-secondary">{resolution.rationale_text}</p>
          {isSuccess && showTrajectoryLink && (
            <Link
              to={`/negotiate/${resolution.conjunction_id}/trajectory`}
              className="mt-6 inline-flex border-b border-current pb-1 font-mono text-[10px] font-semibold uppercase tracking-wider transition-opacity hover:opacity-70 focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
            >
              Inspect trajectory
            </Link>
          )}
        </div>

        {isSuccess && (
          <dl className="grid grid-cols-2 gap-px bg-current/20 sm:grid-cols-3">
            {[
              { label: 'Maneuvering agent', value: resolution.maneuvering_agent, ref: undefined },
              { label: 'Maneuver type',     value: resolution.maneuver_type,     ref: undefined },
              { label: 'Delta-V',           value: `${resolution.delta_v_mps.toFixed(3)} m/s`, ref: deltaVRef },
              { label: 'Expected miss',     value: `${resolution.expected_min_distance_km.toFixed(3)} km`, ref: undefined },
              residualAsDistance
                ? { label: 'Residual min distance', value: `${resolution.residual_risk.toFixed(3)} km`, ref: undefined }
                : { label: 'Residual risk',     value: `${(resolution.residual_risk * 100).toExponential(2)}%`, ref: yieldRef },
              { label: 'Execution UTC',     value: resolution.execution_time_utc, ref: undefined },
            ].map(({ label, value, ref: cellRef }) => (
              <div key={label} className="min-w-0 bg-surface-muted p-4">
                <dt className="font-mono text-[8px] uppercase tracking-wider text-text-muted">{label}</dt>
                <dd ref={cellRef} className="mt-2 break-words font-mono text-xs font-medium tabular-nums text-text-primary">{value}</dd>
              </div>
            ))}
          </dl>
        )}
      </div>
    </section>
  );
}
