/**
 * BackgroundTraffic: ONE THREE.Points object (no per-satellite meshes, no DOM labels) added to the globe's scene.
 * The points are real LEO satellites propagated client-side with satellite.js, DISPLAY ONLY: they are not part of
 * the tracking register, the HUD clustering, conjunction screening or any alert.
 *
 * Cost control: positions are refreshed in 4 slices, one every 250 ms (each satellite once per second, <= ~1 ms
 * of work per slice for ~100 satellites); nothing runs while the tab is hidden; with prefers-reduced-motion the
 * layer is drawn once and never updated. Any error is swallowed: the Monitor then looks exactly as it did before.
 */
import { type RefObject, useEffect } from 'react';
import type { GlobeMethods } from 'react-globe.gl';
import { degreesLat, degreesLong, eciToGeodetic, gstime, propagate, twoline2satrec, type SatRec } from 'satellite.js';
import { BufferGeometry, Float32BufferAttribute, Points, PointsMaterial } from 'three';

import type { TrafficSat } from '../../lib/useTrafficSelection';

const LAYER_NAME = 'aegis-background-traffic';
const EARTH_RADIUS_KM = 6371;
const SLICES = 4;
const SLICE_MS = 250;

export function useBackgroundTraffic(
  globeRef: RefObject<GlobeMethods | undefined>,
  ready: boolean,
  enabled: boolean,
  sats: TrafficSat[],
  reducedMotion: boolean,
): void {
  useEffect(() => {
    const globe = globeRef.current;
    if (!enabled || !ready || !globe || sats.length === 0) return;

    let layer: Points | null = null;
    let timer: number | undefined;
    const dispose = () => {
      window.clearInterval(timer);
      if (layer) {
        layer.removeFromParent();
        layer.geometry.dispose();
        (layer.material as PointsMaterial).dispose();
        layer = null;
      }
    };

    try {
      const n = sats.length;
      const recs: (SatRec | null)[] = sats.map((s) => {
        try {
          return twoline2satrec(s.line1, s.line2);
        } catch {
          return null;
        }
      });
      // NB: Float32BufferAttribute COPIES the array it is given, so always write through attribute.array.
      const attribute = new Float32BufferAttribute(new Float32Array(n * 3), 3); // all zero = inside the Earth = hidden until first update
      const positions = attribute.array as Float32Array;
      const geometry = new BufferGeometry();
      geometry.setAttribute('position', attribute);
      // Dim and small: well below the curated dots (which glow through the bloom pass) so those stay dominant.
      const material = new PointsMaterial({ color: '#74a9ff', size: 2.6, sizeAttenuation: false, transparent: true, opacity: 0.85, depthWrite: false });
      layer = new Points(geometry, material);
      layer.name = LAYER_NAME;
      layer.frustumCulled = false;
      globe.scene().add(layer);

      let slice = 0;
      const update = (all: boolean) => {
        try {
          const now = new Date();
          const gmst = gstime(now);
          const from = all ? 0 : Math.floor((slice * n) / SLICES);
          const to = all ? n : Math.floor(((slice + 1) * n) / SLICES);
          for (let i = from; i < to; i += 1) {
            const rec = recs[i];
            const pv = rec ? propagate(rec, now) : null;
            let x = 0, y = 0, z = 0;
            if (pv) {
              const geo = eciToGeodetic(pv.position, gmst);
              const c = globe.getCoords(degreesLat(geo.latitude), degreesLong(geo.longitude), geo.height / EARTH_RADIUS_KM);
              if (Number.isFinite(c.x + c.y + c.z)) ({ x, y, z } = c);
            }
            positions[i * 3] = x;
            positions[i * 3 + 1] = y;
            positions[i * 3 + 2] = z;
          }
          attribute.needsUpdate = true;
          slice = (slice + 1) % SLICES;
        } catch {
          /* display-only layer: never let it break the Monitor */
        }
      };

      update(true);
      if (!reducedMotion) timer = window.setInterval(() => { if (!document.hidden) update(false); }, SLICE_MS);
    } catch {
      dispose();
    }
    return dispose;
  }, [globeRef, ready, enabled, sats, reducedMotion]);
}
