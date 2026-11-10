import { memo } from 'react';

import { C, GATE_R, RINGS, TICK_R, VB, polar, ticksPath } from './scopeGeometry';

const TICKS = ticksPath();
const COMPASS = [
  { deg: -90, text: '000' },
  { deg: 0, text: '090' },
  { deg: 90, text: '180' },
  { deg: 180, text: '270' },
];

/**
 * Static layer of the Cluster Scope: gradients, orbit rings, tick ring, crosshair,
 * corner brackets and the slow radar sweep (a CSS rotation, so it costs ~nothing).
 * Memoised: it never re-renders.
 */
export const ScopeBase = memo(function ScopeBase() {
  const gate = polar(GATE_R, -90);
  return (
    <g aria-hidden="true">
      <defs>
        <radialGradient id="cloud-core-glow" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#3b82f6" stopOpacity="0.55" />
          <stop offset="55%" stopColor="#3b82f6" stopOpacity="0.14" />
          <stop offset="100%" stopColor="#3b82f6" stopOpacity="0" />
        </radialGradient>
        <radialGradient id="cloud-scope-vignette" cx="50%" cy="50%" r="50%">
          <stop offset="0%" stopColor="#10151f" stopOpacity="0.0" />
          <stop offset="100%" stopColor="#0a0e17" stopOpacity="0.55" />
        </radialGradient>
        <linearGradient id="cloud-sweep-grad" gradientUnits="userSpaceOnUse" x1="740" y1="400" x2="705" y2="300">
          <stop offset="0%" stopColor="#60a5fa" stopOpacity="0.2" />
          <stop offset="100%" stopColor="#60a5fa" stopOpacity="0" />
        </linearGradient>
      </defs>

      <circle cx={C} cy={C} r={TICK_R + 14} fill="url(#cloud-scope-vignette)" />

      {/* radar sweep */}
      <g className="cloud-sweep">
        <path d={`M${C} ${C} L${C + 340} ${C} A340 340 0 0 0 ${C + 340 * Math.cos(-0.5)} ${C + 340 * Math.sin(-0.5)} Z`} fill="url(#cloud-sweep-grad)" />
        <line x1={C} y1={C} x2={C + 340} y2={C} className="stroke-accent-light/40" strokeWidth="1" />
      </g>

      {/* crosshair hairlines */}
      <path d={`M${C} ${C - TICK_R}V${C + TICK_R}M${C - TICK_R} ${C}H${C + TICK_R}`} className="stroke-border-light/50" strokeWidth="1" />

      {/* orbit rings */}
      {RINGS.map((r) => (
        <circle key={r} cx={C} cy={C} r={r} fill="none" className="stroke-border-light" strokeWidth="1" strokeDasharray="2 7" />
      ))}
      <circle cx={C} cy={C} r={TICK_R} fill="none" className="stroke-border-light" strokeWidth="1" />
      <path d={TICKS} className="stroke-text-muted" strokeWidth="1.25" />

      {COMPASS.map(({ deg, text }) => {
        const p = polar(TICK_R - 34, deg);
        return (
          <text key={text} x={p.x} y={p.y + 5} textAnchor="middle" className="fill-text-muted font-mono" fontSize="15" letterSpacing="2">
            {text}
          </text>
        );
      })}

      {/* ingest gate (top) */}
      <path d={`M${gate.x - 22} ${gate.y + 2}H${gate.x + 22}`} className="stroke-accent-light" strokeWidth="2" />
      <text x={gate.x + 30} y={gate.y + 7} className="fill-accent-light font-mono" fontSize="15" letterSpacing="3">
        INGEST
      </text>

      {/* corner brackets */}
      {[
        [8, 8, 1, 1],
        [VB - 8, 8, -1, 1],
        [8, VB - 8, 1, -1],
        [VB - 8, VB - 8, -1, -1],
      ].map(([x, y, sx, sy]) => (
        <path key={`${x}-${y}`} d={`M${x} ${y + sy * 30}V${y}H${x + sx * 30}`} fill="none" className="stroke-accent-light/70" strokeWidth="2" />
      ))}
    </g>
  );
});
