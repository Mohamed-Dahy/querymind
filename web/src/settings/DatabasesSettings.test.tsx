import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { setSession } from '@/api/session'
import type { SaveConnectionRequest } from '@/api/settings'
import { apiError, CONNECTIONS_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { mockSession } from '@/mocks/session'
import {
  CONNECTION_TRIGGERS as T,
  loadMockConnections,
  saveMockConnection,
  saveMockKey,
} from '@/mocks/settings'
import { renderApp } from '@/test/render'

afterEach(() => server.events.removeAllListeners())

const SALES: SaveConnectionRequest = {
  name: 'Sales',
  host: 'db.example.com',
  port: 5432,
  dbname: 'sales',
  user: 'querymind_reader',
  password: 'secret password',
  sslmode: 'verify-full',
}

async function openTab() {
  const rendered = renderApp('/settings/databases')
  await screen.findByRole('heading', { level: 2, name: 'Databases' })
  return rendered
}

const addForm = () => screen.findByRole('form', { name: 'Add a database' })

/** Fill the add form; `changes` replaces what is typed in each field. */
async function fill(
  user: ReturnType<typeof userEvent.setup>,
  changes: Partial<SaveConnectionRequest> = {},
) {
  const values = { ...SALES, ...changes }
  const form = await addForm()
  const type = async (label: string, value: string) => {
    const field = within(form).getByLabelText(label)
    await user.clear(field)
    await user.type(field, value)
  }
  await type('Name', values.name)
  await type('Host', values.host)
  await type('Port', String(values.port))
  await type('Database', values.dbname)
  await type('User', values.user)
  await type('Password', values.password)
  await user.selectOptions(within(form).getByLabelText('Encryption'), values.sslmode)
  return form
}

describe('Settings → Databases', () => {
  it('is a tab with its own URL, and starts with no database', async () => {
    await openTab()

    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).getByRole('link', { name: 'Databases' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(await screen.findByText('No database yet')).toBeVisible()
    await waitFor(() => expect(document.title).toBe('Databases · Settings · QueryMind'))
  })

  it('shows the read-only login recipe next to the form', async () => {
    const user = userEvent.setup()
    await openTab()
    await addForm()

    await user.click(screen.getByText('How to make a read-only login'))

    expect(await screen.findByText(/GRANT SELECT ON ALL TABLES IN SCHEMA public/)).toBeVisible()
    expect(screen.getByLabelText('User')).toHaveAccessibleDescription(/database.s owner/)
  })

  it('adds a database after the check: it is listed, and the form is emptied', async () => {
    const sent: SaveConnectionRequest[] = []
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'POST' && new URL(request.url).pathname === CONNECTIONS_URL) {
        sent.push((await request.clone().json()) as SaveConnectionRequest)
      }
    })
    const user = userEvent.setup()
    await openTab()

    const form = await fill(user, { sslmode: 'require' })
    await user.click(within(form).getByRole('button', { name: 'Check and add' }))

    const list = await screen.findByRole('list', { name: 'Your databases' })
    const row = within(list).getByRole('listitem', { name: 'Sales' })
    expect(row).toHaveTextContent('querymind_reader@db.example.com:5432/sales')
    expect(row).toHaveTextContent('Encrypted, certificate not checked')
    expect(screen.getByText(/Sales added: reachable, and the login can only read/)).toBeVisible()
    expect(within(form).getByLabelText('Password')).toHaveValue('')
    expect(within(form).getByLabelText('Name')).toHaveValue('')
    expect(sent).toEqual([{ ...SALES, sslmode: 'require' }])
  })

  it.each([
    [{ host: T.unsafeHost }, "QueryMind can't connect to that address.", 'Host', false],
    [{ host: T.unreachableHost }, "Couldn't reach the database.", 'Host', false],
    [{ host: T.selfSignedHost }, "The encrypted connection didn't work.", 'Encryption', true],
    [{ password: T.wrongPassword }, 'The database refused the login.', 'Password', true],
    [{ user: T.writerUser }, 'That login can change data.', 'User', true],
  ] as const)(
    'a refusal (%o) says why and how to fix it, next to the form',
    async (changes, title, field, marked) => {
      const user = userEvent.setup()
      await openTab()

      const form = await fill(user, changes)
      await user.click(within(form).getByRole('button', { name: 'Check and add' }))

      const alert = await within(form).findByRole('alert')
      expect(alert).toHaveTextContent(title)
      const input = within(form).getByLabelText(field)
      // Marked when that field is the one to fix; an address problem may be host or port.
      expect(input.getAttribute('aria-invalid')).toBe(marked ? 'true' : null)
      // The field points at the reason, so a screen reader hears it there too.
      expect(input.getAttribute('aria-describedby')?.split(' ')).toContain(alert.id)
      expect(loadMockConnections()).toEqual([]) // nothing saved
    },
  )

  it('the writable login refusal carries its fix', async () => {
    const user = userEvent.setup()
    await openTab()

    const form = await fill(user, { user: T.writerUser })
    await user.click(within(form).getByRole('button', { name: 'Check and add' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent('REVOKE CREATE ON SCHEMA')
  })

  it('a name already used says so', async () => {
    saveMockConnection(SALES)
    const user = userEvent.setup()
    await openTab()

    const form = await fill(user)
    await user.click(within(form).getByRole('button', { name: 'Check and add' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent(
      "This workspace already has a database called 'Sales'.",
    )
    expect(within(form).getByLabelText('Name')).toHaveAttribute('aria-invalid', 'true')
  })

  it('too many checks: a countdown on the button', async () => {
    const user = userEvent.setup()
    await openTab()

    const form = await fill(user, { host: T.tooManyTriesHost })
    await user.click(within(form).getByRole('button', { name: 'Check and add' }))

    expect(await within(form).findByRole('alert')).toHaveTextContent('Too many database checks')
    expect(within(form).getByText('Try again in 30 seconds')).toBeInTheDocument()
  })

  it('"Test again" checks it once more and says so', async () => {
    saveMockConnection(SALES)
    const user = userEvent.setup()
    await openTab()

    await user.click(await screen.findByRole('button', { name: 'Test again Sales' }))

    expect(
      await screen.findByText('Sales: still reachable, and the login can still only read.'),
    ).toBeVisible()
  })

  it('a failed "Test again" says why, in the database\'s row', async () => {
    saveMockConnection(SALES)
    server.use(http.post(`${CONNECTIONS_URL}/:id/test`, () => apiError('connection_writable')))
    const user = userEvent.setup()
    await openTab()

    await user.click(await screen.findByRole('button', { name: 'Test again Sales' }))

    const row = screen.getByRole('listitem', { name: 'Sales' })
    expect(await within(row).findByRole('alert')).toHaveTextContent('That login can change data.')
  })

  it('delete asks first, warns about the conversations, then focuses the heading', async () => {
    saveMockConnection(SALES)
    const user = userEvent.setup()
    await openTab()

    await user.click(await screen.findByRole('button', { name: 'Delete Sales' }))
    const dialog = await screen.findByRole('dialog', { name: 'Delete Sales?' })
    expect(dialog).toHaveTextContent('every conversation about it is deleted too')
    await user.click(within(dialog).getByRole('button', { name: 'Delete' }))

    expect(await screen.findByText('No database yet')).toBeVisible()
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 2, name: 'Databases' })).toHaveFocus(),
    )
    expect(loadMockConnections()).toEqual([])
  })

  it('with a database but no LLM key: asking needs one, with a link to the key tab', async () => {
    saveMockConnection(SALES)
    await openTab()

    const link = await screen.findByRole('link', { name: /Save one in the LLM key tab/ })
    expect(link).toHaveAttribute('href', '/settings/key')
  })

  it('with a key, no such note', async () => {
    saveMockConnection(SALES)
    saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_saved_1234' })
    await openTab()
    await screen.findByRole('listitem', { name: 'Sales' })

    expect(screen.queryByText(/run on your own LLM key/)).not.toBeInTheDocument()
  })

  it('a viewer sees the list, but no form and no buttons', async () => {
    saveMockConnection(SALES)
    setSession({ ...mockSession, user: { ...mockSession.user, role: 'viewer' } })
    await openTab()

    expect(await screen.findByRole('listitem', { name: 'Sales' })).toBeVisible()
    expect(screen.getByText(/Only an admin of this workspace can add/)).toBeVisible()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /Test again|Delete/ })).not.toBeInTheDocument()
  })

  it("when the list can't be read: why, and a way to try again", async () => {
    server.use(http.get(CONNECTIONS_URL, () => apiError('history_unavailable')))
    await openTab()

    expect(await screen.findByText("Your databases can't be listed right now.")).toBeVisible()
  })
})
