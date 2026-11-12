import { motion, useReducedMotion } from 'framer-motion';
import { forwardRef, useCallback, useEffect, useImperativeHandle, useMemo, useRef, useState } from 'react';
import ReactGlobe, { type GlobeMethods } from 'react-globe.gl';
import { useLocation } from 'react-router-dom';
import { ACESFilmicToneMapping, AdditiveBlending, AmbientLight, BufferGeometry, Color, DirectionalLight, Float32BufferAttribute, Group, Mesh, MeshPhongMaterial, Points, PointsMaterial, ShaderMaterial, SphereGeometry, Vector2 } from 'three';
import { UnrealBloomPass } from 'three/examples/jsm/postprocessing/UnrealBloomPass.js';

import earthBumpTexture from '../../assets/earth-bump.png';
import earthNightTexture from '../../assets/earth-night.jpg';
import { parseHoverMode, useTrafficSelection } from '../../lib/useTrafficSelection';
import { BackgroundHover } from './BackgroundHover';
import { useBackgroundTraffic } from './BackgroundTraffic';
import { DOCKED_OBJECT_GROUPS } from './dockedGroups';
import { positionKmToGeo } from './eciToGeo';
import { GlobeHud } from './GlobeHud';
import type { ConjunctionAlert, ManeuverTrajectoryResult, TrackedObject } from './types';

export type GlobeMode = 'live' | 'conjunction' | 'trajectory' | 'landing' | 'transition';

export type GlobeHandle = {
  pointOfView: (
    target: { lat?: number; lng?: number; altitude?: number },
    transitionMs?: number,
  ) => void;
};

type Props = {
  trackedObjects: TrackedObject[];
  mode: GlobeMode;
  conjunctionAlert?: ConjunctionAlert;
  trajectoryResult?: ManeuverTrajectoryResult;
  className?: string;
};

type GlobePoint = {
  norad_id: string;
  name: string;
  lat: number;
  lng: number;
  alt: number;
  isFlagged: boolean;
};

type OrbitTrail = GlobePoint & { endLat: number; endLng: number; endAlt: number };

type ScreenPoint = GlobePoint & { x: number; y: number };
type HudCluster = ScreenPoint & { count: number; members: GlobePoint[] };

// Screen-space distance (px) below which two markers are considered
// overlapping and get merged into one cluster marker. Docked objects (see
// dockedGroups.ts) render at literally the same pixel; this also catches any
// other incidental close approach on screen that isn't in that curated list.
const CLUSTER_PIXEL_THRESHOLD = 18;

function clusterHudPoints(screenPoints: ScreenPoint[]): HudCluster[] {
  const used = new Set<string>();
  const clusters: HudCluster[] = [];

  // Pass 1: known docked/co-located groups get a named combined label.
  for (const group of DOCKED_OBJECT_GROUPS) {
    const members = screenPoints.filter((p) => !used.has(p.norad_id) && group.ids.has(p.norad_id));
    if (members.length < 2) continue;
    members.forEach((m) => used.add(m.norad_id));
    clusters.push({
      ...members[0],
      name: group.label,
      x: members.reduce((sum, m) => sum + m.x, 0) / members.length,
      y: members.reduce((sum, m) => sum + m.y, 0) / members.length,
      isFlagged: members.some((m) => m.isFlagged),
      count: members.length,
      members,
    });
  }

  // Pass 2: generic proximity clustering for anything else still overlapping.
  const remaining = screenPoints.filter((p) => !used.has(p.norad_id));
  for (const point of remaining) {
    if (used.has(point.norad_id)) continue;
    const nearby = remaining.filter(
      (other) => !used.has(other.norad_id) && Math.hypot(other.x - point.x, other.y - point.y) <= CLUSTER_PIXEL_THRESHOLD,
    );
    nearby.forEach((m) => used.add(m.norad_id));
    if (nearby.length >= 2) {
      clusters.push({
        ...nearby[0],
        name: 'OBJECT CLUSTER',
        x: nearby.reduce((sum, m) => sum + m.x, 0) / nearby.length,
        y: nearby.reduce((sum, m) => sum + m.y, 0) / nearby.length,
        isFlagged: nearby.some((m) => m.isFlagged),
        count: nearby.length,
        members: nearby,
      });
    } else {
      clusters.push({ ...point, count: 1, members: [point] });
    }
  }

  // Pass 3: final de-collision. Two markers can still end up screen-adjacent
  // purely from the current camera angle/rotation — e.g. ISS COMPLEX and CSS
  // CLUSTER are physically unrelated but can project close together — which
  // isn't caught by passes 1-2 (those only merge points that are the SAME
  // underlying location). Nudge later markers down until their label text no
  // longer collides with an earlier one's.
  const LABEL_COLLISION_PX = 22;
  for (let i = 1; i < clusters.length; i += 1) {
    for (let attempt = 0; attempt < 6; attempt += 1) {
      const collides = clusters
        .slice(0, i)
        .some((other) => Math.hypot(clusters[i].x - other.x, clusters[i].y - other.y) < LABEL_COLLISION_PX);
      if (!collides) break;
      clusters[i] = { ...clusters[i], y: clusters[i].y + LABEL_COLLISION_PX };
    }
  }

  return clusters;
}

