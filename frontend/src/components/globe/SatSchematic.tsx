/**
 * SatSchematic: hand-drawn inline-SVG line art for the Monitor hover card, one per constellation family.
 * ~120x70, stroke-only, token colours, no images and no network. These are GENERIC schematics (not scale
 * drawings of any real spacecraft); the card says so under the drawing.
 */
import type { SatArt } from '../../lib/satArt';

const BUS = 'var(--color-accent-light)';
const PANEL = 'var(--color-text-secondary)';
const GRID = 'var(--color-border-light)';
const EARTH = 'var(--color-border-light)';

/** Vertical + horizontal solar-cell grid lines inside a rectangle. */
function Cells({ x, y, w, h, cols, rows }: { x: number; y: number; w: number; h: number; cols: number; rows: number }) {
  const lines = [];
  for (let c = 1; c < cols; c += 1) lines.push(<line key={`c${c}`} x1={x + (w * c) / cols} y1={y} x2={x + (w * c) / cols} y2={y + h} />);
  for (let r = 1; r < rows; r += 1) lines.push(<line key={`r${r}`} x1={x} y1={y + (h * r) / rows} x2={x + w} y2={y + (h * r) / rows} />);
  return <g stroke={GRID}>{lines}</g>;
}

function Body({ art }: { art: SatArt }) {
  switch (art) {
    case 'starlink': // flat-panel bus + one long solar array
      return (
        <>
          <rect x="58" y="8" width="54" height="46" stroke={PANEL} />
          <Cells x={58} y={8} w={54} h={46} cols={6} rows={4} />
          <line x1="46" y1="31" x2="58" y2="31" stroke={PANEL} />
          <rect x="14" y="22" width="32" height="18" stroke={BUS} />
          <g stroke={GRID}><line x1="20" y1="28" x2="40" y2="28" /><line x1="20" y1="31" x2="40" y2="31" /><line x1="20" y1="34" x2="40" y2="34" /></g>
          <circle cx="30" cy="46" r="2" stroke={BUS} />
        </>
      );
    case 'oneweb': // small box bus, two symmetric arrays, antenna masts
      return (
        <>
          <rect x="50" y="26" width="20" height="20" stroke={BUS} />
          <rect x="10" y="28" width="34" height="16" stroke={PANEL} />
          <Cells x={10} y={28} w={34} h={16} cols={4} rows={2} />
          <rect x="76" y="28" width="34" height="16" stroke={PANEL} />
          <Cells x={76} y={28} w={34} h={16} cols={4} rows={2} />
          <line x1="44" y1="36" x2="50" y2="36" stroke={PANEL} />
          <line x1="70" y1="36" x2="76" y2="36" stroke={PANEL} />
          <line x1="56" y1="26" x2="56" y2="15" stroke={BUS} />
          <circle cx="56" cy="13" r="2" stroke={BUS} />
          <line x1="64" y1="26" x2="64" y2="18" stroke={BUS} />
          <path d="M60 18 Q64 14 68 18" stroke={BUS} />
        </>
      );
    case 'iridium': // prism bus, three fanned L-band antenna panels, one wing
      return (
        <>
          <polygon points="52,18 68,18 72,34 68,50 52,50 48,34" stroke={BUS} />
          <polygon points="48,24 20,16 20,24 48,30" stroke={PANEL} />
          <polygon points="48,32 20,31 20,39 48,38" stroke={PANEL} />
          <polygon points="48,40 20,46 20,54 48,46" stroke={PANEL} />
          <rect x="76" y="22" width="34" height="24" stroke={PANEL} />
          <Cells x={76} y={22} w={34} h={24} cols={4} rows={2} />
          <line x1="72" y1="34" x2="76" y2="34" stroke={PANEL} />
        </>
      );
    default: // generic bus: box, two wings, dish
      return (
        <>
          <rect x="48" y="24" width="24" height="24" stroke={BUS} />
          <rect x="8" y="30" width="34" height="12" stroke={PANEL} />
          <Cells x={8} y={30} w={34} h={12} cols={4} rows={2} />
          <rect x="78" y="30" width="34" height="12" stroke={PANEL} />
          <Cells x={78} y={30} w={34} h={12} cols={4} rows={2} />
          <line x1="42" y1="36" x2="48" y2="36" stroke={PANEL} />
          <line x1="72" y1="36" x2="78" y2="36" stroke={PANEL} />
          <path d="M52 24 Q60 10 68 24" stroke={BUS} />
          <line x1="60" y1="17" x2="60" y2="24" stroke={BUS} />
          <circle cx="60" cy="13" r="1.6" stroke={BUS} />
        </>
      );
  }
}

export function SatSchematic({ art }: { art: SatArt }) {
  return (
    <svg viewBox="0 0 120 70" width="120" height="70" fill="none" strokeWidth="1" strokeLinejoin="round" aria-hidden="true">
      <path d="M6 67 Q60 58 114 67" stroke={EARTH} strokeDasharray="2 3" />
      <Body art={art} />
    </svg>
  );
}
