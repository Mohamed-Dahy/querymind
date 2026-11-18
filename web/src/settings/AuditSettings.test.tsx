import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { setSession } from '@/api/session'
import { MOCK_AUDIT_LOG } from '@/mocks/audit'
import { server } from '@/mocks/server'
import { MOCK_ROLE_KEY, mockSession, mockUser } from '@/mocks/session'
import { renderApp } from '@/test/render'

import { outcomeOf } from './auditWords'

afterEach(() => server.events.removeAllListeners())

const TOTAL = MOCK_AUDIT_LOG.length

async function openTab(path = '/settings/audit') {
  const rendered = renderApp(path)
  await screen.findByRole('list', { name: 'Audit log lines' })
  return { ...rendered, user: userEvent.setup() }
}

const rows = () =>
  within(screen.getByRole('list', { name: 'Audit log lines' })).getAllByRole('button')

/** The query of every audit request, as the API received it. */
function recordQueries() {
  const asked: URLSearchParams[] = []
  server.events.on('request:start', ({ request }) => {
    const url = new URL(request.url)
    if (url.pathname === '/api/admin/audit') asked.push(url.searchParams)
  })
  return asked
}

describe('Settings → Audit log', () => {
  it('shows the newest 50 lines, and "Load more" reads the rest', async () => {
    const { user } = await openTab()

    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).getByRole('link', { name: 'Audit log' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    await waitFor(() => expect(document.title).toBe('Audit log · Settings · QueryMind'))
    expect(rows()).toHaveLength(50)
    expect(screen.getByText('50 lines, more below.')).toBeVisible()

    await user.click(screen.getByRole('button', { name: 'Load more' }))

    expect(await screen.findByText(`${TOTAL} lines.`)).toBeVisible()
    expect(rows()).toHaveLength(TOTAL)
    expect(screen.queryByRole('button', { name: 'Load more' })).toBeNull()
  })

  it('a row reads as one sentence, and opens to its SQL, model and cost', async () => {
    const { user } = await openTab()
    // The newest question that ran SQL on an LLM (the newest line of all is a re-run).
    const index = MOCK_AUDIT_LOG.findIndex((line) => line.outcome === 'answer' && line.llm)
    const newest = MOCK_AUDIT_LOG[index]!

    const row = rows()[index]!
    expect(row).toHaveAccessibleName(
      expect.stringContaining(`${newest.email} Question Answered ${newest.question}`),
    )
    expect(row).toHaveAttribute('aria-expanded', 'false')
    await user.click(row)

    expect(row).toHaveAttribute('aria-expanded', 'true')
    const details = document.getElementById(row.getAttribute('aria-controls')!)!
    expect(details).toHaveTextContent(newest.sql!)
    expect(details).toHaveTextContent(`Tables${newest.tables.join(', ')}`)
    expect(details).toHaveTextContent('DatabaseNorthwind + Chinook')
    expect(details).toHaveTextContent(/Model.+, the (demo|workspace’s) key/)
    expect(details).toHaveTextContent(/Cost(\$|price not known)/)
  })

  it('outcomes in words, an icon each: done, declined, failed', async () => {
    await openTab('/settings/audit?action=ask')

    const list = screen.getByRole('list', { name: 'Audit log lines' })
    expect(within(list).getAllByText('Answered').length).toBeGreaterThan(0)
    expect(within(list).getAllByText('Declined').length).toBeGreaterThan(0)
    expect(within(list).getAllByText("I couldn't answer that safely.").length).toBeGreaterThan(0)
  })

  it('a filter applies at once and goes in the URL', async () => {
    const asked = recordQueries()
    const { user, router } = await openTab()

    await user.selectOptions(screen.getByLabelText('Action'), 'Failed login')

    await waitFor(() => expect(rows()).toHaveLength(1))
    expect(rows()[0]).toHaveAccessibleName(
      expect.stringContaining(`${mockUser.email} Failed login`),
    )
    expect(rows()[0]).toHaveAccessibleName(expect.stringContaining("Couldn't log you in."))
    expect(router.state.location.search).toBe('?action=login_failed')
    expect(asked.at(-1)?.get('action')).toBe('login_failed')
  })

  it('opens with the filters a link carries', async () => {
    await openTab('/settings/audit?outcome=unsafe_sql&email=sam%40example.com')

    expect(screen.getByLabelText('Outcome')).toHaveValue('unsafe_sql')
    expect(screen.getByLabelText('Person')).toHaveValue('sam@example.com')
    const expected = MOCK_AUDIT_LOG.filter(
      (line) => line.outcome === 'unsafe_sql' && line.email === 'sam@example.com',
    )
    expect(rows()).toHaveLength(expected.length)
  })

  it('the search asks once you stop typing, for the words in questions and SQL', async () => {
    const asked = recordQueries()
    const { user, router } = await openTab()

    await user.type(screen.getByLabelText('Find in questions and SQL'), 'PG_SHADOW')

    await waitFor(() => expect(router.state.location.search).toBe('?q=PG_SHADOW'))
    const expected = MOCK_AUDIT_LOG.filter((line) => line.sql?.includes('pg_shadow'))
    await waitFor(() => expect(rows()).toHaveLength(expected.length))
    // One request for the whole word, not one per letter.
    expect(asked.filter((query) => query.has('q'))).toHaveLength(1)
  })

  it('no line matches: says so, and "Clear the filters" starts over', async () => {
    const { router } = renderApp('/settings/audit?q=nothing-like-this')
    const user = userEvent.setup()

    expect(await screen.findByText('No line matches these filters.')).toBeVisible()
    expect(screen.getByLabelText('Find in questions and SQL')).toHaveValue('nothing-like-this')
    await user.click(screen.getByRole('button', { name: 'Clear the filters' }))

    await waitFor(() => expect(rows()).toHaveLength(50))
    expect(router.state.location.search).toBe('')
    expect(screen.getByLabelText('Find in questions and SQL')).toHaveValue('')
  })

  it('says so when the log can’t be read, with a way to try again', async () => {
    server.use(
      http.get('/api/admin/audit', () =>
        HttpResponse.json(
          { error: 'accounts_unavailable', message: "The audit log isn't available." },
          { status: 503 },
        ),
      ),
    )
    renderApp('/settings/audit')

    expect(await screen.findByText("The audit log can't be read right now.")).toBeVisible()
    expect(screen.getByRole('button', { name: 'Try again' })).toBeVisible()
  })

  it('another role sees no Audit tab, and its URL says why, without asking the API', async () => {
    localStorage.setItem(MOCK_ROLE_KEY, 'analyst')
    setSession({ ...mockSession, user: { ...mockUser, role: 'analyst' } })
    const asked = recordQueries()
    renderApp('/settings/audit')

    expect(
      await screen.findByText('Only an admin of this workspace can read its audit log.'),
    ).toBeVisible()
    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).queryByRole('link', { name: 'Audit log' })).toBeNull()
    expect(asked).toHaveLength(0)
  })
})

describe('outcomeOf', () => {
  it('knows the plain outcomes, an error code by its title, and shows an unknown code as is', () => {
    expect(outcomeOf('ok')).toEqual({ kind: 'done', label: 'Done' })
    expect(outcomeOf('cancelled')).toEqual({ kind: 'declined', label: 'Stopped' })
    expect(outcomeOf('invalid_login')).toEqual({ kind: 'failed', label: "Couldn't log you in." })
    expect(outcomeOf('brand_new_code')).toEqual({ kind: 'failed', label: 'brand_new_code' })
  })
})
