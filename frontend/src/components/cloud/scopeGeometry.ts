/** Geometry + easing for the Cluster Scope (ClusterConstellation). viewBox is 800 x 800, centre (400, 400). */

export const VB = 800;
export const C = 400;

export const TICK_R = 372;   // outer tick ring
export const GATE_R = 392;   // where "ingest" / result packets enter and leave
export const CORE_R = 62;    // queue core body

/** Worker orbit rings (radius) and their drift in deg/s (alternating direction). */
export const RINGS = [185, 255, 325] as const;
export const OMEGA = [2.4, 2.4, 2.4] as const;   // rigid drift: nodes never collide

/** Slot order = lighting order. Angles come from a search that maximises the minimum node spacing
 *  (>=165 units) so labels never touch; the whole constellation drifts rigidly. */
export const SLOT_DEFS: ReadonlyArray<{ ring: 0 | 1 | 2; a0: number }> = [
  { ring: 0, a0: 44 },
  { ring: 0, a0: 313 },
  { ring: 1, a0: 92 },
  { ring: 2, a0: 196 },
  { ring: 1, a0: 267 },
  { ring: 2, a0: 237 },
  { ring: 1, a0: 152 },
  { ring: 2, a0: 4 },
];
export const MAX_SLOTS = SLOT_DEFS.length;

export type Pt = { x: number; y: number };

const RAD = Math.PI / 180;

export function polar(radius: number, deg: number): Pt {
  return { x: C + radius * Math.cos(deg * RAD), y: C + radius * Math.sin(deg * RAD) };
}

/** Position of worker slot `slot` at animation time `t` seconds. */
export function nodePos(slot: number, t: number): Pt {
  const def = SLOT_DEFS[slot];
  return polar(RINGS[def.ring], def.a0 + OMEGA[def.ring] * t);
}

export const lerp = (a: number, b: number, u: number): number => a + (b - a) * u;
export const lerpPt = (a: Pt, b: Pt, u: number): Pt => ({ x: lerp(a.x, b.x, u), y: lerp(a.y, b.y, u) });
export const easeOut = (u: number): number => 1 - (1 - u) * (1 - u);
export const easeInOut = (u: number): number => (u < 0.5 ? 2 * u * u : 1 - (-2 * u + 2) ** 2 / 2);
export const clamp01 = (u: number): number => Math.max(0, Math.min(1, u));

/** One <path d> holding all outer tick marks: 72 short ticks, every 6th (30 deg) longer. */
export function ticksPath(): string {
  const parts: string[] = [];
  for (let i = 0; i < 72; i += 1) {
    const long = i % 6 === 0;
    const a = polar(TICK_R, i * 5);
    const b = polar(TICK_R - (long ? 16 : 7), i * 5);
    parts.push(`M${a.x.toFixed(1)} ${a.y.toFixed(1)}L${b.x.toFixed(1)} ${b.y.toFixed(1)}`);
  }
  return parts.join('');
}
