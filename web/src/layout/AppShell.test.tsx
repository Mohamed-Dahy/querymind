import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { health } from '@/mocks/fixtures'
import { apiError, HEALTH_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

// The fake API (src/test/setup.ts) answers /api/health from the fixtures; a test swaps the answer
// with server.use(...), and the normal one is back for the next test.
describe('API status in the top bar', () => {
  it('shows the API version from /api/health', async () => {
    renderApp()

    expect(await screen.findByText(health.version)).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(`API ok · version ${health.version}`)
  })

  it('explains an API error with its title and message', async () => {
    server.use(http.get(HEALTH_URL, () => apiError('db_unavailable')))

    renderApp()

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("The database isn't reachable right now.")
    expect(alert).toHaveTextContent('Could not connect to the database. Please try again later.')
  })

  it('says the API is unreachable when nothing answers', async () => {
    // A network failure, like fetch gets when uvicorn isn't running at all.
    server.use(http.get(HEALTH_URL, () => HttpResponse.error()))

    renderApp()

    expect(await screen.findByRole('alert')).toHaveTextContent("The QueryMind API isn't reachable.")
  })
})

describe('routes', () => {
  it.each([
    ['/', 'Ask about your data', 'Canvas · QueryMind'],
    ['/c/3f2a', 'Ask about your data', 'Canvas · QueryMind'],
    ['/explore', 'Data explorer', 'Data explorer · QueryMind'],
    ['/evals', 'Evals lab', 'Evals lab · QueryMind'],
    ['/no/such/page', 'Page not found', 'Page not found · QueryMind'],
  ])('%s shows "%s" and names the tab', async (path, heading, title) => {
    renderApp(path)

    expect(await screen.findByRole('heading', { level: 1, name: heading })).toBeInTheDocument()
    await waitFor(() => expect(document.title).toBe(title))
  })

  it('the 404 page says which path was wrong and links back', async () => {
    renderApp('/old-link')

    expect(await screen.findByText('/old-link')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Back to the canvas' })).toHaveAttribute('href', '/')
  })

  it.each([
    ['/', 'Canvas'],
    ['/c/3f2a', 'Canvas'],
    ['/explore', 'Explore'],
    ['/evals', 'Evals'],
  ])('at %s the rail marks %s as the current page', async (path, current) => {
    renderApp(path)

    const nav = await screen.findByRole('navigation', { name: 'Main' })
    // Only the current page's link has aria-current="page"; the others have none (null).
    const marks = ['Canvas', 'Explore', 'Evals'].map((name) =>
      within(nav).getByRole('link', { name }).getAttribute('aria-current'),
    )
    expect(marks).toEqual(
      ['Canvas', 'Explore', 'Evals'].map((n) => (n === current ? 'page' : null)),
    )
  })
})

describe('keyboard and focus', () => {
  it('does not move focus on the first load', async () => {
    renderApp('/explore')

    await screen.findByRole('heading', { level: 1, name: 'Data explorer' })
    expect(document.body).toHaveFocus()
  })

  it('moves focus to the main region after navigating, and back works', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await user.click(await screen.findByRole('link', { name: 'Explore' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Data explorer' }),
    ).toBeInTheDocument()
    expect(screen.getByRole('main')).toHaveFocus()

    await router.navigate(-1)
    expect(
      await screen.findByRole('heading', { level: 1, name: 'Ask about your data' }),
    ).toBeInTheDocument()
    expect(router.state.location.pathname).toBe('/')
  })

  it('the skip link is the first Tab stop and jumps to the main region', async () => {
    const user = userEvent.setup()
    renderApp('/')
    await screen.findByRole('heading', { level: 1 })

    await user.tab()
    const skip = screen.getByRole('link', { name: 'Skip to content' })
    expect(skip).toHaveFocus()

    await user.keyboard('{Enter}')
    expect(screen.getByRole('main')).toHaveFocus()
  })
})
