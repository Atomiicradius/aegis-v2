import { animate, stagger } from 'animejs';
import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { Link } from 'react-router-dom';
import { useGlobeStage } from '../components/globe/GlobeStage';

/* -- cards data ------------------------------------------- */
const CARDS = [
  { id: 'fleet', tag: 'PRIORITY QUEUE', meta: '2 / 5 ACTIVE',   title: 'ISS - CSS',             sub: 'CONJUNCTION ALERT',    badge: { label: 'DANGER', tone: 'danger'  as const }, detail: 'TCA in 04h 22m',   value: '3.202 km miss'   },
  { id: 'neg',   tag: 'NEXT ACTION',    meta: 'ETA 00H 48M',    title: 'Operator Negotiation',  sub: 'MANEUVER COST CALC',   badge: { label: 'ARMED',  tone: 'warning' as const }, detail: 'dv budget ok',     value: '1.8 m/s estimate'},
  { id: 'scan',  tag: 'SYSTEM SCAN',    meta: 'LIVE',            title: 'SGP4 Propagator',       sub: 'TLE REFRESH NOMINAL',  badge: { label: 'ONLINE', tone: 'success' as const }, detail: 'Pairs screened',   value: '412 this cycle'  },
  { id: 'val',   tag: 'RESOLUTION LOG', meta: 'UPCOMING',        title: 'Validation Gate',       sub: '6H SWEEP PENDING',     badge: { label: 'GATED',  tone: 'warning' as const }, detail: 'Safety check',     value: 'pending sign-off'},
];

type Tone = 'success' | 'warning' | 'danger';

function StatusDot({ tone = 'success' }: { tone?: Tone }) {
  const cls: Record<Tone, string> = {
    success: 'bg-success shadow-[0_0_6px_var(--color-success)]',
    warning: 'bg-warning shadow-[0_0_6px_var(--color-warning)]',
    danger:  'bg-danger  shadow-[0_0_6px_var(--color-danger)]',
  };
  return <span aria-hidden="true" className={`inline-block h-1.5 w-1.5 rounded-full ${cls[tone]}`} />;
}

function Badge({ label, tone }: { label: string; tone: Tone }) {
  const cls: Record<Tone, string> = {
    success: 'border-success/30 bg-success-muted/60 text-success-light',
    warning: 'border-warning/30 bg-warning-muted/60 text-warning-light',
    danger:  'border-danger/30  bg-danger-muted/60  text-danger-light',
  };
  return (
    <span className={`flex items-center gap-1.5 border px-2 py-0.5 font-mono text-[9px] uppercase tracking-wider ${cls[tone]}`}>
      <StatusDot tone={tone} /> {label}
    </span>
  );
}

