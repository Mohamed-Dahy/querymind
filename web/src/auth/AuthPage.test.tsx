import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { getSession, resetAuth, setSession } from '@/api/session'
import { AUTH_URL, TAKEN_EMAIL, WRONG_PASSWORD } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { MOCK_LOGGED_OUT_KEY, mockSession } from '@/mocks/session'
import { renderApp } from '@/test/render'

/** Nobody logged in, and the refresh cookie is gone too (the fake API answers 401). */
function loggedOut() {
  resetAuth()
  localStorage.setItem(MOCK_LOGGED_OUT_KEY, '1')
}

async function fillIn(email: string, password: string) {
  const user = userEvent.setup()
  await user.type(await screen.findByLabelText('Email'), email)
  await user.type(screen.getByLabelText('Password'), password)
  return user
}

describe('logged out', () => {
  it('the canvas sends you to log in, and back afterwards', async () => {
    loggedOut()
    const { router } = renderApp('/c/3f2a9c1e-0000-4000-8000-000000000000')

    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeVisible()
    expect(router.state.location.pathname).toBe('/login')
    expect(router.state.location.search).toBe(
      `?next=${encodeURIComponent('/c/3f2a9c1e-0000-4000-8000-000000000000')}`,
    )

    const user = await fillIn('ana@example.com', 'a long enough password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/c/3f2a9c1e-0000-4000-8000-000000000000'),
    )
    expect(getSession()?.user.email).toBe('ana@example.com')
  })

  it('the explorer needs a login too', async () => {
    loggedOut()
    const { router } = renderApp('/explore')

    await screen.findByRole('heading', { level: 1, name: 'Log in' })
    expect(router.state.location.search).toBe('?next=%2Fexplore')
  })

  it('the evals lab stays open, with "Log in" instead of history', async () => {
    loggedOut()
    renderApp('/evals')

    expect(await screen.findByRole('heading', { level: 1, name: 'Evals lab' })).toBeVisible()
    expect(screen.getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login')
    expect(screen.queryByRole('heading', { name: 'History' })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /New conversation/ })).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Search/ })).not.toBeInTheDocument()
  })

  it('a session that ended on its own says so', async () => {
    setSession(mockSession)
    server.use(
      http.post(AUTH_URL.refresh, () =>
        HttpResponse.json(
          { error: 'not_authenticated', message: 'Please log in.' },
          { status: 401 },
        ),
      ),
      http.get('/api/conversations', () =>
        HttpResponse.json(
          { error: 'not_authenticated', message: 'Please log in.' },
          { status: 401 },
        ),
      ),
    )
    renderApp('/')

    // The history's 401 → the refresh is refused → logged out → the login page explains why.
    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeVisible()
    expect(screen.getByRole('status')).toHaveTextContent('Your session ended. Please log in again.')
  })
})

describe('the login form', () => {
  it('says when the email or password is wrong, without saying which', async () => {
    loggedOut()
    renderApp('/login')

    const user = await fillIn('ana@example.com', WRONG_PASSWORD)
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Wrong email or password.')
    expect(screen.getByLabelText('Password')).toHaveAttribute('aria-invalid', 'true')
    expect(screen.getByLabelText('Password')).toHaveAccessibleDescription(
      'Wrong email or password.',
    )
    expect(getSession()).toBeNull()
  })

  it('lets the browser fill saved passwords', async () => {
    loggedOut()
    renderApp('/login')

    expect(await screen.findByLabelText('Email')).toHaveAttribute('autocomplete', 'email')
    expect(screen.getByLabelText('Password')).toHaveAttribute('autocomplete', 'current-password')
  })

  it('never follows a ?next= to another site', async () => {
    loggedOut()
    const { router } = renderApp('/login?next=%2F%2Fevil.example')

    const user = await fillIn('ana@example.com', 'a long enough password')
    await user.click(screen.getByRole('button', { name: 'Log in' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
  })

  it('logged in already: straight on', async () => {
    const { router } = renderApp('/login?next=%2Fevals')

    await waitFor(() => expect(router.state.location.pathname).toBe('/evals'))
  })
})

describe('the sign-up form', () => {
  it('creates the account and opens the canvas', async () => {
    loggedOut()
    const { router } = renderApp('/signup')

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Create your account' }),
    ).toBeVisible()
    const password = screen.getByLabelText('Password')
    expect(password).toHaveAttribute('autocomplete', 'new-password')
    expect(password).toHaveAttribute('minlength', '10')
    expect(password).toHaveAccessibleDescription('At least 10 characters.')

    const user = await fillIn('new@example.com', 'a long enough password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(getSession()?.user.email).toBe('new@example.com')
  })

  it('says when the email already has an account', async () => {
    loggedOut()
    renderApp('/signup')

    const user = await fillIn(TAKEN_EMAIL, 'a long enough password')
    await user.click(screen.getByRole('button', { name: 'Create account' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'An account with this email already exists.',
    )
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true')
  })

  it('links to the login, keeping where to go next', async () => {
    loggedOut()
    renderApp('/signup?next=%2Fexplore')

    const link = await screen.findByRole('link', { name: 'Log in' })
    expect(link).toHaveAttribute('href', '/login?next=%2Fexplore')
  })
})

describe('logged in', () => {
  it('shows who you are and your role, and "Log out" ends it', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/')

    const header = screen.getByRole('banner')
    expect(within(header).getByText('analyst@example.com')).toBeInTheDocument()
    expect(within(header).getByText('Admin')).toBeInTheDocument()

    await user.click(within(header).getByRole('button', { name: 'Log out' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/login'))
    expect(getSession()).toBeNull()
    // No "session ended" note: this was asked for.
    expect(await screen.findByRole('heading', { level: 1, name: 'Log in' })).toBeVisible()
    expect(screen.queryByText(/Your session ended/)).not.toBeInTheDocument()
    // A reload stays logged out: the fake cookie is gone too.
    expect(localStorage.getItem(MOCK_LOGGED_OUT_KEY)).toBe('1')
  })
})
