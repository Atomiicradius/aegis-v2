import type { CSSProperties } from 'react';

/** Four corner brackets for an instrument panel; the parent must be `relative`. Colour via --cloud-bracket. */
export function CornerBrackets({ color }: { color?: string }) {
  return (
    <span
      aria-hidden="true"
      className="cloud-brackets pointer-events-none absolute inset-0"
      style={color ? ({ '--cloud-bracket': color } as CSSProperties) : undefined}
    >
      <i />
      <i />
      <i />
      <i />
    </span>
  );
}