export default function Landing() {
  const { setGlobeStage, navigateToMonitor, phase } = useGlobeStage();

  /* ── animation refs ── */
  const heroStatusRef  = useRef<HTMLDivElement>(null);
  const heroHeadRef    = useRef<HTMLHeadingElement>(null);
  const heroBodyRef    = useRef<HTMLParagraphElement>(null);
  const heroCTARef     = useRef<HTMLDivElement>(null);
  const cardsRef       = useRef<HTMLElement>(null);
  const pipelineRef    = useRef<HTMLOListElement>(null);

  /* ── hero entry sequence (once on mount) ── */
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;

    const tl = [
      { el: heroStatusRef.current,  delay: 0   },
      { el: heroHeadRef.current,    delay: 90  },
      { el: heroBodyRef.current,    delay: 320 },
      { el: heroCTARef.current,     delay: 420 },
    ];

    // headline words get individual stagger
    const headlineWords = heroHeadRef.current
      ? Array.from(heroHeadRef.current.children)
      : [];

    tl.forEach(({ el, delay }) => {
      if (!el) return;
      animate(el, {
        opacity: [0, 1],
        translateY: [14, 0],
        duration: 480,
        delay,
        easing: 'easeOutCubic',
      });
    });

    if (headlineWords.length > 0) {
      animate(headlineWords, {
        opacity: [0, 1],
        translateY: [18, 0],
        duration: 440,
        delay: stagger(80, { start: 110 }),
        easing: 'easeOutCubic',
      });
    }
  }, []);

  useEffect(() => {
    setGlobeStage({ mode: 'hero' });
  }, [setGlobeStage]);

  /* ── scroll-triggered stagger for bottom cards ── */
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const el = cardsRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        observer.disconnect();
        const cards = Array.from(el.children) as HTMLElement[];
        animate(cards, {
          opacity: [0, 1],
          translateY: [16, 0],
          duration: 360,
          delay: stagger(65),
          easing: 'easeOutCubic',
        });
      },
      { threshold: 0.12 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  /* ── scroll-triggered stagger for pipeline steps ── */
  useEffect(() => {
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const el = pipelineRef.current;
    if (!el) return;

    const observer = new IntersectionObserver(
      ([entry]) => {
        if (!entry.isIntersecting) return;
        observer.disconnect();
        const steps = Array.from(el.children) as HTMLElement[];
        animate(steps, {
          opacity: [0, 1],
          translateX: [-10, 0],
          duration: 320,
          delay: stagger(70),
          easing: 'easeOutCubic',
        });
      },
      { threshold: 0.1 },
    );
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  return (
    <main className="ops-grid min-h-screen text-text-primary overflow-x-hidden">

      {/* ── HERO ──────────────────────────────────────── */}
      <section
        className="relative border-b border-border overflow-hidden"
        style={{ minHeight: 'clamp(560px,82vh,860px)' }}
      >
        {/* Readability gradient so the left-side copy stays legible over the
            full-bleed globe — same left-to-right treatment Monitor uses. */}
        <div className="pointer-events-none absolute inset-0 bg-[linear-gradient(90deg,rgba(10,14,23,0.96)_0%,rgba(10,14,23,0.86)_32%,rgba(10,14,23,0.4)_50%,transparent_66%)]" />

        {/* HUD label overlay */}
        <div
          className="pointer-events-none absolute z-10 font-mono text-[9px] uppercase tracking-widest"
          style={{ top: '18%', right: '6%' }}
        >
          <div className="border border-border-light bg-surface/80 px-2.5 py-1.5 backdrop-blur-sm">
            <div className="text-text-muted">Target</div>
            <div className="mt-0.5 font-semibold text-text-primary">ISS / CSS</div>
            <div className="text-danger-light">Miss: 3.202 km</div>
          </div>
          <div className="mt-1 ml-auto h-px w-10 bg-border-light" />
        </div>

        <div className="scan-beam" aria-hidden="true" />

        {/* LEFT copy */}
        <motion.div
          animate={phase === 'hero-exit' || phase === 'monitor-enter'
            ? { opacity: 0, y: -12 }
            : { opacity: 1, y: 0 }}
          transition={{ duration: 0.22, ease: [0.22, 1, 0.36, 1] }}
          className="relative z-10 flex h-full flex-col justify-center px-6 pt-16 pb-10 sm:px-10 lg:px-14 lg:pt-20 lg:pb-14"
          style={{ maxWidth: '50%', minHeight: 'inherit' }}
        >
          {/* status row */}
          <div
            ref={heroStatusRef}
            className="mb-8 flex flex-wrap items-center gap-x-5 gap-y-2 font-mono text-[10px] uppercase tracking-[0.18em] text-text-muted"
            style={{ opacity: 0 }}
          >
            <span className="flex items-center gap-2 border border-success/25 bg-success-muted/40 px-2.5 py-1 text-success-light">
              <StatusDot tone="success" /> System Status&nbsp;&middot;&nbsp;Nominal
            </span>
            <span>AEGIS / OPS-01</span>
            <span>UTC synchronized</span>
          </div>

          {/* headline — each span is a stagger target */}
          <h1
            ref={heroHeadRef}
            className="font-sans font-bold uppercase leading-[0.88] tracking-[-0.04em]"
            style={{ fontSize: 'clamp(3.2rem,8vw,7.4rem)' }}
          >
            <span className="block text-text-primary" style={{ opacity: 0 }}>Detect.</span>
            <span className="block text-text-primary" style={{ opacity: 0 }}>Negotiate.</span>
            <span
              className="block text-accent-light"
              style={{ opacity: 0, textShadow: '0 0 48px rgba(96,165,250,0.35)' }}
            >
              Resolve.
            </span>
          </h1>

          {/* body */}
          <p
            ref={heroBodyRef}
            className="mt-8 max-w-[52ch] text-sm leading-[1.75] text-text-secondary sm:text-base"
            style={{ opacity: 0 }}
          >
            Aegis screens every close approach in low Earth orbit, assigns maneuver
            responsibility through deterministic cost functions, and validates the
            resulting trajectory before a plan is released.
          </p>

          {/* CTAs */}
          <div ref={heroCTARef} className="mt-9 flex flex-wrap items-center gap-4" style={{ opacity: 0 }}>
            <button
              aria-label="Play demo"
              className="flex h-11 w-11 items-center justify-center rounded-full border border-border-light bg-surface-secondary text-text-secondary transition hover:border-accent hover:text-accent-light"
            >
              <svg viewBox="0 0 16 16" fill="currentColor" className="h-4 w-4 translate-x-0.5">
                <path d="M5 3.5l8 4.5-8 4.5V3.5z" />
              </svg>
            </button>

            <Link
              to="/monitor"
              onClick={(event) => {
                event.preventDefault();
                navigateToMonitor();
              }}
              className="flex items-center gap-2 bg-accent px-5 py-2.5 font-mono text-[11px] font-semibold uppercase tracking-wider text-white transition duration-200 hover:bg-accent-dark active:translate-y-px"
            >
              Open Monitor
              <svg viewBox="0 0 16 16" fill="none" stroke="currentColor" strokeWidth="1.5" className="h-3.5 w-3.5">
                <path d="M3 8h10M9 4l4 4-4 4" strokeLinecap="round" strokeLinejoin="round" />
              </svg>
            </Link>

            <Link
              to="/negotiate"
              className="border-b border-border-light px-1 py-2.5 font-mono text-[11px] uppercase tracking-wider text-text-secondary transition hover:border-accent hover:text-text-primary"
            >
              View negotiation ledger +
            </Link>

            <Link
              to="/cloud"
              className="border-b border-border-light px-1 py-2.5 font-mono text-[11px] uppercase tracking-wider text-text-secondary transition hover:border-accent hover:text-text-primary"
            >
              Cluster scope +
            </Link>
          </div>
        </motion.div>

      </section>

      {/* ── BOTTOM CARDS ──────────────────────────────── */}
      <section
        ref={cardsRef}
        className="mx-auto w-full max-w-[1440px] grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 border-x border-b border-border"
        aria-label="Mission overview"
      >
        {CARDS.map((card, i) => (
          <div
            key={card.id}
            className={`group relative flex flex-col gap-3 p-5 sm:p-6 transition-colors duration-200 hover:bg-surface-secondary/30 ${i > 0 ? 'border-l border-border' : ''}`}
            style={{ opacity: 0 }}
          >
            <div className="flex items-start justify-between gap-2">
              <div>
                <p className="font-mono text-[9px] uppercase tracking-[0.2em] text-text-muted">{card.tag}</p>
                <p className="mt-0.5 font-mono text-[9px] uppercase tracking-wider text-accent-light">{card.meta}</p>
              </div>
              <Badge label={card.badge.label} tone={card.badge.tone} />
            </div>
            <div>
              <h3 className="text-base font-semibold leading-tight text-text-primary">{card.title}</h3>
              <p className="mt-0.5 font-mono text-[9px] uppercase tracking-widest text-text-muted">{card.sub}</p>
            </div>
            <div className="mt-auto flex items-end justify-between border-t border-border pt-3">
              <span className="font-mono text-[10px] text-text-muted">{card.detail}</span>
              <span className="font-mono text-xs font-medium text-text-primary">{card.value}</span>
            </div>
            <div className="absolute bottom-0 left-0 h-[2px] w-0 bg-accent transition-all duration-300 group-hover:w-full" />
          </div>
        ))}
      </section>

      {/* ── PIPELINE ──────────────────────────────────── */}
      <section className="mx-auto max-w-[1440px] border-x border-b border-border lg:grid lg:grid-cols-[0.92fr_1.35fr_0.73fr]">
        <div className="border-b border-border p-6 sm:p-8 lg:border-b-0 lg:border-r">
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-accent-light">Execution chain</p>
          <h2 className="mt-3 max-w-sm text-3xl font-semibold leading-tight tracking-tight">
            One incident. Four accountable stages.
          </h2>
          <p className="mt-4 max-w-[46ch] text-sm leading-6 text-text-secondary">
            The interface exposes the handoff between physics, negotiation, validation, and resolution.
            Each emitted object remains inspectable.
          </p>
        </div>

        <ol ref={pipelineRef}>
          {([
            { code: 'MON', state: 'ACTIVE', tone: 'success' as Tone, title: 'Conjunction monitor',  detail: 'CelesTrak TLE -> SGP4 propagation -> pairwise screen', output: 'ConjunctionAlert'    },
            { code: 'NEG', state: 'ARMED',  tone: 'success' as Tone, title: 'Operator negotiation', detail: 'Mission value + fuel margin + maneuver cost',            output: 'NegotiationMessage' },
            { code: 'VAL', state: 'GATED',  tone: 'warning' as Tone, title: 'Safety validation',    detail: 'Six-hour propagation against the tracked set',            output: 'approve / reject'   },
            { code: 'RES', state: 'READY',  tone: 'success' as Tone, title: 'Resolution record',    detail: 'Maneuver, delta-v, execution UTC, residual risk',          output: 'Resolution'         },
          ]).map((step, index) => (
            <li key={step.code} className="group grid grid-cols-[3.5rem_minmax(0,1fr)_auto] gap-4 px-6 py-5 transition-colors duration-200 hover:bg-surface-secondary/45 sm:px-8 divide-y divide-border [&:not(:last-child)]:border-b [&:not(:last-child)]:border-border" style={{ opacity: 0 }}>
              <div className="font-mono text-xs text-text-muted">
                <span className="block text-accent-light">{step.code}</span>
                <span className="mt-1 block text-[9px]">0{index + 1}</span>
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <StatusDot tone={step.tone} />
                  <h3 className="text-sm font-semibold text-text-primary">{step.title}</h3>
                </div>
                <p className="mt-1.5 text-xs leading-5 text-text-secondary">{step.detail}</p>
              </div>
              <div className="text-right font-mono text-[9px] uppercase tracking-wider text-text-muted">
                <span className="block text-text-secondary">{step.state}</span>
                <span className="mt-2 hidden border-t border-border pt-2 xl:block">{step.output}</span>
              </div>
            </li>
          ))}
        </ol>

        <aside className="border-t border-border bg-surface-muted/70 p-6 sm:p-8 lg:border-l lg:border-t-0">
          <p className="font-mono text-[10px] uppercase tracking-[0.2em] text-text-muted">Audit contract</p>
          <div className="mt-6 space-y-6">
            <div className="border-l-2 border-success pl-4">
              <p className="font-mono text-[10px] uppercase tracking-wider text-success-light">Verified source</p>
              <p className="mt-2 text-sm font-medium">Safety numbers originate in deterministic Python.</p>
            </div>
            <div className="border-l-2 border-accent pl-4">
              <p className="font-mono text-[10px] uppercase tracking-wider text-accent-light">Narration boundary</p>
              <p className="mt-2 text-sm font-medium">The LLM explains a computed decision; it does not make one.</p>
            </div>
            <div className="border-l-2 border-warning pl-4">
              <p className="font-mono text-[10px] uppercase tracking-wider text-warning-light">Fail-safe</p>
              <p className="mt-2 text-sm font-medium">Unsafe or non-convergent plans escalate as valid outcomes.</p>
            </div>
          </div>
          <Link
            to="/negotiate"
            className="mt-10 inline-flex items-center gap-3 border-t border-border-light pt-4 font-mono text-xs uppercase tracking-wider text-text-primary transition-colors hover:text-accent-light focus-visible:outline-2 focus-visible:outline-offset-4 focus-visible:outline-accent"
          >
            Inspect negotiation record <span aria-hidden="true">-&gt;</span>
          </Link>
        </aside>
      </section>

      {/* ── FOOTER ────────────────────────────────────── */}
      <footer className="border-t border-border">
        <div className="mx-auto flex max-w-[1440px] flex-col gap-2 px-6 py-5 font-mono text-[9px] uppercase tracking-wider text-text-muted sm:flex-row sm:items-center sm:justify-between sm:px-8">
          <span>Aegis orbital safety demonstrator / CelesTrak + SGP4</span>
          <span>delta-v &middot; miss distance &middot; yield score - deterministic sources only</span>
        </div>
      </footer>
    </main>
  );
}
