import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { setSession } from '@/api/session'
import { catalog } from '@/mocks/fixtures'
import { mockTableAccess } from '@/mocks/permissions'
import { server } from '@/mocks/server'
import { MOCK_ROLE_KEY, mockSession, mockUser } from '@/mocks/session'
import { saveMockConnection } from '@/mocks/settings'
import { renderApp } from '@/test/render'

afterEach(() => server.events.removeAllListeners())

const TABLES = catalog.tables.length

async function openTab() {
  const rendered = renderApp('/settings/permissions')
  await screen.findByRole('region', { name: 'Tables each role may use' })
  return rendered
}

const box = (name: string) => screen.getByRole('checkbox', { name })

function recordPuts() {
  const sent: unknown[] = []
  server.events.on('request:start', async ({ request }) => {
    if (request.method === 'PUT') sent.push(await request.clone().json())
  })
  return sent
}

describe('Settings → Permissions', () => {
  it('starts with every table for both roles, new ones too', async () => {
    await openTab()

    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).getByRole('link', { name: 'Permissions' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(box('Analysts may use every table')).toBeChecked()
    expect(box('Viewers may use every table')).toBeChecked()
    // While "every table" is on, the single tables follow it.
    expect(box('Viewers may use orders')).toBeChecked()
    expect(box('Viewers may use orders')).toBeDisabled()
    expect(screen.getByText('Viewers may use every table, new ones too.')).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save viewers' })).toBeDisabled()
    await waitFor(() => expect(document.title).toBe('Permissions · Settings · QueryMind'))
  })

  it('a list for viewers: today’s tables minus one, saved for that role only', async () => {
    const sent = recordPuts()
    const user = userEvent.setup()
    await openTab()

    await user.click(box('Viewers may use every table'))
    await user.click(box('Viewers may use orders'))
    expect(
      screen.getByText(
        `Viewers may use ${TABLES - 1} of ${TABLES} tables; tables added later stay hidden. Not saved yet.`,
      ),
    ).toBeVisible()
    expect(screen.getByRole('button', { name: 'Save analysts' })).toBeDisabled()
    await user.click(screen.getByRole('button', { name: 'Save viewers' }))

    expect(await screen.findByText('Viewers: saved.')).toBeVisible()
    expect(sent).toHaveLength(1)
    expect(sent[0]).toMatchObject({ connection_id: null, role: 'viewer' })
    const tables = (sent[0] as { tables: string[] }).tables
    expect(tables).toHaveLength(TABLES - 1)
    expect(tables).not.toContain('orders')
    expect(mockTableAccess(null).viewer).not.toContain('orders')
    expect(screen.getByRole('button', { name: 'Save viewers' })).toBeDisabled()
  })

  it('ticking "every table" again sends null: new tables too', async () => {
    const sent = recordPuts()
    server.use(
      http.get('/api/table-access', () =>
        HttpResponse.json({ ...mockTableAccess(null), analyst: ['orders'] }),
      ),
    )
    const user = userEvent.setup()
    await openTab()

    await user.click(box('Analysts may use every table'))
    await user.click(screen.getByRole('button', { name: 'Save analysts' }))

    await waitFor(() =>
      expect(sent).toEqual([{ connection_id: null, role: 'analyst', tables: null }]),
    )
  })

  it('a role left no table is told plainly: its questions here are refused', async () => {
    server.use(
      http.get('/api/table-access', () =>
        HttpResponse.json({ ...mockTableAccess(null), viewer: [] }),
      ),
    )
    await openTab()

    expect(
      screen.getByText('Viewers may use no table: their questions here are refused.'),
    ).toBeVisible()
    expect(box('Viewers may use orders')).not.toBeChecked()
  })

  it('a refused save says why, under that role', async () => {
    server.use(
      http.put('/api/table-access', () =>
        HttpResponse.json(
          { error: 'table_access_invalid', message: 'This database has no table called ordres.' },
          { status: 422 },
        ),
      ),
    )
    const user = userEvent.setup()
    await openTab()

    await user.click(box('Analysts may use every table'))
    await user.click(screen.getByRole('button', { name: 'Save analysts' }))

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This database has no table called ordres.',
    )
  })

  it('another database shows its own tables', async () => {
    const sales = saveMockConnection({
      name: 'Sales',
      host: 'db.example.com',
      port: 5432,
      dbname: 'sales',
      user: 'querymind_reader',
      password: 'secret password',
      sslmode: 'verify-full',
    })!
    const asked: (string | null)[] = []
    server.events.on('request:start', ({ request }) => {
      const url = new URL(request.url)
      if (url.pathname === '/api/table-access') asked.push(url.searchParams.get('connection_id'))
    })
    const user = userEvent.setup()
    await openTab()

    // The page's own picker (the top bar has the app's database switcher too).
    await user.selectOptions(within(screen.getByRole('main')).getByLabelText('Database'), 'Sales')

    // The saved database in the fake API: the demo's Northwind tables only (no Chinook).
    await waitFor(() => expect(screen.queryByRole('checkbox', { name: /use album$/ })).toBeNull())
    expect(await screen.findByRole('checkbox', { name: 'Viewers may use orders' })).toBeVisible()
    expect(asked).toContain(sales.id)
  })

  it('another role sees no Permissions tab, and its URL says why, without asking the API', async () => {
    localStorage.setItem(MOCK_ROLE_KEY, 'analyst')
    setSession({ ...mockSession, user: { ...mockUser, role: 'analyst' } })
    let asked = false
    server.use(
      http.get('/api/table-access', () => {
        asked = true
        return HttpResponse.json({})
      }),
    )
    renderApp('/settings/permissions')

    expect(
      await screen.findByText(/Only an admin of this workspace can see and change/),
    ).toBeVisible()
    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).queryByRole('link', { name: 'Permissions' })).toBeNull()
    expect(asked).toBe(false)
  })
})
