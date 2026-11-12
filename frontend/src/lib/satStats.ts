/**
 * Live numbers for the Monitor's hover card, computed client-side from a background satellite's TLE.
 * Static parts (inclination, period, epoch) come straight from the TLE text; altitude / speed / lat / lon come
 * from one SGP4 propagation to "now". Display only: nothing here feeds screening, the register or alerts.
 */
import { degreesLat, degreesLong, eciToGeodetic, gstime, propagate, twoline2satrec, type SatRec } from 'satellite.js';

import type { TrafficSat } from './useTrafficSelection';

export type SatStats = {
  altKm: number;
  speedKmS: number;
  incDeg: number;
  periodMin: number;
  epochAgeDays: number;
  latDeg: number;
  lonDeg: number;
};

const recCache = new Map<string, SatRec | null>();

/** Parsed SGP4 record, cached per NORAD id (null if the TLE does not parse). */
export function satrecOf(sat: TrafficSat): SatRec | null {
  if (!recCache.has(sat.id)) {
    try {
      recCache.set(sat.id, twoline2satrec(sat.line1, sat.line2));
    } catch {
      recCache.set(sat.id, null);
    }
  }
  return recCache.get(sat.id) ?? null;
}

/** Orbital period in minutes from the TLE mean motion (rev/day, line 2 cols 53-63). */
export function periodMinutes(sat: TrafficSat): number {
  const revPerDay = Number.parseFloat(sat.line2.slice(52, 63));
  return revPerDay > 0 ? 1440 / revPerDay : 0;
}

/** TLE epoch (line 1 cols 19-32, YYDDD.DDDDDDDD) as a UTC timestamp in ms. */
export function tleEpochMs(line1: string): number {
  const yy = Number.parseInt(line1.slice(18, 20), 10);
  const day = Number.parseFloat(line1.slice(20, 32));
  return Date.UTC(yy < 57 ? 2000 + yy : 1900 + yy, 0, 1) + (day - 1) * 86_400_000;
}

/** Null when the TLE cannot be propagated (the card then shows dashes for the live values). */
export function computeSatStats(sat: TrafficSat, now: Date = new Date()): SatStats | null {
  const rec = satrecOf(sat);
  if (!rec) return null;
  const pv = propagate(rec, now);
  if (!pv) return null;
  const geo = eciToGeodetic(pv.position, gstime(now));
  const v = pv.velocity;
  return {
    altKm: geo.height,
    speedKmS: Math.hypot(v.x, v.y, v.z),
    incDeg: Number.parseFloat(sat.line2.slice(8, 16)),
    periodMin: periodMinutes(sat),
    epochAgeDays: (now.getTime() - tleEpochMs(sat.line1)) / 86_400_000,
    latDeg: degreesLat(geo.latitude),
    lonDeg: degreesLong(geo.longitude),
  };
}
