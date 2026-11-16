import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it } from 'vitest'

import { getSession, setSession } from '@/api/session'
import { AUTH_URL, MEMBERS_URL } from '@/mocks/handlers'
import { loadMockMembers, MEMBER_TRIGGERS, setMockRole } from '@/mocks/members'
import { server } from '@/mocks/server'
import { MOCK_ROLE_KEY, mockSession, mockUser, OTHER_WORKSPACE } from '@/mocks/session'
import { renderApp } from '@/test/render'

afterEach(() => server.events.removeAllListeners())

const SAM = '5f0d6c1e-0000-4000-8000-000000000011'

async function openTab() {
  const rendered = renderApp('/settings/members')
  await screen.findByRole('list', { name: 'Members' })
  return rendered
}

const row = (email: string) => screen.getByRole('listitem', { name: new RegExp(email) })
const roleOf = (email: string) => screen.getByRole('combobox', { name: `Role of ${email}` })

function asViewer() {
  localStorage.setItem(MOCK_ROLE_KEY, 'viewer')
  setSession({ ...mockSession, user: { ...mockUser, role: 'viewer' } })
}

describe('Settings → Members', () => {
  it('lists the members with their role, rows and when they joined, you marked', async () => {
    await openTab()

    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).getByRole('link', { name: 'Members' })).toHaveAttribute(
      'aria-current',
      'page',
    )
    expect(row(mockUser.email)).toHaveTextContent('(you)')
    expect(roleOf(mockUser.email)).toHaveValue('admin')
    expect(roleOf('sam@example.com')).toHaveValue('analyst')
    expect(row('sam@example.com')).toHaveTextContent('Rows: Eastern')
    expect(row('lee@example.com')).toHaveTextContent('Rows: All rows')
    await waitFor(() => expect(document.title).toBe('Members · Settings · QueryMind'))
  })

  it('a role chosen on the row is saved at once', async () => {
    const sent: unknown[] = []
    server.events.on('request:start', async ({ request }) => {
      if (request.method === 'PATCH') sent.push(await request.clone().json())
    })
    const user = userEvent.setup()
    await openTab()

    await user.selectOptions(roleOf('sam@example.com'), 'viewer')

    expect(await screen.findByText('sam@example.com is viewer now.')).toBeVisible()
    expect(roleOf('sam@example.com')).toHaveValue('viewer')
    expect(sent).toEqual([{ role: 'viewer' }])
  })

  it('the last admin can’t stop being one: asked first, refused, and the select goes back', async () => {
    const user = userEvent.setup()
    await openTab()

    await user.selectOptions(roleOf(mockUser.email), 'viewer')
    const dialog = await screen.findByRole('dialog', { name: 'Make yourself viewer?' })
    await user.click(within(dialog).getByRole('button', { name: 'Make me viewer' }))

    const alert = await within(row(mockUser.email)).findByRole('alert')
    expect(alert).toHaveTextContent('make someone else an admin first')
    expect(roleOf(mockUser.email)).toHaveValue('admin')
    expect(getSession()?.user.role).toBe('admin')
  })

  it('with another admin, making yourself an analyst closes the admin tabs', async () => {
    setMockRole(SAM, 'admin')
    const user = userEvent.setup()
    const { router } = await openTab()

    await user.selectOptions(roleOf(mockUser.email), 'analyst')
    const dialog = await screen.findByRole('dialog', { name: 'Make yourself analyst?' })
    await user.click(within(dialog).getByRole('button', { name: 'Make me analyst' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/key'))
    expect(getSession()?.user.role).toBe('analyst')
    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).queryByRole('link', { name: 'Members' })).not.toBeInTheDocument()
  })

  it('Cancel keeps you an admin, and nothing is sent', async () => {
    let patches = 0
    server.events.on('request:start', ({ request }) => {
      if (request.method === 'PATCH') patches += 1
    })
    const user = userEvent.setup()
    await openTab()

    await user.selectOptions(roleOf(mockUser.email), 'viewer')
    await user.click(await screen.findByRole('button', { name: 'Cancel' }))

    expect(roleOf(mockUser.email)).toHaveValue('admin')
    expect(patches).toBe(0)
  })

  it('adds someone who has signed up, and empties the form', async () => {
    const user = userEvent.setup()
    await openTab()

    const form = screen.getByRole('form', { name: 'Add a member' })
    await user.type(within(form).getByLabelText('Email'), 'Ana@Example.com')
    await user.selectOptions(within(form).getByLabelText('Role'), 'analyst')
    expect(form).toHaveTextContent('Analyst: asks questions, on the tables allowed to analysts.')
    await user.click(within(form).getByRole('button', { name: 'Add' }))

    expect(await screen.findByText('ana@example.com added as analyst.')).toBeVisible()
    expect(roleOf('ana@example.com')).toHaveValue('analyst')
    expect(within(form).getByLabelText('Email')).toHaveValue('')
    expect(within(form).getByLabelText('Role')).toHaveValue('viewer')
  })

  it.each([
    [MEMBER_TRIGGERS.unknownEmail, 'they need to sign up first'],
    ['sam@example.com', 'already a member of this workspace'],
  ])('%s: says why, next to the email', async (email, why) => {
    const user = userEvent.setup()
    await openTab()

    const form = screen.getByRole('form', { name: 'Add a member' })
    const field = within(form).getByLabelText('Email')
    await user.type(field, email)
    await user.click(within(form).getByRole('button', { name: 'Add' }))

    const alert = await within(form).findByRole('alert')
    expect(alert).toHaveTextContent(why)
    expect(field).toHaveAttribute('aria-invalid', 'true')
    expect(field.getAttribute('aria-describedby')).toBe(alert.id)
  })

  it('too many adds: a countdown on the button', async () => {
    const user = userEvent.setup()
    await openTab()

    const form = screen.getByRole('form', { name: 'Add a member' })
    await user.type(within(form).getByLabelText('Email'), MEMBER_TRIGGERS.tooManyEmail)
    await user.click(within(form).getByRole('button', { name: 'Add' }))

    expect(await within(form).findByText(/30 seconds/)).toBeVisible()
  })

  it('removes a member after asking, then focuses the heading', async () => {
    const user = userEvent.setup()
    await openTab()

    await user.click(screen.getByRole('button', { name: 'Remove sam@example.com' }))
    const dialog = await screen.findByRole('dialog', { name: 'Remove sam@example.com?' })
    expect(dialog).toHaveTextContent('They lose access to this workspace at their next request.')
    await user.click(within(dialog).getByRole('button', { name: 'Remove' }))

    await waitFor(() =>
      expect(screen.queryByRole('listitem', { name: /sam@example.com/ })).not.toBeInTheDocument(),
    )
    expect(screen.getByRole('heading', { level: 2, name: 'Members' })).toHaveFocus()
    expect(loadMockMembers().map(({ email }) => email)).not.toContain('sam@example.com')
  })

  it('Leave: asked first, then the app moves to another of your workspaces', async () => {
    setMockRole(SAM, 'admin') // the workspace keeps an admin
    server.use(
      http.post(AUTH_URL.refresh, () =>
        HttpResponse.json({
          access_token: 'renewed',
          token_type: 'bearer',
          expires_in: 900,
          user: { ...mockUser, role: 'viewer', workspace: OTHER_WORKSPACE },
        }),
      ),
    )
    const user = userEvent.setup()
    const { router } = await openTab()

    await user.click(await screen.findByRole('button', { name: 'Leave' }))
    const dialog = await screen.findByRole('dialog', { name: 'Leave this workspace?' })
    await user.click(within(dialog).getByRole('button', { name: 'Leave' }))

    expect(await screen.findByText(/You're now in Acme Sales/)).toBeVisible()
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(getSession()?.user.workspace).toEqual(OTHER_WORKSPACE)
  })

  it('your only workspace can’t be left: it would lock you out', async () => {
    server.use(
      http.get('/api/auth/workspaces', () =>
        HttpResponse.json({
          workspaces: [{ ...mockUser.workspace, role: 'admin', current: true }],
        }),
      ),
    )
    await openTab()

    expect(await within(row(mockUser.email)).findByText(/you can’t leave it/)).toBeVisible()
    expect(within(row(mockUser.email)).queryByRole('button', { name: 'Leave' })).toBeNull()
  })

  it('another role sees no Members tab, and its URL says why, without asking the API', async () => {
    asViewer()
    let asked = false
    server.use(
      http.get(MEMBERS_URL, () => {
        asked = true
        return new Response(null, { status: 500 })
      }),
    )
    renderApp('/settings/members')

    expect(await screen.findByText(/Only an admin of this workspace can see/)).toBeVisible()
    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    expect(within(tabs).queryByRole('link', { name: 'Members' })).not.toBeInTheDocument()
    expect(asked).toBe(false)
  })

  it.each([
    ['admin', true],
    ['viewer', false],
  ] as const)('the command palette offers Members to an %s: %s', async (role, offered) => {
    if (role === 'viewer') asViewer()
    const user = userEvent.setup()
    renderApp('/settings/key')
    await screen.findByRole('heading', { level: 1, name: 'Settings' })

    await user.keyboard('{Control>}k{/Control}')
    const palette = await screen.findByRole('dialog', { name: 'Command palette' })
    await user.type(within(palette).getByRole('combobox'), 'settings')

    expect(await within(palette).findByRole('option', { name: /LLM key/ })).toBeVisible()
    expect(within(palette).queryByRole('option', { name: /^Members/ }) !== null).toBe(offered)
  })
})
