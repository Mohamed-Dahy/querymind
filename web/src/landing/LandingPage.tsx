import {
  ArrowRight,
  Database,
  EyeOff,
  FileSearch,
  KeyRound,
  ListChecks,
  Lock,
  ScrollText,
  ShieldCheck,
  Timer,
  Users,
  type LucideIcon,
} from 'lucide-react'
import { useEffect } from 'react'
import { Link } from 'react-router'

import { useAuthStatus } from '@/api/session'
import { LogoMark } from '@/components/LogoMark'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Button } from '@/components/ui/button'
import { Reveal, Stagger, StaggerItem } from '@/motion'

import { LiveDemo } from './LiveDemo'
import { Proof } from './Proof'

/** The seven steps, as the rail shows them, in a sentence each (what the agent really does). */
const STEPS = [
  ['Retrieve', 'Finds the tables that matter for the question, from your data dictionary.'],
  ['Write SQL', 'Drafts one query, guided by similar questions it has solved before.'],
  ['Validate', 'Parses it first: a single read-only SELECT, on tables you may see.'],
  ['Run', 'Runs it as a read-only database role, with a time limit and a row limit.'],
  ['Check', 'Looks at the result for signs it went wrong, like no rows at all.'],
  ['Repair', 'If the database or the check says no, it fixes the SQL and tries again.'],
  ['Summarize', 'Answers in plain words, with a chart, the rows, the SQL and how sure it is.'],
] as const

/** What protects the data: each one is a rule the backend enforces (docs/CLAUDE.md §8, Phase 5). */
const SAFETY: { Icon: LucideIcon; title: string; text: string }[] = [
  {
    Icon: Lock,
    title: 'Read-only by design',
    text: 'Questions run as a database role that can only read. A saved database is checked to be read-only before it is kept.',
  },
  {
    Icon: ShieldCheck,
    title: 'Every query checked first',
    text: 'One SELECT only: no writes, no schema changes, no system catalogs, no functions outside an allowlist.',
  },
  {
    Icon: Timer,
    title: 'Limits on every run',
    text: 'A statement timeout and a row limit on every query, and rate limits on every account.',
  },
  {
    Icon: EyeOff,
    title: 'Personal data hidden',
    text: 'Columns marked as personal come back as “hidden” unless your role may see them.',
  },
  {
    Icon: Users,
    title: 'Tables and rows per role',
    text: 'Admins choose which tables each role sees, and which rows each member may read.',
  },
  {
    Icon: ScrollText,
    title: 'Everything audited',
    text: 'Every question, its SQL, who asked, the outcome and the cost go to an audit log.',
  },
]

/**
 * The public front page (U8.4, ADR 026): what QueryMind is, a live replay of a real run, how it
 * works, what keeps data safe, measured results, and how to start. Outside the app shell, like the
 * login page. Logged-out visitors opening `/` land here (RequireLogin); logged-in ones see a way back
 * into the canvas instead of "Create account".
 */
