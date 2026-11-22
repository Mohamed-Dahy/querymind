import { useQueryClient } from '@tanstack/react-query'
import { Check, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'

import { logIn, signUp } from '@/api/auth'
import { asApiError, type ApiError } from '@/api/client'
import { safeNext, takeSessionEnded, useAuthStatus } from '@/api/session'
import { formatWait } from '@/canvas/wait'
import { FIELD } from '@/components/field'
import { Plate } from '@/components/Instrument'
import { LogoMark } from '@/components/LogoMark'
import { SubmitButton } from '@/components/SubmitButton'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Reveal } from '@/motion'

import { AccessTerminal } from './AccessTerminal'

type Mode = 'login' | 'signup'

const TEXT: Record<Mode, { title: string; submit: string; busy: string; intro: string }> = {
  login: {
    title: 'Log in',
    submit: 'Log in',
    busy: 'Logging in…',
    intro: 'Ask questions about your data in plain words.',
  },
  signup: {
    title: 'Create your account',
    submit: 'Create account',
    busy: 'Creating your account…',
    intro: 'Free. You get your own workspace; your conversations are yours only.',
  },
}

// The API's limits (auth/passwords.py), checked here first so the user hears it before sending.
const MIN_PASSWORD = 10
const MAX_PASSWORD = 128

/**
 * The login and sign-up page (U7.1). Outside the app shell, so logging in starts a fresh canvas.
 * After it: `?next=` (only a path inside this app), else the canvas. Already logged in: straight there.
 */
