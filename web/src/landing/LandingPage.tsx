import { ArrowRight } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { Link } from 'react-router'

import { useAuthStatus } from '@/api/session'
import { Plate } from '@/components/Instrument'
import { LogoMark } from '@/components/LogoMark'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Button } from '@/components/ui/button'
import { ROWS_TEXT, TIMEOUT_TEXT } from '@/lib/limits'
import { DrawPath, Reveal, Stagger, StaggerItem } from '@/motion'

import { Circuit } from './Circuit'
import { LiveDemo } from './LiveDemo'
import { Proof } from './Proof'

/**
 * What protects the data, as a stack of guards a question passes through. Each is a rule the backend
 * enforces (docs/CLAUDE.md §8, Phase 5); `stops` are real examples of what it refuses or limits, with
 * the limits at their default settings (`lib/limits.ts`, from config.py).
 */
const GUARDS: { title: string; text: string; stops: string[] }[] = [
  {
    title: 'Read-only by design',
    text: 'Questions run as a database role that can only read. A saved database is checked to be read-only before it is kept.',
    stops: ['INSERT', 'UPDATE', 'DELETE'],
  },
  {
    title: 'Every query checked first',
    text: 'One SELECT only: no writes, no schema changes, no system catalogs, no functions outside an allowlist.',
    stops: ['DROP · ALTER', 'pg_catalog', 'pg_sleep()', '; a second statement'],
  },
  {
    title: 'Limits on every run',
    text: 'A statement timeout and a row limit on every query, and rate limits on every account.',
    stops: [`${TIMEOUT_TEXT} timeout`, ROWS_TEXT, 'rate limits'],
  },
  {
    title: 'Personal data hidden',
    text: 'Columns marked as personal come back as “hidden” unless your role may see them.',
    // Real columns marked personal in semantic_layer/ (Northwind's and Chinook's customers).
    stops: ['contact_name → hidden', 'phone → hidden'],
  },
  {
    title: 'Tables and rows per role',
    text: 'Admins choose which tables each role sees, and which rows each member may read.',
    stops: ['tables per role', 'rows per member'],
  },
  {
    title: 'Everything audited',
    text: 'Every question, its SQL, who asked, the outcome and the cost go to an audit log.',
    stops: ['question', 'SQL', 'user', 'outcome', 'cost'],
  },
]

/** Your own data, in three steps, in the order they happen. */
const OWN = [
  {
    title: 'Start on the demo',
    text: 'Northwind (a shop) and Chinook (a music store) are ready to ask, free.',
  },
  {
    title: 'Bring your own key',
    text: 'Use your own LLM key from a fixed list of providers, Groq and Gemini among them. It is encrypted, and shown back only as its last four characters.',
  },
  {
    title: 'Connect your PostgreSQL',
    text: 'QueryMind reads your tables, drafts a data dictionary for you to review, then answers about your own data.',
  },
]

/**
 * The public front page (U8.4; rebuilt as an instrument's spec sheet in U9.1, ADR 027): a split hero
 * with the product working on the right, the agent as a circuit, the guards, the measured results,
 * and how to start. Outside the app shell, like the login page. Logged-out visitors opening `/` land
 * here (RequireLogin); logged-in ones see a way back into the canvas instead of "Create account".
 */
