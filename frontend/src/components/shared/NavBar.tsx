import { NavLink, useLocation } from 'react-router-dom';

import { useGlobeStage } from '../globe/GlobeStage';
import { useNegotiationStore, type ConnectionStatus } from '../../store/useNegotiationStore';

const links = [
  { to: '/monitor', label: 'Monitor' },
  { to: '/negotiate', label: 'Negotiate' },
  { to: '/history', label: 'History' },
  { to: '/cloud', label: 'Cloud' },
  { to: '/about', label: 'About' },
];

const STATUS_DISPLAY: Record<ConnectionStatus, { label: string; dotClassName: string }> = {
  connecting: { label: 'Connecting', dotClassName: 'bg-warning shadow-[0_0_6px_var(--color-warning)]' },
  connected: { label: 'Live', dotClassName: 'bg-success shadow-[0_0_6px_var(--color-success)]' },
  reconnecting: {
    label: 'Reconnecting',
    dotClassName: 'bg-warning shadow-[0_0_6px_var(--color-warning)] animate-pulse',
  },
  disconnected: { label: 'Disconnected', dotClassName: 'bg-danger shadow-[0_0_6px_var(--color-danger)]' },
};

export function NavBar() {
  const connectionStatus = useNegotiationStore((s) => s.connectionStatus);
  const location = useLocation();
  const { navigateToMonitor } = useGlobeStage();
  const { label, dotClassName } = STATUS_DISPLAY[connectionStatus];

  return (
    <header className="flex h-16 items-center justify-between border-b border-border bg-surface px-4 sm:px-8">
      <NavLink
        to="/"
        className="font-mono text-xs font-semibold uppercase tracking-[0.16em] text-text-primary focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
      >
        Aegis<span className="text-accent">/</span>Ops
      </NavLink>
      <nav className="flex items-center gap-3 sm:gap-6" aria-label="Primary navigation">
        {links.map((link) => (
          <NavLink
            key={link.to}
            to={link.to}
            onClick={(event) => {
              if (link.to === '/monitor' && location.pathname === '/') {
                event.preventDefault();
                navigateToMonitor();
              }
            }}
            className={({ isActive }) =>
              isActive
                ? 'border-b border-accent pb-1 font-mono text-[10px] font-medium uppercase tracking-wider text-text-primary'
                : 'border-b border-transparent pb-1 font-mono text-[10px] uppercase tracking-wider text-text-secondary transition-colors hover:border-border-light hover:text-text-primary focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent'
            }
          >
            {link.label}
          </NavLink>
        ))}
      </nav>
      <div className="flex items-center gap-2 font-mono text-[9px] uppercase tracking-wider text-text-muted">
        <span className={`h-2 w-2 rounded-full ${dotClassName}`} />
        {label}
      </div>
    </header>
  );
}