export default function LandingPage() {
  const loggedIn = useAuthStatus() === 'in'

  useEffect(() => {
    document.title = 'QueryMind · Ask your database in plain words'
  }, [])

  return (
    <div className="min-h-dvh bg-bg">
      <header className="sticky top-0 z-20 border-b bg-bg">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-2.5">
          <Link
            to="/welcome"
            className="flex min-h-11 items-center gap-2.5 rounded-lg font-heading text-[1.0625rem] font-semibold tracking-tight"
          >
            <LogoMark />
            QueryMind
          </Link>
          <nav aria-label="On this page" className="hidden md:block">
            <ul className="flex gap-1 text-sm text-fg-muted">
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
        {/* Hero, on the instrument grid. No glow or blur behind text: the contrast test must be
            able to measure every word (e2e/a11y.spec.ts); the demo card's glow is a box-shadow. */}
        <section aria-labelledby="hero-heading" className="canvas-grid relative">
          <div className="relative mx-auto max-w-6xl px-4 pt-16 pb-12 text-center sm:pt-24">
            <Reveal as="p" className="label-mono">
              AI DATA ANALYST FOR POSTGRESQL · READ-ONLY BY DESIGN
            </Reveal>
            <Reveal delay={0.06}>
              <h1
                id="hero-heading"
                className="mx-auto mt-5 max-w-4xl text-4xl leading-[1.05] font-semibold tracking-tight sm:text-6xl"
              >
                Ask your database. <span className="text-signal">See every step it takes.</span>
              </h1>
            </Reveal>
            <Reveal
              as="p"
              delay={0.12}
              className="mx-auto mt-6 max-w-2xl text-lg leading-relaxed text-fg-muted"
            >
              QueryMind turns a plain-language question into checked, read-only SQL, runs it, and
              answers with a chart, the rows, the SQL and how sure it is. It shows its work, repairs
              its own mistakes, and asks back when a question is unclear.
            </Reveal>
            <Reveal delay={0.18} className="mt-8 flex flex-wrap justify-center gap-3">
              <StartButtons loggedIn={loggedIn} />
              <Button asChild size="lg" variant="ghost">
                <Link to="/evals">See the evals lab</Link>
              </Button>
            </Reveal>
            <Reveal
              as="p"
              delay={0.24}
              className="mx-auto mt-8 flex max-w-3xl flex-wrap justify-center gap-x-5 gap-y-2 font-mono text-xs text-fg-muted"
            >
              {[
                'Read-only role',
                'SQL validated before it runs',
                'Personal data hidden',
                'Every question audited',
              ].map((fact) => (
                <span key={fact} className="inline-flex items-center gap-1.5">
                  <span aria-hidden="true" className="size-1 rounded-full bg-signal" />
                  {fact}
                </span>
              ))}
            </Reveal>
          </div>

          <div className="relative mx-auto max-w-5xl px-4 pb-20">
            <Reveal delay={0.3}>
              <LiveDemo />
            </Reveal>
          </div>
        </section>

        <Band id="how" label="01 · HOW IT WORKS" title="Seven steps, all on screen">
          <p className="max-w-2xl text-fg-muted">
            Most AI tools hand you an answer and ask you to trust it. QueryMind shows the path: the
            tables it chose, the SQL it wrote, every try that failed and why, and how sure it is.
          </p>
          <Stagger as="ol" className="mt-10 grid gap-3 sm:grid-cols-2 lg:grid-cols-7">
            {STEPS.map(([name, text], index) => (
              <StaggerItem
                as="li"
                key={name}
                className="relative rounded-cell border bg-surface p-4 lg:p-3.5"
              >
                <p aria-hidden="true" className="label-mono">
                  {String(index + 1).padStart(2, '0')}
                </p>
                <h3 className="mt-2 font-semibold">{name}</h3>
                <p className="mt-1.5 text-sm text-fg-muted">{text}</p>
              </StaggerItem>
            ))}
          </Stagger>
        </Band>

        <Band id="safety" label="02 · SAFETY" title="Built so it can't change your data">
          <Stagger as="ul" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {SAFETY.map(({ Icon, title, text }) => (
              <StaggerItem as="li" key={title} className="lift rounded-cell border bg-surface p-5">
                <span className="grid size-9 place-items-center rounded-lg border border-signal/40 bg-muted">
                  <Icon aria-hidden="true" className="size-4 text-signal" />
                </span>
                <h3 className="mt-4 font-semibold">{title}</h3>
                <p className="mt-1.5 text-sm text-fg-muted">{text}</p>
              </StaggerItem>
            ))}
          </Stagger>
        </Band>

        <Band id="proof" label="03 · RESULTS" title="Measured, not promised">
          <p className="mb-8 max-w-2xl text-fg-muted">
            Every number below comes from a saved evaluation run, and says which report it is from.
          </p>
          <Proof />
        </Band>

        <Band id="own" label="04 · YOUR DATA" title="Your key, your database">
          <ul className="grid gap-4 md:grid-cols-3">
            {[
              {
                Icon: Database,
                title: 'Start on the demo',
                text: 'Northwind (a shop) and Chinook (a music store) are ready to ask, free.',
              },
              {
                Icon: KeyRound,
                title: 'Bring your own key',
                text: 'Use your own LLM key from a fixed list of providers, Groq and Gemini among them. It is encrypted, and shown back only as its last four characters.',
              },
              {
                Icon: FileSearch,
                title: 'Connect your PostgreSQL',
                text: 'QueryMind reads your tables, drafts a data dictionary for you to review, then answers about your own data.',
              },
            ].map(({ Icon, title, text }) => (
              <li key={title} className="flex gap-3 rounded-cell border bg-surface p-5">
                <Icon aria-hidden="true" className="mt-0.5 size-5 shrink-0 text-info" />
                <div>
                  <h3 className="font-semibold">{title}</h3>
                  <p className="mt-1.5 text-sm text-fg-muted">{text}</p>
                </div>
              </li>
            ))}
          </ul>
        </Band>

        <section aria-labelledby="start-heading" className="border-t">
          <Reveal inView className="mx-auto max-w-3xl px-4 py-24 text-center">
            <ListChecks aria-hidden="true" className="mx-auto size-8 text-signal" />
            <h2
              id="start-heading"
              className="mt-4 text-3xl font-semibold tracking-tight sm:text-4xl"
            >
              An assistant for data teams, not a replacement
            </h2>
            <p className="mx-auto mt-4 max-w-xl text-fg-muted">
              QueryMind takes the routine questions, so analysts can spend their time on the deep
              ones. Every answer comes with the SQL behind it, ready to check.
            </p>
            <div className="mt-8 flex flex-wrap justify-center gap-3">
              <StartButtons loggedIn={loggedIn} />
            </div>
          </Reveal>
        </section>
      </main>

      <footer className="border-t">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center gap-x-6 gap-y-2 px-4 py-6 text-sm text-fg-muted">
          <span className="flex items-center gap-2">
            <LogoMark className="size-5" />
            QueryMind
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

/** One section of the page: a mono label, a heading, then its content, revealed when scrolled to. */
function Band({
  id,
  label,
  title,
  children,
}: {
  id: string
  label: string
  title: string
  children: React.ReactNode
}) {
  const headingId = `${id}-heading`
  return (
    <section id={id} aria-labelledby={headingId} className="scroll-mt-20 border-t">
      <div className="mx-auto max-w-6xl px-4 py-20 sm:py-24">
        <Reveal inView>
          <p aria-hidden="true" className="label-mono">
            {label}
          </p>
          <h2 id={headingId} className="mt-3 text-3xl font-semibold tracking-tight sm:text-4xl">
            {title}
          </h2>
        </Reveal>
        <div className="mt-6">{children}</div>
      </div>
    </section>
  )
}
