/** Which hand-drawn schematic (components/globe/SatSchematic.tsx) to show for a background satellite's pool group. */
export type SatArt = 'starlink' | 'oneweb' | 'iridium' | 'generic';

export function artForGroup(group: string): SatArt {
  if (group === 'starlink') return 'starlink';
  if (group === 'oneweb') return 'oneweb';
  if (group.toLowerCase().startsWith('iridium')) return 'iridium';
  return 'generic';
}