const ACCENT_COLOR = '#60a5fa';
const DANGER_COLOR = '#ff5b67';

const atmosphereVertex = `
  varying vec3 vNormal;
  varying vec3 vViewDirection;
  void main() {
    vec4 viewPosition = modelViewMatrix * vec4(position, 1.0);
    vNormal = normalize(normalMatrix * normal);
    vViewDirection = normalize(-viewPosition.xyz);
    gl_Position = projectionMatrix * viewPosition;
  }
`;

const atmosphereFragment = `
  uniform vec3 glowColor;
  uniform float intensity;
  varying vec3 vNormal;
  varying vec3 vViewDirection;
  void main() {
    float fresnel = pow(1.0 - max(dot(vNormal, vViewDirection), 0.0), 3.1);
    gl_FragColor = vec4(glowColor, fresnel * intensity);
  }
`;

function createAtmosphere(radius: number) {
  const material = new ShaderMaterial({
    uniforms: { glowColor: { value: new Color('#6fa8ff') }, intensity: { value: 0.52 } },
    vertexShader: atmosphereVertex,
    fragmentShader: atmosphereFragment,
    transparent: true,
    blending: AdditiveBlending,
    depthWrite: false,
  });
  const mesh = new Mesh(new SphereGeometry(radius * 1.045, 96, 96), material);
  mesh.name = 'aegis-fresnel-atmosphere';
  return mesh;
}

function createStarLayer(radius: number, count: number, color: string, size: number, opacity: number, offset: number) {
  const positions = new Float32Array(count * 3);
  const goldenAngle = Math.PI * (3 - Math.sqrt(5));

  for (let index = 0; index < count; index += 1) {
    // Fibonacci-distributed points avoid the artificial clumps produced by a
    // naive random cloud and make the sky deterministic across page loads.
    const y = 1 - ((index + 0.5) / count) * 2;
    const ring = Math.sqrt(1 - y * y);
    const theta = index * goldenAngle + offset;
    const distance = radius + ((index * 17) % 11) - 5;
    positions[index * 3] = Math.cos(theta) * ring * distance;
    positions[index * 3 + 1] = y * distance;
    positions[index * 3 + 2] = Math.sin(theta) * ring * distance;
  }

  const geometry = new BufferGeometry();
  geometry.setAttribute('position', new Float32BufferAttribute(positions, 3));
  const material = new PointsMaterial({
    color,
    size,
    sizeAttenuation: false,
    transparent: true,
    opacity,
    depthWrite: false,
  });
  return new Points(geometry, material);
}

function createStarfield(radius: number) {
  const starfield = new Group();
  starfield.name = 'aegis-starfield';
  starfield.add(
    createStarLayer(radius * 7.5, 520, '#9fc9ff', 1.15, 0.46, 0.2),
    createStarLayer(radius * 7.45, 56, '#f3e8c9', 2.15, 0.56, 1.6),
  );
  return starfield;
}

