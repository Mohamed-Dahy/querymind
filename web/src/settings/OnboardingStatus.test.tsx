import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { onboardingKey, type OnboardingInfo } from '@/api/onboarding'
import { setSession } from '@/api/session'
import type { SaveConnectionRequest } from '@/api/settings'
import { CONNECTIONS_URL } from '@/mocks/handlers'
import {
  FAILED_MESSAGE,
  FAILING_DATABASE,
  pollMockOnboarding,
  startMockOnboarding,
} from '@/mocks/onboarding'
import { server } from '@/mocks/server'
import { MOCK_ROLE_KEY, mockSession } from '@/mocks/session'
import { saveMockConnection, saveMockKey } from '@/mocks/settings'
import { renderApp } from '@/test/render'

const SALES: SaveConnectionRequest = {
  name: 'Sales',
  host: 'db.example.com',
  port: 5432,
  dbname: 'sales',
  user: 'querymind_reader',
  password: 'secret password',
  sslmode: 'verify-full',
}

/** One saved database (and the workspace's own key, unless `key: false`). */
function seed({ key = true, name = 'Sales' } = {}) {
  if (key) saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_test_1234' })
  return saveMockConnection({ ...SALES, name })!
}

const PROGRESS_URL = `${CONNECTIONS_URL}/:id/onboarding`

function status(overrides: Partial<OnboardingInfo>): OnboardingInfo {
  return {
    status: 'drafting',
    tables_total: 30,
    tables_drafted: 12,
    tables_skipped: 2,
    error: null,
    generated_by: '',
    has_dictionary: false,
    started_at: '2026-11-16T10:00:00Z',
    updated_at: '2026-11-16T10:01:00Z',
    ...overrides,
  }
}

async function openTab() {
  const rendered = renderApp('/settings/databases')
  await screen.findByRole('list', { name: 'Your databases' })
  return rendered
}

describe('Settings → Databases: onboarding', () => {
  it('without a key: not onboarded yet, and a link to save one first', async () => {
    seed({ key: false })
    await openTab()

    expect(await screen.findByText('Not onboarded yet.')).toBeVisible()
    expect(screen.getByRole('link', { name: 'save one first' })).toHaveAttribute(
      'href',
      '/settings/key',
    )
    expect(screen.queryByRole('button', { name: /^Onboard/ })).not.toBeInTheDocument()
  })

  it('Onboard: waits, reads, drafts, then offers the review, and says so to a screen reader', async () => {
    const user = userEvent.setup()
    const sales = seed()
    const { client } = await openTab()

    await user.click(await screen.findByRole('button', { name: 'Onboard Sales' }))
    expect(await screen.findByText('Waiting to start…')).toBeVisible()
    const announcer = screen.getByText('Sales: onboarding is about to start.')
    expect(announcer).toHaveAttribute('role', 'status')

    // Each poll is one stage further in the fake API; ask now instead of waiting for the timer.
    const poll = () => client.refetchQueries({ queryKey: onboardingKey(sales.id) })
    await poll()
    await waitFor(() => expect(announcer).toHaveTextContent('Sales: reading its tables.'))
    const steps = screen.getByRole('list', { name: 'Onboarding' })
    expect(within(steps).getByText('Reading tables').closest('li')).toHaveAttribute(
      'aria-current',
      'step',
    )

    await poll()
    await poll()
    const review = await screen.findByRole('link', { name: 'Review descriptions of Sales' })
    expect(review).toHaveAttribute('href', `/settings/databases/${sales.id}/dictionary`)
    expect(screen.getByText(/Last run: 13 tables drafted · 1 skipped\./)).toBeVisible()
    expect(announcer).toHaveTextContent('Sales: onboarded.')
    expect(screen.getByRole('button', { name: 'Onboard again Sales' })).toBeEnabled()
  })

  it('while drafting: the stages, a progress bar and the counts', async () => {
    seed()
    server.use(http.get(PROGRESS_URL, () => HttpResponse.json(status({}))))
    await openTab()

    const bar = await screen.findByRole('progressbar', { name: 'Tables drafted' })
    expect(bar).toHaveAttribute('aria-valuenow', '14') // drafted + skipped
    expect(bar).toHaveAttribute('aria-valuemax', '30')
    expect(screen.getByText('12 of 30 tables drafted · 2 skipped')).toBeVisible()
    const steps = within(screen.getByRole('list', { name: 'Onboarding' })).getAllByRole('listitem')
    expect(steps.map((step) => step.textContent)).toEqual([
      'Reading tables (done)',
      'Drafting descriptions',
      'Ready',
    ])
    expect(steps[1]).toHaveAttribute('aria-current', 'step')
  })

  it('a page opened on a finished run announces nothing', async () => {
    server.use(
      http.get(PROGRESS_URL, () =>
        HttpResponse.json(status({ status: 'ready', tables_drafted: 30, has_dictionary: true })),
      ),
    )
    seed()
    await openTab()

    await screen.findByRole('link', { name: 'Review descriptions of Sales' })
    const list = screen.getByRole('list', { name: 'Your databases' })
    expect(within(list).getByRole('status')).toBeEmptyDOMElement()
  })

  it('a failed run says why and offers to try again; no review without a dictionary', async () => {
    const broken = seed({ name: FAILING_DATABASE })
    startMockOnboarding(broken)
    pollMockOnboarding(broken)
    pollMockOnboarding(broken)
    await openTab()

    expect(await screen.findByText('Onboarding failed.')).toBeVisible()
    expect(screen.getByText(FAILED_MESSAGE)).toBeVisible()
    expect(screen.getByRole('button', { name: `Try again ${FAILING_DATABASE}` })).toBeEnabled()
    expect(screen.queryByRole('link', { name: /Review descriptions/ })).not.toBeInTheDocument()
  })

  it('too many checks: says when onboarding can start again', async () => {
    const user = userEvent.setup()
    seed()
    server.use(
      http.post(`${CONNECTIONS_URL}/:id/onboard`, () =>
        HttpResponse.json(
          { error: 'rate_limited', message: 'Too many checks.', retry_after_seconds: 30 },
          { status: 429, headers: { 'Retry-After': '30' } },
        ),
      ),
    )
    await openTab()

    await user.click(await screen.findByRole('button', { name: 'Onboard Sales' }))
    expect(await screen.findByRole('alert')).toHaveTextContent('Too many checks.')
    expect(screen.getByText(/You can onboard in 30 seconds\./)).toBeVisible()
  })

  it('a viewer never asks for progress, and gets a link to read the descriptions', async () => {
    localStorage.setItem(MOCK_ROLE_KEY, 'viewer')
    setSession({ ...mockSession, user: { ...mockSession.user, role: 'viewer' } })
    const sales = seed()
    let asked = false
    server.use(
      http.get(PROGRESS_URL, () => {
        asked = true
        return HttpResponse.json(status({}))
      }),
    )
    await openTab()

    expect(
      await screen.findByRole('link', { name: 'Read its descriptions (Sales)' }),
    ).toHaveAttribute('href', `/settings/databases/${sales.id}/dictionary`)
    expect(screen.queryByText('Not onboarded yet.')).not.toBeInTheDocument()
    expect(asked).toBe(false)
  })
})