export default function LandingPage() {
  const loggedIn = useAuthStatus() === 'in'

  useEffect(() => {
    document.title = 'QueryMind · Ask your database in plain words'
  }, [])

  return (
    <div className="min-h-dvh bg-bg">
      <header className="sticky top-0 z-20 border-b bg-bg">
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2">
          <Link
            to="/welcome"
            className="flex min-h-11 items-center gap-2.5 rounded-lg font-heading text-[1.0625rem] font-semibold tracking-tight"
          >
            <LogoMark />
            QueryMind
          </Link>
          <span
            aria-hidden="true"
            className="hidden border-l pl-4 font-mono text-[0.6875rem] tracking-[0.08em] text-fg-muted xl:inline"
          >
            QM-1 · DATA ANALYST AGENT
          </span>
          <nav aria-label="On this page" className="hidden md:block">
            <ul className="flex gap-1 font-mono text-xs text-fg-muted">
              {[
                ['#how', 'How it works'],
                ['#safety', 'Safety'],
                ['#proof', 'Results'],
              ].map(([href, label]) => (
                <li key={href}>
                  <a
                    href={href}
                    className="inline-flex min-h-11 items-center rounded-lg px-3 transition-colors hover:text-fg"
                  >
                    {label}
                  </a>
                </li>
              ))}
              <li>
                <Link
                  to="/evals"
                  className="inline-flex min-h-11 items-center rounded-lg px-3 transition-colors hover:text-fg"
                >
                  Evals lab
                </Link>
              </li>
            </ul>
          </nav>
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            <ThemeSwitcher compact />
            <StartButtons loggedIn={loggedIn} compact />
          </div>
        </div>
      </header>

      <main>
        {/* The hero: the claim on the left, the product working on the right, on the instrument
            grid. No glow or blur behind text: the contrast test must be able to measure every word
            (e2e/a11y.spec.ts). */}
        <section aria-labelledby="hero-heading" className="canvas-grid relative border-b">
          <div className="mx-auto grid max-w-7xl items-start gap-x-12 gap-y-14 px-4 pt-12 pb-16 sm:pt-16 lg:grid-cols-12 lg:pt-20 lg:pb-24">
            {/* The hero's words show at once, with no fade: they hold the page's largest paint,
                and a browser counts text only once it is visible, so a fade-in made the page
                score slow (Lighthouse mobile LCP 5.7 s, 2026-11-24). The demo beside them still
                arrives with motion. */}
            <div className="lg:col-span-5 lg:pt-4">
              <Plate code="QM-1">AI DATA ANALYST · POSTGRESQL · READ-ONLY</Plate>
              <h1
                id="hero-heading"
                className="mt-7 text-5xl leading-[0.95] font-semibold tracking-[-0.035em] sm:text-6xl xl:text-7xl"
              >
                Ask your database.{' '}
                <span className="mt-2 block text-signal">See every step it takes.</span>
              </h1>
              <p className="mt-7 max-w-xl text-lg leading-relaxed text-fg-muted">
                QueryMind turns a plain-language question into checked, read-only SQL, runs it, and
                answers with a chart, the rows, the SQL and how sure it is. It shows its work,
                repairs its own mistakes, and asks back when a question is unclear.
              </p>
              <div className="mt-9 flex flex-wrap gap-3">
                <StartButtons loggedIn={loggedIn} />
                <Button asChild size="lg" variant="ghost">
                  <Link to="/evals">See the evals lab</Link>
                </Button>
              </div>
              {/* A readout of what is always true, as an instrument's fixed settings. */}
              <div>
                <dl className="mt-12 grid grid-cols-2 border-t border-l text-sm">
                  {[
                    ['MODE', 'Read-only role'],
                    ['SQL', 'Validated before it runs'],
                    ['PII', 'Hidden by role'],
                    ['AUDIT', 'Every question'],
                  ].map(([term, value]) => (
                    <div key={term} className="border-r border-b bg-bg px-3 py-2.5">
                      <dt className="label-mono">{term}</dt>
                      <dd className="mt-0.5 font-medium">{value}</dd>
                    </div>
                  ))}
                </dl>
              </div>
            </div>

            <Reveal delay={0.2} className="min-w-0 px-2 lg:col-span-7">
              <LiveDemo />
            </Reveal>
          </div>
        </section>

        <Band
          id="how"
          index="01"
          label="THE CIRCUIT"
          title="Seven steps, all on screen"
          intro="Most AI tools hand you an answer and ask you to trust it. QueryMind shows the path: the tables it chose, the SQL it wrote, every try that failed and why, and how sure it is."
        >
          <Circuit />
        </Band>

        <Band
          id="safety"
          index="02"
          label="GUARD STACK"
          title="Built so it can't change your data"
          intro="Every question passes through the same guards, in this order, before a single row comes back."
        >
          <Stagger as="ol" className="border-t">
            {GUARDS.map(({ title, text, stops }, index) => (
              <StaggerItem
                as="li"
                key={title}
                className="grid gap-x-8 gap-y-3 border-b py-6 md:grid-cols-[5rem_minmax(0,1fr)_minmax(0,1fr)] md:items-baseline"
              >
                <p aria-hidden="true" className="font-mono text-3xl font-semibold text-signal">
                  G{index + 1}
                </p>
                <div>
                  <h3 className="text-lg font-semibold tracking-tight">{title}</h3>
                  <p className="mt-1 text-fg-muted">{text}</p>
                </div>
                <div>
                  <p className="label-mono">{index === 2 ? 'LIMITS' : 'STOPS'}</p>
                  <ul className="mt-2 flex flex-wrap gap-1.5">
                    {stops.map((stop) => (
                      <li
                        key={stop}
                        className="rounded-[4px] border bg-surface px-2 py-0.5 font-mono text-xs"
                      >
                        {stop}
                      </li>
                    ))}
                  </ul>
                </div>
              </StaggerItem>
            ))}
          </Stagger>
        </Band>

        <Band
          id="proof"
          index="03"
          label="SPEC SHEET"
          title="Measured, not promised"
          intro="Every number below comes from a saved evaluation run, and says which report it is from."
        >
          <Proof />
        </Band>

        <Band id="own" index="04" label="YOUR DATA" title="Your key, your database">
          <ol className="grid gap-px overflow-hidden rounded-[6px] border bg-border md:grid-cols-3">
            {OWN.map(({ title, text }, index) => (
              <li key={title} className="bg-surface p-6">
                <p aria-hidden="true" className="flex items-center gap-3 label-mono">
                  STEP {String.fromCharCode(65 + index)}
                  {index < OWN.length - 1 && (
                    <ArrowRight className="hidden size-3.5 text-signal md:block" />
                  )}
                </p>
                <h3 className="mt-6 text-xl font-semibold tracking-tight">{title}</h3>
                <p className="mt-2 text-sm text-fg-muted">{text}</p>
              </li>
            ))}
          </ol>
        </Band>

        <section aria-labelledby="start-heading" className="border-t">
          <div className="mx-auto grid max-w-7xl gap-10 px-4 py-24 lg:grid-cols-12">
            <Reveal inView className="lg:col-span-8">
              <p aria-hidden="true" className="label-mono">
                <span className="text-signal">05</span> · READY WHEN YOU ARE
              </p>
              <h2
                id="start-heading"
                className="mt-5 text-4xl leading-[1.02] font-semibold tracking-[-0.025em] sm:text-5xl"
              >
                An assistant for data teams, not a replacement
              </h2>
              <p className="mt-5 max-w-xl text-lg text-fg-muted">
                QueryMind takes the routine questions, so analysts can spend their time on the deep
                ones. Every answer comes with the SQL behind it, ready to check.
              </p>
            </Reveal>
            <div className="flex flex-wrap content-end gap-3 lg:col-span-4 lg:justify-end">
              <StartButtons loggedIn={loggedIn} />
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t">
        {/* The mark's signal line, drawn across the whole page as the last thing on it. */}
        <svg
          aria-hidden="true"
          viewBox="0 0 1200 120"
          preserveAspectRatio="none"
          className="block h-24 w-full text-signal sm:h-32"
        >
          <DrawPath
            inView
            d="M 0 90 L 420 90 L 500 20 L 600 110 L 680 50 L 1200 50"
            fill="none"
            stroke="currentColor"
            strokeWidth={2}
            strokeLinejoin="round"
            vectorEffect="non-scaling-stroke"
          />
        </svg>
        <div className="mx-auto flex max-w-7xl flex-wrap items-center gap-x-6 gap-y-2 border-t px-4 py-6 text-sm text-fg-muted">
          <span className="flex items-center gap-2 font-medium text-fg">
            <LogoMark className="size-5" />
            QueryMind
          </span>
          <span aria-hidden="true" className="font-mono text-xs">
            QM-1
          </span>
          <Link to="/evals" className="hover:text-fg">
            Evals lab
          </Link>
          {!loggedIn && (
            <Link to="/login" className="hover:text-fg">
              Log in
            </Link>
          )}
        </div>
      </footer>
    </div>
  )
}

