/**
 * BackgroundTrafficCard: the Monitor's hover card for a display-only background satellite.
 * Hairline-bordered, square, mono labels, blue panel-header eyebrow (Monitor panel pattern); no filled colour.
 * Rendered in a portal by BackgroundHover with `position: fixed` and pointer-events: none, so it never
 * intercepts a click and never shifts layout. Positioning is done by the parent through the forwarded ref.
 */
import { forwardRef } from 'react';

import { artForGroup } from '../../lib/satArt';
import type { SatStats } from '../../lib/satStats';
import type { TrafficSat } from '../../lib/useTrafficSelection';
import { SatSchematic } from './SatSchematic';

const GROUP_LABEL: Record<string, string> = { starlink: 'Starlink', oneweb: 'OneWeb', 'iridium-NEXT': 'Iridium NEXT', stations: 'Station', active: 'Active LEO' };

const dash = '--';
const fmt = (v: number | undefined, digits: number, unit: string) => (v === undefined || !Number.isFinite(v) ? dash : `${v.toFixed(digits)}${unit}`);
const latLon = (lat: number, lon: number) => `${Math.abs(lat).toFixed(1)}${lat >= 0 ? 'N' : 'S'} ${Math.abs(lon).toFixed(1)}${lon >= 0 ? 'E' : 'W'}`;

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-surface-muted/95 px-3 py-2">
      <dt className="font-mono text-[10px] uppercase tracking-wider text-text-muted">{label}</dt>
      <dd className="mt-0.5 font-mono text-[13px] tabular-nums text-text-primary">{value}</dd>
    </div>
  );
}

export const BackgroundTrafficCard = forwardRef<HTMLDivElement, { sat: TrafficSat; stats: SatStats | null }>(function BackgroundTrafficCard({ sat, stats }, ref) {
  return (
    <div
      ref={ref}
      role="tooltip"
      aria-label={`Background satellite ${sat.name}`}
      className="pointer-events-none fixed left-0 top-0 z-30 w-[17.5rem] border border-border bg-background/95 backdrop-blur-sm"
      style={{ transform: 'translate(-9999px, -9999px)' }}
    >
      <div className="border-b border-border px-3 py-2.5">
        <p className="font-mono text-[10px] uppercase tracking-[0.18em] text-accent-light">Background traffic</p>
        <h3 className="mt-1 truncate text-sm font-semibold text-text-primary">{sat.name}</h3>
        <div className="mt-1.5 flex flex-wrap gap-1.5 font-mono text-[10px] uppercase tracking-wider">
          <span className="border border-border px-1.5 py-0.5 text-text-secondary">NORAD {sat.id}</span>
          <span className="border border-accent/40 px-1.5 py-0.5 text-accent-light">{GROUP_LABEL[sat.group] ?? sat.group}</span>
        </div>
      </div>

      <div className="flex items-center justify-center border-b border-border px-3 py-2 text-accent-light">
        <SatSchematic art={artForGroup(sat.group)} />
      </div>
      <p className="border-b border-border px-3 py-1 text-center font-mono text-[9px] uppercase tracking-wider text-text-muted">generic schematic / not to scale</p>

      <dl className="grid grid-cols-2 gap-px bg-border">
        <Stat label="Altitude" value={fmt(stats?.altKm, 0, ' km')} />
        <Stat label="Speed" value={fmt(stats?.speedKmS, 2, ' km/s')} />
        <Stat label="Inclination" value={fmt(stats?.incDeg, 1, '°')} />
        <Stat label="Period" value={fmt(stats?.periodMin, 1, ' min')} />
        <Stat label="TLE epoch age" value={fmt(stats?.epochAgeDays, 1, ' d')} />
        <Stat label="Lat / lon" value={stats ? latLon(stats.latDeg, stats.lonDeg) : dash} />
      </dl>

      <p className="border-t border-border border-l-2 border-l-warning px-3 py-2 font-mono text-[10px] uppercase leading-4 tracking-wider text-warning-light">
        Display only / not screened for conjunctions
      </p>
    </div>
  );
});
