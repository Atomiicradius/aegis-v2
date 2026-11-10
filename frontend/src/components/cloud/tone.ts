export type Tone = 'accent' | 'success' | 'warning' | 'danger' | 'muted';

export const TONE_TEXT: Record<Tone, string> = {
  accent: 'text-accent-light',
  success: 'text-success-light',
  warning: 'text-warning-light',
  danger: 'text-danger-light',
  muted: 'text-text-secondary',
};
