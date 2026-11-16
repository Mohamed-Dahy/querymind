import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { chooseDatabase } from '@/api/database'
import { getSession, setSession } from '@/api/session'
import { AUTH_URL, CONNECTIONS_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { recordTurn } from '@/mocks/conversations'
import { answer } from '@/mocks/fixtures'
import { mockSession, mockUser, OTHER_WORKSPACE } from '@/mocks/session'
import { saveMockConnection } from '@/mocks/settings'
import { renderApp } from '@/test/render'

// The workspace switcher and what a workspace change resets (U7.1d). The fake login belongs to
// its own workspace (admin) and to Acme Sales (viewer).

afterEach(() => server.events.removeAllListeners())

/** How many times the app asked for each path, from now on. */
function countRequests() {
  const counts = new Map<string, number>()
  server.events.on('request:start', ({ request }) => {
    const path = new URL(request.url).pathname
    counts.set(path, (counts.get(path) ?? 0) + 1)
  })
  return (path: string) => counts.get(path) ?? 0
}

async function openMenu(user: ReturnType<typeof userEvent.setup>) {
  const header = screen.getByRole('banner')
  await user.click(within(header).getByRole('button', { name: 'Account' }))
  return header
}

function savedSales() {
  return saveMockConnection({
    name: 'Sales',
    host: 'db.example.com',
    port: 5432,
    dbname: 'sales',
    user: 'querymind_reader',
    password: 'secret password',
    sslmode: 'verify-full',
  })!
}

describe('the workspace switcher', () => {
  it('lists your workspaces in the account menu, the current one marked', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const header = await openMenu(user)

    const list = await within(header).findByRole('list', { name: 'Workspaces' })
    const items = within(list).getAllByRole('listitem')
    expect(items[0]).toHaveTextContent(`${mockUser.workspace.name} Admin (current)`)
    expect(within(items[1]!).getByRole('button')).toHaveAccessibleName(
      'Switch to Acme Sales Viewer',
    )
  })

  it('with one workspace, there is nothing to switch to', async () => {
    server.use(
      http.get('/api/auth/workspaces', () =>
        HttpResponse.json({
          workspaces: [{ ...mockUser.workspace, role: 'admin', current: true }],
        }),
      ),
    )
    const user = userEvent.setup()
    renderApp('/')
    const header = await openMenu(user)

    await within(header).findByRole('link', { name: 'Settings' })
    await waitFor(() =>
      expect(within(header).queryByRole('list', { name: 'Workspaces' })).not.toBeInTheDocument(),
    )
  })

  it('switching starts over in the other workspace: empty canvas, data asked again, its own database', async () => {
    const sales = savedSales()
    chooseDatabase(sales.id) // your own workspace's choice
    const user = userEvent.setup()
    const { router } = renderApp('/settings/databases')
    await screen.findByRole('listitem', { name: 'Sales' })
    const asked = countRequests()

    const header = await openMenu(user)
    await user.click(await within(header).findByRole('button', { name: /Switch to Acme Sales/ }))

    expect(await screen.findByText('Switched to Acme Sales.')).toBeVisible()
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(getSession()?.user.workspace).toEqual(OTHER_WORKSPACE)
    // The shell was drawn again for the new workspace: find the top bar anew.
    const bar = screen.getByRole('banner')
    expect(within(bar).getByText('Viewer')).toBeVisible() // the role badge
    // The old workspace's cached answers are gone: the history and databases are asked again.
    await waitFor(() => expect(asked('/api/conversations')).toBeGreaterThan(0))
    await waitFor(() => expect(asked(CONNECTIONS_URL)).toBeGreaterThan(0))
    // Acme Sales has its own database choice (none yet): the demo, not your Sales.
    expect(within(bar).getByRole('combobox', { name: 'Database' })).toHaveValue('demo')
  })

  it('a conversation on the canvas doesn’t follow you into the other workspace', async () => {
    const user = userEvent.setup()
    renderApp('/')

    await user.type(
      await screen.findByRole('textbox', { name: 'Your question' }),
      'How many orders?',
    )
    await user.keyboard('{Control>}{Enter}{/Control}')
    const cells = await screen.findByRole('list', { name: 'Questions and answers' })
    await within(cells).findByText('How many orders?')

    const header = await openMenu(user)
    await user.click(await within(header).findByRole('button', { name: /Switch to Acme Sales/ }))

    await screen.findByText('Switched to Acme Sales.')
    await waitFor(() =>
      expect(screen.queryByRole('list', { name: 'Questions and answers' })).not.toBeInTheDocument(),
    )
  })

  it('removed from the workspace: the refresh lands in another, and the app says so and resets', async () => {
    let refused = false
    server.use(
      // The old token is refused once (removed), then the refresh lands in Acme Sales.
      http.get('/api/conversations', () => {
        if (refused) return undefined
        refused = true
        return HttpResponse.json(
          { error: 'not_authenticated', message: 'Please log in.' },
          { status: 401 },
        )
      }),
      http.post(AUTH_URL.refresh, () =>
        HttpResponse.json({
          access_token: 'renewed',
          token_type: 'bearer',
          expires_in: 900,
          user: { ...mockUser, role: 'viewer', workspace: OTHER_WORKSPACE },
        }),
      ),
    )
    const { router } = renderApp('/settings/key')

    expect(await screen.findByText(/You're now in Acme Sales/)).toBeVisible()
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(getSession()?.user.workspace).toEqual(OTHER_WORKSPACE)
  })

  it('a delete still in its Undo time is sent at once, to its own workspace, with its token', async () => {
    const id = crypto.randomUUID()
    recordTurn({ ...answer, question: 'Sales per category?', conversation_id: id }, [])
    setSession({ ...mockSession, accessToken: 'own-workspace-token' })
    const deletes: (string | null)[] = []
    server.events.on('request:start', ({ request }) => {
      if (request.method === 'DELETE') deletes.push(request.headers.get('Authorization'))
    })
    const user = userEvent.setup()
    renderApp('/')

    const history = screen.getByRole('region', { name: 'History' })
    await user.click(
      await within(history).findByRole('button', { name: 'Delete “Sales per category?”' }),
    )
    const header = await openMenu(user)
    await user.click(await within(header).findByRole('button', { name: /Switch to Acme Sales/ }))

    // Not 5 s later with Acme's token (a 404 there, and the conversation would stay).
    await waitFor(() => expect(deletes).toEqual(['Bearer own-workspace-token']))
  })

  it('coming back to the tab checks your role: a changed one shows at once', async () => {
    server.use(http.get(AUTH_URL.me, () => HttpResponse.json({ ...mockUser, role: 'analyst' })))
    renderApp('/')
    const header = screen.getByRole('banner')
    expect(within(header).getByText('Admin')).toBeVisible()

    act(() => {
      window.dispatchEvent(new Event('focus'))
    })

    expect(await within(header).findByText('Analyst')).toBeVisible()
  })
})