function disposeObject(object: Group | Mesh) {
  object.traverse((child) => {
    if (child instanceof Mesh || child instanceof Points) {
      child.geometry.dispose();
      if (Array.isArray(child.material)) child.material.forEach((material) => material.dispose());
      else child.material.dispose();
    }
  });
}

export const Globe = forwardRef<GlobeHandle, Props>(function Globe(
  { trackedObjects, mode, conjunctionAlert, trajectoryResult, className = '' },
  ref,
) {
  const globeRef = useRef<GlobeMethods | undefined>(undefined);
  const prefersReducedMotion = useReducedMotion();
  const requestedPointOfView = useRef<{
    target: { lat?: number; lng?: number; altitude?: number };
    transitionMs: number;
  } | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const bloomRef = useRef<UnrealBloomPass | null>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const [isGlobeReady, setIsGlobeReady] = useState(false);
  const [hudMarkers, setHudMarkers] = useState<HudCluster[]>([]);
  const globeMaterial = useMemo(() => new MeshPhongMaterial({
    color: '#bfd6f5',
    emissive: '#071a36',
    emissiveIntensity: 0.28,
    specular: '#42689a',
    shininess: 9,
    bumpScale: 0.72,
  }), []);

  useImperativeHandle(ref, () => ({
    pointOfView: (target, transitionMs = 0) => {
      requestedPointOfView.current = { target, transitionMs };
      globeRef.current?.pointOfView(target, transitionMs);
    },
  }), []);

  useEffect(() => {
    const el = containerRef.current;
    if (!el) return;
    const observer = new ResizeObserver(([entry]) => {
      // ResizeObserver reports the container's fractional/subpixel width
      // (e.g. 389.984375px). react-globe.gl's canvas sizing can round that
      // up by a hair when it's applied, making the canvas a fraction of a
      // pixel wider than the container that measured it — enough to tip
      // the page into a horizontal scrollbar at some viewport widths.
      // Flooring here guarantees the canvas is never larger than its
      // container, at the actual root cause rather than clipping the
      // symptom with overflow-x: hidden.
      if (entry) {
        setSize({
          width: Math.floor(entry.contentRect.width),
          height: Math.floor(entry.contentRect.height),
        });
      }
    });
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  // Display-only background traffic: only on /monitor, only in the live/conjunction views (never landing,
  // transition or trajectory, and not while the persistent globe sits hidden behind other pages).
  const onMonitor = useLocation().pathname === '/monitor' && (mode === 'live' || mode === 'conjunction');
  const trafficSelection = useTrafficSelection(useMemo(() => trackedObjects.map((o) => o.norad_id), [trackedObjects]), onMonitor);
  const trafficPositionsRef = useBackgroundTraffic(globeRef, isGlobeReady, onMonitor, trafficSelection.sats, Boolean(prefersReducedMotion));
  const hoverMode = parseHoverMode(useLocation().search);

  const flaggedIds = useMemo(() => conjunctionAlert ? new Set([conjunctionAlert.primary_id, conjunctionAlert.secondary_id]) : new Set<string>(), [conjunctionAlert]);

  const points = useMemo<GlobePoint[]>(() => trackedObjects.map((obj) => ({
    norad_id: obj.norad_id,
    name: obj.name,
    ...positionKmToGeo(obj.position_km, obj.timestamp_utc),
    isFlagged: flaggedIds.has(obj.norad_id),
  })), [trackedObjects, flaggedIds]);

  const trails = useMemo<OrbitTrail[]>(() => points.map((point, index) => ({
    ...point,
    endLat: Math.max(-82, Math.min(82, point.lat + (index % 2 === 0 ? 8 : -8))),
    endLng: point.lng - 34,
    endAlt: point.alt,
  })), [points]);

  const rings = useMemo(() => (mode === 'conjunction' || mode === 'landing') && conjunctionAlert ? points.filter((point) => point.isFlagged) : [], [mode, conjunctionAlert, points]);

  const pathsData = useMemo(() => {
    if (mode !== 'trajectory' || !trajectoryResult) return [];
    const convert = (path: [number, number, number][]) => path.map(([lng, lat, alt]) => ({ lat, lng, alt }));
    return [
      { coords: convert(trajectoryResult.nominal_path_primary), color: '#64748b' },
      { coords: convert(trajectoryResult.nominal_path_secondary), color: '#64748b' },
      { coords: convert(trajectoryResult.maneuvered_path), color: ACCENT_COLOR },
    ];
  }, [mode, trajectoryResult]);

  // Landing's hero copy sits on the left of the (full-bleed) panel, so the
  // globe itself is shifted right within that same panel to stop it
  // overlapping the text — nothing else (panel, starfield) moves.
  // Applied as a plain screen-space offset, kept in sync between the visual
  // canvas transform below and the HUD label coordinates here so dots and
  // labels stay aligned.
  const landingXOffset = mode === 'landing' ? size.width * 0.22 : 0;

  const updateHud = useCallback(() => {
    const globe = globeRef.current;
    if (!globe) return;
    const screenPoints = points
      .map((point) => {
        const screen = globe.getScreenCoords(point.lat, point.lng, point.alt);
        return { ...point, x: screen.x + landingXOffset, y: screen.y };
      })
      .filter((point) => point.x >= 0 && point.y >= 0 && point.x <= size.width && point.y <= size.height);
    setHudMarkers(clusterHudPoints(screenPoints));
  }, [points, size, landingXOffset]);

  const configureScene = useCallback(() => {
    const globe = globeRef.current;
    if (!globe) return;
    const scene = globe.scene();
    const previousAtmosphere = scene.getObjectByName('aegis-fresnel-atmosphere') as Mesh | undefined;
    const previousStarfield = scene.getObjectByName('aegis-starfield') as Group | undefined;
    previousAtmosphere?.removeFromParent();
    previousStarfield?.removeFromParent();
    if (previousAtmosphere) disposeObject(previousAtmosphere);
    if (previousStarfield) disposeObject(previousStarfield);
    const atmosphere = createAtmosphere(globe.getGlobeRadius());
    const starfield = createStarfield(globe.getGlobeRadius());
    starfield.rotation.y = -0.42;
    scene.add(atmosphere);
    scene.add(starfield);

    const renderer = globe.renderer();
    renderer.toneMapping = ACESFilmicToneMapping;
    renderer.toneMappingExposure = 1.04;

    const directional = globe.lights().find((light) => light instanceof DirectionalLight);
    if (directional instanceof DirectionalLight) {
      directional.position.set(1.3, 0.65, 1.5);
      directional.intensity = 1.45;
    }
    let fillLight = scene.getObjectByName('aegis-twilight-fill') as AmbientLight | undefined;
    if (!fillLight) {
      fillLight = new AmbientLight('#89b6eb', 0.48);
      fillLight.name = 'aegis-twilight-fill';
      scene.add(fillLight);
    }

    const composer = globe.postProcessingComposer();
    if (!bloomRef.current) {
      bloomRef.current = new UnrealBloomPass(new Vector2(size.width || 1, size.height || 1), 0.82, 0.3, 0.8);
      composer.addPass(bloomRef.current);
    }
    bloomRef.current.setSize(size.width || 1, size.height || 1);

    const requestedView = requestedPointOfView.current;
    if (requestedView) globe.pointOfView(requestedView.target, requestedView.transitionMs);

    updateHud();
  }, [size, updateHud]);

  const handleGlobeReady = useCallback(() => {
    configureScene();
    setIsGlobeReady(true);
  }, [configureScene]);

  // Landing is an ambient background. The persistent globe must restore
  // controls when its same canvas becomes the interactive Monitor surface.
  useEffect(() => {
    if (!isGlobeReady) return;
    const globe = globeRef.current;
    if (!globe) return;
    const controls = globe.controls();
    const isAmbient = mode === 'landing' || mode === 'transition';
    controls.autoRotate = mode === 'landing';
    controls.autoRotateSpeed = mode === 'landing' ? 0.28 : 0;
    controls.enableZoom = !isAmbient;
    controls.enablePan = !isAmbient;
  }, [isGlobeReady, mode]);

  useEffect(() => {
    const timer = window.setInterval(updateHud, 280);
    return () => window.clearInterval(timer);
  }, [updateHud]);

  useEffect(() => () => {
    const globe = globeRef.current;
    if (globe) {
      const scene = globe.scene();
      const atmosphere = scene.getObjectByName('aegis-fresnel-atmosphere') as Mesh | undefined;
      const starfield = scene.getObjectByName('aegis-starfield') as Group | undefined;
      atmosphere?.removeFromParent();
      starfield?.removeFromParent();
      if (atmosphere) disposeObject(atmosphere);
      if (starfield) disposeObject(starfield);
      scene.getObjectByName('aegis-twilight-fill')?.removeFromParent();
      if (bloomRef.current) globe.postProcessingComposer().removePass(bloomRef.current);
    }
    bloomRef.current = null;
    globeMaterial.dispose();
  }, [globeMaterial]);

  return (
    <div ref={containerRef} className={`relative h-full w-full overflow-hidden ${className}`}>
      <div className="globe-star-dust" aria-hidden="true" />
      <motion.div
        className="h-full w-full"
        initial={false}
        animate={{ x: landingXOffset }}
        transition={{
          duration: prefersReducedMotion ? 0 : mode === 'transition' ? 0.82 : 0.28,
          ease: [0.22, 1, 0.36, 1],
        }}
      >
      <ReactGlobe
        ref={globeRef}
        width={size.width || undefined}
        height={size.height || undefined}
        onGlobeReady={handleGlobeReady}
        globeImageUrl={earthNightTexture}
        bumpImageUrl={earthBumpTexture}
        globeMaterial={globeMaterial}
        showAtmosphere={false}
        backgroundColor="rgba(0,0,0,0)"
        pointsData={points}
        pointLat="lat"
        pointLng="lng"
        pointAltitude="alt"
        pointColor={(point) => ((point as GlobePoint).isFlagged ? DANGER_COLOR : ACCENT_COLOR)}
        pointRadius={(point) => ((point as GlobePoint).isFlagged ? 0.72 : 0.42)}
        pointResolution={32}
        pointLabel={(point) => `${(point as GlobePoint).name} (${(point as GlobePoint).norad_id})`}
        arcsData={mode === 'live' || mode === 'conjunction' || mode === 'landing' ? trails : []}
        arcStartLat="lat"
        arcStartLng="lng"
        arcStartAltitude="alt"
        arcEndLat="endLat"
        arcEndLng="endLng"
        arcEndAltitude="endAlt"
        arcColor={(trail: object) => (trail as OrbitTrail).isFlagged
          ? ['rgba(255,91,103,0.72)', 'rgba(255,91,103,0.02)']
          : ['rgba(96,165,250,0.46)', 'rgba(96,165,250,0.01)']}
        arcStroke={0.22}
        arcAltitude={0.065}
        arcDashLength={0.48}
        arcDashGap={0.72}
        arcDashAnimateTime={5200}
        arcsTransitionDuration={700}
        ringsData={rings}
        ringLat="lat"
        ringLng="lng"
        ringAltitude="alt"
        ringColor={() => (t: number) => `rgba(255, 91, 103, ${Math.pow(1 - t, 1.8)})`}
        ringResolution={160}
        ringMaxRadius={4.2}
        ringPropagationSpeed={1.35}
        ringRepeatPeriod={3100}
        pathsData={pathsData}
        pathPoints="coords"
        pathPointLat="lat"
        pathPointLng="lng"
        pathPointAlt="alt"
        pathColor="color"
        pathDashLength={0.1}
        pathDashGap={0.05}
        pathDashAnimateTime={3000}
      />
      </motion.div>
      {/* Hover card for the display-only background satellites (renders nothing until one is hovered) */}
      <BackgroundHover globeRef={globeRef} ready={isGlobeReady} enabled={onMonitor && hoverMode !== 'off'} orbit={hoverMode === 'full'} sats={trafficSelection.sats} positionsRef={trafficPositionsRef} />
      {mode !== 'transition' && (
        <GlobeHud
          markers={hudMarkers.map(({ norad_id, name, x, y, isFlagged, count, members }) => ({
            noradId: norad_id,
            name,
            x,
            y,
            flagged: isFlagged,
            count,
            members: members.map((m) => ({ noradId: m.norad_id, name: m.name })),
          }))}
        />
      )}
    </div>
  );
});
