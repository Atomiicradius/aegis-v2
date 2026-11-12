/**
 * BackgroundHover: hover card for the Monitor's display-only background satellites.
 *
 *  - NO per-frame raycasting. pointermove on the globe canvas is throttled to ~60 ms; each run projects the traffic
 *    layer's position array to screen space (pickNearest) and takes the nearest point within ~10 px that is not behind
 *    the Earth. Nothing runs while the user drags / zooms (OrbitControls start..end) or touches a button.
 *  - Highlight = ONE separate one-vertex Points ring (the big buffer is never edited).
 *  - Card (BackgroundTrafficCard) is a pointer-events:none DOM overlay in a portal: debounced 80 ms in, ~50 ms out,
 *    stats refreshed at most once a second. It only ever receives events from the canvas, so it cannot appear while
 *    the pointer is over the register / alert panels or the curated-object HUD markers (they sit above the canvas).
 *  - Orbit ring (optional, per hover): one LineLoop of 90 SGP4 samples over one period, in the Earth-fixed frame of
 *    "now", cached per satellite for 30 s.
 * Display only: nothing here touches detection, the register, the alert or history. Every error is swallowed.
 */
import { type RefObject, useEffect, useLayoutEffect, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import type { GlobeMethods } from 'react-globe.gl';
import { degreesLat, degreesLong, eciToGeodetic, gstime, propagate } from 'satellite.js';
import { BufferGeometry, CanvasTexture, Float32BufferAttribute, LineBasicMaterial, LineLoop, Points, PointsMaterial, Vector3 } from 'three';

import { computeSatStats, periodMinutes, satrecOf, type SatStats } from '../../lib/satStats';
import type { TrafficSat } from '../../lib/useTrafficSelection';
import { BackgroundTrafficCard } from './BackgroundTrafficCard';
import { pickNearest } from './pickNearest';

const THROTTLE_MS = 60;
const SHOW_DELAY_MS = 80;
const HIDE_DELAY_MS = 50;
const PICK_PX = 10;
const TICK_MS = 500;            // marker follows the satellite every tick; stats every 2nd tick (<= 1/s)
const ORBIT_SAMPLES = 90;
const ORBIT_TTL_MS = 30_000;
const NAV_HEIGHT_PX = 68;       // keep the card below the 64 px nav bar
const EARTH_RADIUS_KM = 6371;

type Props = {
  globeRef: RefObject<GlobeMethods | undefined>;
  ready: boolean;
  enabled: boolean;
  /** draw the orbit ring while hovering (`?hover=card` turns it off) */
  orbit: boolean;
  sats: TrafficSat[];
  /** the traffic layer's live position array (read lazily: it is created after this component's effect runs) */
  positionsRef: RefObject<Float32Array | null>;
};

/**
 * The free rectangle between the Monitor's pointer-events-auto panels (left register, right alert, header,
 * footer), so the card never covers them. Derived from the DOM each time: no hard-coded layout.
 */
function freeBounds() {
  let l = 8, r = window.innerWidth - 8, t = NAV_HEIGHT_PX + 4, b = window.innerHeight - 8;
  document.querySelectorAll('main .pointer-events-auto').forEach((el) => {
    const q = el.getBoundingClientRect();
    if (q.width <= 0 || q.height <= 0) return;
    if (q.width > window.innerWidth * 0.6) {
      if ((q.top + q.bottom) / 2 < window.innerHeight / 2) t = Math.max(t, q.bottom + 8);
      else b = Math.min(b, q.top - 8);
    } else if ((q.left + q.right) / 2 < window.innerWidth / 2) {
      l = Math.max(l, q.right + 8);
    } else {
      r = Math.min(r, q.left - 8);
    }
  });
  return { l, r, t, b };
}

/** 90 scene-space points of the satellite's orbit over one period, at the Earth orientation of `now`. */
function computeOrbit(globe: GlobeMethods, sat: TrafficSat, now: Date): Float32Array | null {
  const rec = satrecOf(sat);
  const period = periodMinutes(sat);
  if (!rec || period <= 0) return null;
  const gmst = gstime(now);
  const out = new Float32Array(ORBIT_SAMPLES * 3);
  for (let k = 0; k < ORBIT_SAMPLES; k += 1) {
    const pv = propagate(rec, new Date(now.getTime() + (k * period * 60_000) / ORBIT_SAMPLES));
    if (!pv) return null;
    const geo = eciToGeodetic(pv.position, gmst);
    const c = globe.getCoords(degreesLat(geo.latitude), degreesLong(geo.longitude), geo.height / EARTH_RADIUS_KM);
    out[k * 3] = c.x;
    out[k * 3 + 1] = c.y;
    out[k * 3 + 2] = c.z;
  }
  return out;
}

/** Small white ring + centre dot, tinted by the material colour. Drawn in code: no image, no network. */
function makeRingTexture(): CanvasTexture {
  const canvas = document.createElement('canvas');
  canvas.width = 64;
  canvas.height = 64;
  const ctx = canvas.getContext('2d');
  if (ctx) {
    ctx.strokeStyle = '#ffffff';
    ctx.fillStyle = '#ffffff';
    ctx.lineWidth = 3;
    ctx.beginPath();
    ctx.arc(32, 32, 20, 0, Math.PI * 2);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(32, 32, 2.5, 0, Math.PI * 2);
    ctx.fill();
  }
  return new CanvasTexture(canvas);
}

export function BackgroundHover({ globeRef, ready, enabled, orbit: showRing, sats, positionsRef }: Props) {
  const [hover, setHover] = useState<number | null>(null);
  const [stats, setStats] = useState<SatStats | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const placeRef = useRef<() => void>(() => undefined);

  useEffect(() => {
    const globe = globeRef.current;
    if (!enabled || !ready || !globe || sats.length === 0) return;

    let canvas: HTMLCanvasElement;
    let controls: { addEventListener: (t: string, f: () => void) => void; removeEventListener: (t: string, f: () => void) => void };
    try {
      canvas = globe.renderer().domElement;
      controls = globe.controls() as unknown as typeof controls;
    } catch {
      return;
    }

    const scene = globe.scene();
    const radius = globe.getGlobeRadius();
    const camPos = new Vector3();
    const ptr = { x: 0, y: 0 };

    // one-vertex highlight ring
    const hlAttr = new Float32BufferAttribute(new Float32Array(3), 3);
    const hlGeo = new BufferGeometry();
    hlGeo.setAttribute('position', hlAttr);
    const ringTex = makeRingTexture();
    const hlMat = new PointsMaterial({ map: ringTex, color: '#8fc0ff', size: 26, sizeAttenuation: false, transparent: true, depthTest: false, depthWrite: false });
    const marker = new Points(hlGeo, hlMat);
    marker.renderOrder = 10;
    marker.frustumCulled = false;
    marker.visible = false;
    scene.add(marker);

    // orbit line (hidden until a satellite is hovered)
    const orbitAttr = new Float32BufferAttribute(new Float32Array(ORBIT_SAMPLES * 3), 3);
    const orbitGeo = new BufferGeometry();
    orbitGeo.setAttribute('position', orbitAttr);
    const orbitMat = new LineBasicMaterial({ color: '#60a5fa', transparent: true, opacity: 0.5, depthWrite: false });
    const orbit = new LineLoop(orbitGeo, orbitMat);
    orbit.frustumCulled = false;
    orbit.visible = false;
    scene.add(orbit);
    const orbitCache = new Map<number, { t: number; pts: Float32Array | null }>();

    let dragging = false;
    let lastRun = 0;
    let trailTimer: number | undefined;
    let showTimer: number | undefined;
    let hideTimer: number | undefined;
    let candidate = -1;
    let current = -1;
    let tick = 0;

    const placeCard = () => {
      const el = cardRef.current;
      if (!el) return;
      const w = el.offsetWidth, h = el.offsetHeight;
      const { l, r, t, b } = freeBounds();
      let x = ptr.x + 16, y = ptr.y + 16;
      if (x + w > r) x = ptr.x - 16 - w;
      if (y + h > b) y = ptr.y - 16 - h;
      x = Math.min(Math.max(x, l), Math.max(l, r - w));
      y = Math.min(Math.max(y, t), Math.max(t, b - h));
      el.style.transform = `translate(${x}px, ${y}px)`;
    };
    placeRef.current = placeCard;

    const updateMarker = () => {
      const p = positionsRef.current;
      if (!p || current < 0) return;
      const a = hlAttr.array as Float32Array;
      a[0] = p[current * 3];
      a[1] = p[current * 3 + 1];
      a[2] = p[current * 3 + 2];
      hlAttr.needsUpdate = true;
      marker.visible = a[0] !== 0 || a[1] !== 0 || a[2] !== 0;
    };

    const showOrbit = (idx: number) => {
      if (!showRing) return;
      try {
        const now = Date.now();
        let hit = orbitCache.get(idx);
        if (!hit || now - hit.t > ORBIT_TTL_MS) {
          hit = { t: now, pts: computeOrbit(globe, sats[idx], new Date(now)) };
          orbitCache.set(idx, hit);
        }
        if (hit.pts) {
          (orbitAttr.array as Float32Array).set(hit.pts);
          orbitAttr.needsUpdate = true;
          orbit.visible = true;
        } else {
          orbit.visible = false;
        }
      } catch {
        orbit.visible = false;
      }
    };

    const hide = () => {
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
      hideTimer = undefined;
      candidate = -1;
      current = -1;
      marker.visible = false;
      orbit.visible = false;
      setHover(null);
      setStats(null);
    };

    const show = (idx: number) => {
      current = idx;
      candidate = idx;
      updateMarker();
      showOrbit(idx);
      setStats(computeSatStats(sats[idx]));
      setHover(idx);
    };

    const handle = (idx: number) => {
      if (idx < 0) {
        candidate = -1;
        window.clearTimeout(showTimer);
        if (current >= 0 && hideTimer === undefined) hideTimer = window.setTimeout(hide, HIDE_DELAY_MS);
        return;
      }
      window.clearTimeout(hideTimer);
      hideTimer = undefined;
      placeCard();
      if (idx === current || idx === candidate) return;
      candidate = idx;
      window.clearTimeout(showTimer);
      showTimer = window.setTimeout(() => show(idx), SHOW_DELAY_MS);
    };

    const run = () => {
      lastRun = performance.now();
      try {
        const positions = positionsRef.current;
        if (!positions || positions.length < sats.length * 3) return;
        const rect = canvas.getBoundingClientRect();
        const px = ptr.x - rect.left, py = ptr.y - rect.top;
        if (px < 0 || py < 0 || px > rect.width || py > rect.height) {
          handle(-1);
          return;
        }
        const cam = globe.camera();
        cam.updateMatrixWorld();
        cam.getWorldPosition(camPos);
        handle(pickNearest(positions, sats.length, cam, camPos, radius, rect.width, rect.height, px, py, PICK_PX));
      } catch {
        /* display-only: never break the Monitor */
      }
    };

    const onMove = (e: PointerEvent) => {
      if (e.pointerType === 'touch') return;
      ptr.x = e.clientX;
      ptr.y = e.clientY;
      if (dragging || e.buttons) return;
      const dt = performance.now() - lastRun;
      if (dt >= THROTTLE_MS) {
        window.clearTimeout(trailTimer);
        trailTimer = undefined;
        run();
      } else if (trailTimer === undefined) {
        trailTimer = window.setTimeout(() => { trailTimer = undefined; run(); }, THROTTLE_MS - dt);
      }
    };
    const onLeave = () => {
      window.clearTimeout(trailTimer);
      trailTimer = undefined;
      hide();
    };
    const onDragStart = () => { dragging = true; onLeave(); };
    const onDragEnd = () => { dragging = false; };

    canvas.addEventListener('pointermove', onMove, { passive: true });
    canvas.addEventListener('pointerleave', onLeave);
    controls.addEventListener('start', onDragStart);
    controls.addEventListener('end', onDragEnd);

    // follow the satellite while hovered; stats at most once a second
    const timer = window.setInterval(() => {
      if (current < 0 || document.hidden) return;
      updateMarker();
      tick += 1;
      if (tick % 2 === 0) setStats(computeSatStats(sats[current]));
    }, TICK_MS);

    return () => {
      window.clearInterval(timer);
      window.clearTimeout(trailTimer);
      window.clearTimeout(showTimer);
      window.clearTimeout(hideTimer);
      canvas.removeEventListener('pointermove', onMove);
      canvas.removeEventListener('pointerleave', onLeave);
      controls.removeEventListener('start', onDragStart);
      controls.removeEventListener('end', onDragEnd);
      marker.removeFromParent();
      orbit.removeFromParent();
      hlGeo.dispose();
      hlMat.dispose();
      ringTex.dispose();
      orbitGeo.dispose();
      orbitMat.dispose();
      placeRef.current = () => undefined;
      setHover(null);
      setStats(null);
    };
  }, [globeRef, ready, enabled, showRing, sats, positionsRef]);

  // position the card before paint whenever it (re)appears or its content changes size
  useLayoutEffect(() => {
    placeRef.current();
  }, [hover, stats]);

  if (hover === null || !sats[hover]) return null;
  return createPortal(<BackgroundTrafficCard ref={cardRef} sat={sats[hover]} stats={stats} />, document.body);
}