/** "Create account" + "Log in" for visitors; "Open the canvas" for someone already logged in. */
function StartButtons({ loggedIn, compact = false }: { loggedIn: boolean; compact?: boolean }) {
  if (loggedIn) {
    return (
      <Button asChild size={compact ? 'default' : 'lg'}>
        <Link to="/">
          Open the canvas
          <ArrowRight aria-hidden="true" />
        </Link>
      </Button>
    )
  }
  return (
    <>
      {/* In the top bar of a phone only the main action fits; "Log in" is in the hero and footer. */}
      <Button
        asChild
        size={compact ? 'default' : 'lg'}
        variant="outline"
        className={compact ? 'hidden sm:inline-flex' : undefined}
      >
        <Link to="/login">Log in</Link>
      </Button>
      <Button asChild size={compact ? 'default' : 'lg'}>
        <Link to="/signup">
          Create account
          {!compact && <ArrowRight aria-hidden="true" />}
        </Link>
      </Button>
    </>
  )
}

/**
 * One section of the page, on a Swiss grid: its number and label in the left third, the heading
 * and intro in the right two thirds, then the content across the full width.
 */
function Band({
  id,
  index,
  label,
  title,
  intro,
  children,
}: {
  id: string
  index: string
  label: string
  title: string
  intro?: string
  children: ReactNode
}) {
  const headingId = `${id}-heading`
  return (
    <section id={id} aria-labelledby={headingId} className="scroll-mt-16 border-t">
      <div className="mx-auto max-w-7xl px-4 py-20 sm:py-28">
        <div className="grid gap-x-12 gap-y-5 lg:grid-cols-12">
          <Reveal inView className="lg:col-span-4">
            <p aria-hidden="true" className="label-mono flex items-center gap-3 lg:pt-3">
              <span className="text-signal">{index}</span>
              <span className="h-px w-10 bg-border" />
              {label}
            </p>
          </Reveal>
          <Reveal inView delay={0.06} className="lg:col-span-8">
            <h2
              id={headingId}
              className="text-4xl leading-[1.02] font-semibold tracking-[-0.025em] sm:text-5xl"
            >
              {title}
            </h2>
            {intro && <p className="mt-5 max-w-2xl text-lg text-fg-muted">{intro}</p>}
          </Reveal>
        </div>
        <div className="mt-14">{children}</div>
      </div>
    </section>
  )
}
