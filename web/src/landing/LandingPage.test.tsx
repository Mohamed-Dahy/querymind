import { screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { resetAuth } from '@/api/session'
import { repair } from '@/mocks/fixtures'
import { MOCK_LOGGED_OUT_KEY } from '@/mocks/session'
import { renderApp } from '@/test/render'

// The public front page (U8.4). Unit tests run with animations off (src/test/setup.ts), so the
// demo shows the finished recorded run at once, as under reduced motion.

/** Logged out: no session in memory, and the page-load refresh fails (no cookie). */
function loggedOut() {
  resetAuth()
  localStorage.setItem(MOCK_LOGGED_OUT_KEY, '1')
}

describe('landing page', () => {
  it('a visitor who opens the site lands here, not on a bare login form', async () => {
    loggedOut()
    const { router } = renderApp('/')

    expect(
      await screen.findByRole('heading', { level: 1, name: /Ask your database/ }),
    ).toBeVisible()
    expect(router.state.location.pathname).toBe('/welcome')
    await waitFor(() => expect(document.title).toBe('QueryMind · Ask your database in plain words'))
  })

  it('a deeper link still goes to the login and back', async () => {
    loggedOut()
    const { router } = renderApp('/explore')

    await screen.findByRole('heading', { level: 1, name: 'Log in' })
    expect(router.state.location.search).toBe('?next=%2Fexplore')
  })

  it('offers an account to visitors', async () => {
    loggedOut()
    renderApp('/welcome')

    const hero = await screen.findByRole('region', { name: /Ask your database/ })
    expect(within(hero).getByRole('link', { name: /Create account/ })).toHaveAttribute(
      'href',
      '/signup',
    )
    expect(within(hero).getByRole('link', { name: 'Log in' })).toHaveAttribute('href', '/login')
    expect(within(hero).getByRole('link', { name: 'See the evals lab' })).toHaveAttribute(
      'href',
      '/evals',
    )
  })

  it('offers the canvas to someone already logged in', async () => {
    renderApp('/welcome')

    const hero = await screen.findByRole('region', { name: /Ask your database/ })
    expect(within(hero).getByRole('link', { name: /Open the canvas/ })).toHaveAttribute('href', '/')
    expect(within(hero).queryByRole('link', { name: /Create account/ })).not.toBeInTheDocument()
  })

  it('replays a real recorded run: the repair, then the real answer, and says it is a recording', async () => {
    renderApp('/welcome')

    const demo = await screen.findByRole('region', { name: repair.question })
    expect(within(demo).getByText(/Replay of a recorded run/)).toBeInTheDocument()
    // Under reduced motion (and here) the finished run: two tries, then the answer card.
    expect(within(demo).getByText('2 tries')).toBeInTheDocument()
    expect(await within(demo).findByText(repair.answer)).toBeInTheDocument()
    // A replay has no run to stop, and with no motion nothing to pause either.
    expect(within(demo).queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
    expect(within(demo).queryByRole('button', { name: /Pause|Replay/ })).not.toBeInTheDocument()
  })

  it('shows the evals lab numbers, each with the report it comes from', async () => {
    renderApp('/welcome')

    const results = await screen.findByRole('region', { name: 'Measured, not promised' })
    const tiles = await within(results).findAllByRole('listitem')
    // The four headline tiles, then the link to the lab.
    expect(tiles.length).toBeGreaterThanOrEqual(2)
    // Each tile names its source ("From golden….md · 2026-11-08"), never a number on its own.
    expect(tiles[0]).toHaveTextContent(/From \S+.* · \d{4}-\d{2}-\d{2}/)
    expect(within(results).getByRole('link', { name: /evals lab/ })).toHaveAttribute(
      'href',
      '/evals',
    )
  })
})
