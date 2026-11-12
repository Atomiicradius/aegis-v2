/**
 * Display-only "background traffic" for the Monitor globe: a static copy of real LEO TLEs
 * (public/traffic-pool.json, made by scripts/export_traffic_pool.py) drawn as dim points.
 * Nothing here touches the backend, the tracking register, conjunction detection or the alert channel.
 *
 * Setting: ?traffic=off | <N> | all   (default: VITE_BACKGROUND_TRAFFIC, else all).
 * Any failure (missing file, bad JSON, bad shape) yields an empty selection, silently.
 */
import { useEffect, useMemo, useState } from 'react';
import { useLocation } from 'react-router-dom';

export type TrafficSat = { id: string; name: string; line1: string; line2: string; group: string };
export type TrafficSetting = 'off' | 'all' | number;

export function parseTrafficSetting(search: string): TrafficSetting {
  const raw = (new URLSearchParams(search).get('traffic') ?? import.meta.env.VITE_BACKGROUND_TRAFFIC ?? 'all').trim().toLowerCase();
  if (raw === 'off' || raw === '0' || raw === 'false') return 'off';
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 ? n : 'all';
}

/**
 * `?hover=off` disables the hover card AND the orbit ring on the background traffic (the dots stay);
 * `?hover=card` keeps the card but skips the orbit ring; anything else (default) = card + ring.
 */
export type HoverMode = 'off' | 'card' | 'full';
export function parseHoverMode(search: string): HoverMode {
  const v = new URLSearchParams(search).get('hover')?.trim().toLowerCase();
  return v === 'off' ? 'off' : v === 'card' ? 'card' : 'full';
}

let pending: Promise<TrafficSat[]> | null = null;

/** Fetches the pool once per page load. Never rejects: failure resolves to []. */
function loadTrafficPool(): Promise<TrafficSat[]> {
  pending ??= fetch('/traffic-pool.json')
    .then((r) => (r.ok ? r.json() : Promise.reject(new Error(`HTTP ${r.status}`))))
    .then((data: unknown): TrafficSat[] => {
      const rows = (data as { sats?: unknown }).sats;
      if (!Array.isArray(rows)) return [];
      return rows.flatMap((row): TrafficSat[] => {
        if (!Array.isArray(row) || row.length < 4 || row.slice(0, 4).some((v) => typeof v !== 'string')) return [];
        const [name, line1, line2, group] = row as string[];
        return [{ id: line1.slice(2, 7).trim(), name, line1, line2, group }];
      });
    })
    .catch(() => []);
  return pending;
}

/** Deterministic subset of n satellites, balanced across groups (round-robin quotas, evenly spaced picks). */
export function pickBalanced(sats: TrafficSat[], n: number): TrafficSat[] {
  if (n >= sats.length) return sats;
  const buckets = new Map<string, TrafficSat[]>();
  for (const s of sats) buckets.set(s.group, [...(buckets.get(s.group) ?? []), s]);
  const groups = [...buckets.values()];
  const quota = groups.map(() => 0);
  for (let left = n; left > 0; ) {
    let moved = false;
    groups.forEach((g, i) => {
      if (left > 0 && quota[i] < g.length) {
        quota[i] += 1;
        left -= 1;
        moved = true;
      }
    });
    if (!moved) break;
  }
  return groups.flatMap((g, i) => Array.from({ length: quota[i] }, (_, k) => g[Math.floor((k * g.length) / quota[i])]));
}

/**
 * The satellites to draw. `exclude` = NORAD ids already shown as curated objects (never drawn twice).
 * `active` false (not on /monitor) = no fetch and an empty selection.
 */
export function useTrafficSelection(exclude: readonly string[], active: boolean): { sats: TrafficSat[]; status: 'off' | 'loading' | 'ready' } {
  const { search } = useLocation();
  const setting = useMemo(() => parseTrafficSetting(search), [search]);
  const [pool, setPool] = useState<TrafficSat[] | null>(null);

  useEffect(() => {
    if (!active || setting === 'off' || pool) return;
    let cancelled = false;
    void loadTrafficPool().then((p) => {
      if (!cancelled) setPool(p);
    });
    return () => {
      cancelled = true;
    };
  }, [active, setting, pool]);

  const excludeKey = exclude.join(',');
  const sats = useMemo(() => {
    if (!active || setting === 'off' || !pool) return [];
    const skip = new Set(excludeKey.split(','));
    const usable = pool.filter((s) => !skip.has(s.id));
    return setting === 'all' ? usable : pickBalanced(usable, setting);
  }, [active, setting, pool, excludeKey]);

  return { sats, status: setting === 'off' ? 'off' : pool ? 'ready' : 'loading' };
}
