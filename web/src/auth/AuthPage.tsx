import { useQueryClient } from '@tanstack/react-query'
import { Loader2, ScanSearch, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useState, type FormEvent } from 'react'
import { Link, Navigate, useNavigate, useSearchParams } from 'react-router'

import { logIn, signUp } from '@/api/auth'
import { ApiError } from '@/api/client'
import { safeNext, takeSessionEnded, useAuthStatus } from '@/api/session'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

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

const FIELD = cn(
  'mt-1.5 h-11 w-full rounded-lg border border-input bg-muted px-3 text-base',
  'outline-none focus-visible:ring-3 focus-visible:ring-ring aria-invalid:border-danger',
)

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
      setError(
        caught instanceof ApiError
          ? caught
          : new ApiError('internal_error', 'Something went wrong. Please try again.'),
      )
      setBusy(false)
    }
  }

  const other =
    mode === 'login'
      ? { to: '/signup', question: 'New here?', action: 'Create an account' }
      : { to: '/login', question: 'Already have an account?', action: 'Log in' }
  const otherHref = next === '/' ? other.to : `${other.to}?next=${encodeURIComponent(next)}`

  return (
    <div className="canvas-grid flex min-h-dvh flex-col">
      <header className="flex items-center justify-between gap-4 border-b bg-surface px-4 py-2">
        <Link
          to="/evals"
          className="flex min-h-11 items-center gap-2 rounded-lg font-heading text-lg font-semibold"
        >
          <ScanSearch aria-hidden="true" className="size-5 text-signal" />
          QueryMind
        </Link>
        <ThemeSwitcher compact />
      </header>

      <main className="flex flex-1 items-start justify-center px-4 py-10 sm:items-center">
        <div className="w-full max-w-sm rounded-cell border bg-surface p-6 shadow-sm">
          <h1 className="font-heading text-2xl font-semibold">{text.title}</h1>
          <p className="mt-1 text-sm text-fg-muted">{text.intro}</p>

          {ended && (
            <p role="status" className="mt-4 rounded-lg border bg-muted px-3 py-2 text-sm">
              Your session ended. Please log in again.
            </p>
          )}

          <form onSubmit={(event) => void onSubmit(event)} className="mt-5 space-y-4">
            <div>
              <label htmlFor={ids.email} className="text-sm font-medium">
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
              <label htmlFor={ids.password} className="text-sm font-medium">
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
                <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
                <span>{error.message}</span>
              </div>
            )}

            <Button type="submit" size="lg" disabled={busy} aria-busy={busy} className="w-full">
              {busy && (
                <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
              )}
              {busy ? text.busy : text.submit}
            </Button>
          </form>

          <p className="mt-5 text-sm">
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
        </div>
      </main>
    </div>
  )
}
