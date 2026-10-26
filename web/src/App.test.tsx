import { screen } from '@testing-library/react'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import App from './App'
import { health } from './mocks/fixtures'
import { apiError, HEALTH_URL } from './mocks/handlers'
import { server } from './mocks/server'
import { renderWithQueryClient } from './test/render'

// The fake API (src/test/setup.ts) answers /api/health from the fixtures; a test swaps the answer
// with server.use(...), and the normal one is back for the next test.
describe('App', () => {
  it('shows the API version from /api/health', async () => {
    renderWithQueryClient(<App />)

    expect(await screen.findByText(health.version)).toBeInTheDocument()
    expect(screen.getByRole('status')).toHaveTextContent(`API ok · version ${health.version}`)
  })

  it('explains an API error with its title and message', async () => {
    server.use(http.get(HEALTH_URL, () => apiError('db_unavailable')))

    renderWithQueryClient(<App />)

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("The database isn't reachable right now.")
    expect(alert).toHaveTextContent('Could not connect to the database. Please try again later.')
  })

  it('says the API is unreachable when nothing answers', async () => {
    // A network failure, like fetch gets when uvicorn isn't running at all.
    server.use(http.get(HEALTH_URL, () => HttpResponse.error()))

    renderWithQueryClient(<App />)

    expect(await screen.findByRole('alert')).toHaveTextContent("The QueryMind API isn't reachable.")
  })
})