export function AuthPage({ mode }: { mode: Mode }) {
  const text = TEXT[mode]
  const status = useAuthStatus()
  const [params] = useSearchParams()
  const next = safeNext(params.get('next'))
  const navigate = useNavigate()
  const queryClient = useQueryClient()
  // Read once: shown on this visit, not again after the next login.
  const [ended] = useState(takeSessionEnded)
  const [error, setError] = useState<ApiError | null>(null)
  const [busy, setBusy] = useState(false)
  // After a `rate_limited` (U7.5): when one more login or sign-up fits.
  const [retryAt, setRetryAt] = useState<number | undefined>()
  const ids = { email: useId(), password: useId(), hint: useId(), error: useId() }

  useEffect(() => {
    document.title = `${text.title} · QueryMind`
  }, [text.title])

  // Already logged in (a reload, or the back button): this page has nothing to do.
  if (status === 'in' && !busy) return <Navigate to={next} replace />

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const form = new FormData(event.currentTarget)
    const body = { email: String(form.get('email')), password: String(form.get('password')) }
    setBusy(true)
    setError(null)
    try {
      await (mode === 'login' ? logIn(body) : signUp(body))
      // Cached answers belong to whoever was here before: a new user starts empty.
      queryClient.clear()
      void navigate(next, { replace: true })
    } catch (caught) {
      const failure = asApiError(caught)
      setError(failure)
      if (failure.retryAfterSeconds !== undefined) {
        setRetryAt(Date.now() + failure.retryAfterSeconds * 1000)
      }
      setBusy(false)
    }
  }

  const other =
    mode === 'login'
      ? { to: '/signup', question: 'New here?', action: 'Create an account' }
      : { to: '/login', question: 'Already have an account?', action: 'Log in' }
  const otherHref = next === '/' ? other.to : `${other.to}?next=${encodeURIComponent(next)}`

  return (
    <div className="flex min-h-dvh flex-col bg-bg">
      <header className="flex items-center justify-between gap-4 border-b bg-bg px-4 py-2">
        {/* The front page (U8.4): what QueryMind is, for someone who isn't sure yet. */}
        <Link
          to="/welcome"
          className="flex min-h-11 items-center gap-2.5 rounded-lg font-heading text-[1.0625rem] font-semibold tracking-tight"
        >
          <LogoMark />
          QueryMind
        </Link>
        <ThemeSwitcher compact />
      </header>

      {/* An access terminal (U9.2, ADR 027): from 1024 px, a console on the left prints a real
          recorded session; the form stands on the page on the right, not in a card. */}
      <main className="grid flex-1 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,1fr)]">
        <AuthAside />
        <div className="flex items-start justify-center px-4 py-12 sm:items-center sm:py-16">
          <Reveal className="w-full max-w-sm">
            <p aria-hidden="true" className="label-mono flex items-center gap-3">
              <span className="text-signal">01</span>
              <span className="h-px w-8 bg-border" />
              {mode === 'login' ? 'WELCOME BACK' : 'NEW WORKSPACE'}
            </p>
            <h1 className="mt-4 font-heading text-4xl leading-[1.05] font-semibold tracking-tight">
              {text.title}
            </h1>
            <p className="mt-2 text-fg-muted">{text.intro}</p>

            {ended && (
              <p
                role="status"
                className="mt-6 rounded-[6px] border bg-surface px-3 py-2 text-sm shadow-[inset_2px_0_0_var(--warn)]"
              >
                Your session ended. Please log in again.
              </p>
            )}

            <form onSubmit={(event) => void onSubmit(event)} className="mt-8 space-y-5">
              <div>
                <label
                  htmlFor={ids.email}
                  className="font-mono text-xs font-medium tracking-[0.04em] text-fg-muted"
                >
                  Email
                </label>
                <input
                  id={ids.email}
                  name="email"
                  type="email"
                  autoComplete="email"
                  required
                  maxLength={254}
                  aria-invalid={error?.code === 'email_taken' || undefined}
                  aria-describedby={error ? ids.error : undefined}
                  className={FIELD}
                />
              </div>
              <div>
                <label
                  htmlFor={ids.password}
                  className="font-mono text-xs font-medium tracking-[0.04em] text-fg-muted"
                >
                  Password
                </label>
                <input
                  id={ids.password}
                  name="password"
                  type="password"
                  // The browser's password manager: fill a saved one, or offer to make a new one.
                  autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
                  required
                  minLength={mode === 'signup' ? MIN_PASSWORD : undefined}
                  maxLength={MAX_PASSWORD}
                  aria-invalid={error?.code === 'invalid_login' || undefined}
                  aria-describedby={
                    [mode === 'signup' && ids.hint, error && ids.error].filter(Boolean).join(' ') ||
                    undefined
                  }
                  className={FIELD}
                />
                {mode === 'signup' && (
                  <p id={ids.hint} className="mt-1.5 text-xs text-fg-muted">
                    At least {MIN_PASSWORD} characters.
                  </p>
                )}
              </div>

              {error && (
                <div
                  id={ids.error}
                  role="alert"
                  className="flex items-start gap-1.5 rounded-lg border border-danger px-3 py-2 text-sm"
                >
                  <TriangleAlert
                    aria-hidden="true"
                    className="mt-0.5 size-4 shrink-0 text-danger"
                  />
                  <span>
                    {error.message}
                    {/* The countdown by the button is hidden from screen readers: the wait is here. */}
                    {error.retryAfterSeconds !== undefined && (
                      <span className="sr-only">
                        {' '}
                        You can try again in {formatWait(error.retryAfterSeconds)}.
                      </span>
                    )}
                  </span>
                </div>
              )}

              <SubmitButton
                label={text.submit}
                busyLabel={text.busy}
                busy={busy}
                retryAt={retryAt}
                wide
              />
            </form>

            <p className="mt-8 border-t pt-5 text-sm">
              {other.question}{' '}
              <Link to={otherHref} className="text-info underline underline-offset-4">
                {other.action}
              </Link>
            </p>
            <p className="mt-2 text-sm">
              <Link to="/evals" className="text-info underline underline-offset-4">
                See the evals lab without an account
              </Link>
            </p>
            <p aria-hidden="true" className="mt-10 font-mono text-[0.6875rem] text-fg-muted">
              READ-ONLY BY DESIGN · EVERY QUESTION AUDITED
            </p>
          </Reveal>
        </div>
      </main>
    </div>
  )
}

/**
 * Beside the form from 1024 px (U9.2): the model plate, the promise, a terminal printing a real
 * recorded session, and three true facts. On the canvas's hairline grid.
 */
function AuthAside() {
  return (
    <aside
      aria-label="What QueryMind does"
      className="canvas-grid hidden flex-col justify-center gap-10 border-r px-10 py-12 lg:flex xl:px-16"
    >
      <div>
        <Plate code="QM-1">ACCESS · DATA ANALYST AGENT</Plate>
        <p className="mt-6 text-4xl leading-[1.05] font-semibold tracking-tight">
          Questions in plain words.
          <br />
          <span className="text-signal">Answers you can check.</span>
        </p>
      </div>
      <div className="px-2">
        <AccessTerminal />
      </div>
      <ul className="grid gap-px overflow-hidden rounded-[6px] border bg-border text-sm xl:grid-cols-3">
        {[
          'Every answer shows its steps, its SQL and how sure it is.',
          'Your database is only ever read, never changed.',
          'Your conversations stay in your own workspace.',
        ].map((fact) => (
          <li key={fact} className="flex items-start gap-2.5 bg-bg p-3">
            <Check aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-signal" />
            {fact}
          </li>
        ))}
      </ul>
    </aside>
  )
}
