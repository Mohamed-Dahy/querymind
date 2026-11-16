import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { getLLMKey, type SaveLLMKeyRequest } from '@/api/settings'
import { setSession } from '@/api/session'
import { providers } from '@/mocks/fixtures'
import { apiError, LLM_KEY_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { mockSession } from '@/mocks/session'
import { INVALID_KEY, saveMockKey, TOO_MANY_TRIES_KEY } from '@/mocks/settings'
import { renderApp } from '@/test/render'

const groq = providers.find(({ id }) => id === 'groq')!
const gemini = providers.find(({ id }) => id === 'gemini')!

function signedInAs(role: 'admin' | 'analyst' | 'viewer') {
  setSession({ ...mockSession, user: { ...mockSession.user, role } })
}

async function openKeyTab() {
  const { router } = renderApp('/settings/key')
  await screen.findByRole('heading', { level: 2, name: 'LLM key' })
  return router
}

const form = () => screen.findByRole('form', { name: /key$/ })
const keyField = () => screen.getByLabelText('API key')
const modelField = () => screen.getByRole('textbox', { name: 'Model' })

// recordSaves listens to the fake API's events: stop after each test.
afterEach(() => server.events.removeAllListeners())

/** Every PUT /llm/key the page sends, without changing what the fake API answers. */
function recordSaves(): SaveLLMKeyRequest[] {
  const saves: SaveLLMKeyRequest[] = []
  server.events.on('request:start', async ({ request }) => {
    if (request.method === 'PUT' && new URL(request.url).pathname === LLM_KEY_URL) {
      saves.push((await request.clone().json()) as SaveLLMKeyRequest)
    }
  })
  return saves
}

describe('the Settings page', () => {
  it('/settings opens the first tab, with its own URL and browser-tab title', async () => {
    const { router } = renderApp('/settings')

    expect(await screen.findByRole('heading', { level: 1, name: 'Settings' })).toBeVisible()
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/key'))
    const tabs = screen.getByRole('navigation', { name: 'Settings' })
    await waitFor(() =>
      expect(within(tabs).getByRole('link', { name: 'LLM key' })).toHaveAttribute(
        'aria-current',
        'page',
      ),
    )
    await waitFor(() => expect(document.title).toBe('LLM key · Settings · QueryMind'))
    expect(screen.getByText(mockSession.user.workspace.name)).toBeVisible()
  })

  it('an unknown tab is the 404 page', async () => {
    renderApp('/settings/nope')

    expect(await screen.findByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible()
  })

  it('opens from the account menu in the top bar', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/')
    const header = screen.getByRole('banner')
    const account = within(header).getByRole('button', { name: 'Account' })
    expect(account).toHaveAttribute('aria-expanded', 'false')

    await user.click(account)
    expect(account).toHaveAttribute('aria-expanded', 'true')
    await user.click(within(header).getByRole('link', { name: 'Settings' }))

    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/key'))
    expect(within(header).queryByRole('link', { name: 'Settings' })).not.toBeInTheDocument()
  })

  it('the account menu closes on Escape (focus back), a click outside, and Tab out', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const header = screen.getByRole('banner')
    const account = within(header).getByRole('button', { name: 'Account' })
    const settings = () => within(header).queryByRole('link', { name: 'Settings' })

    await user.click(account)
    await user.keyboard('{Escape}')
    expect(settings()).not.toBeInTheDocument()
    expect(account).toHaveFocus()

    await user.click(account)
    await user.click(screen.getByRole('main'))
    expect(settings()).not.toBeInTheDocument()

    await user.click(account)
    // The fake login is in two workspaces (U7.1d): the other one is a button too.
    await within(header).findByRole('button', { name: /Switch to Acme Sales/ })
    await user.tab() // Switch to Acme Sales
    await user.tab() // Settings
    await user.tab() // Log out
    await user.tab() // out of the menu
    expect(settings()).not.toBeInTheDocument()
  })
})

describe('Settings → LLM key', () => {
  it('without a key: the demo key, and a form ready for the first provider', async () => {
    await openKeyTab()

    expect(await screen.findByText('QueryMind’s free demo key')).toBeVisible()
    expect(await form()).toHaveAccessibleName('Save your own key')
    expect(screen.getByRole('combobox', { name: 'Provider' })).toHaveValue(groq.id)
    expect(modelField()).toHaveValue(groq.example_model)
    expect(keyField()).toHaveAttribute('type', 'password')
    expect(keyField()).toHaveAttribute('autocomplete', 'off')
    expect(screen.getByRole('link', { name: /Make one at Groq/ })).toHaveAttribute(
      'href',
      groq.keys_url,
    )
  })

  it('another provider brings its example model, but a model the user typed stays', async () => {
    const user = userEvent.setup()
    await openKeyTab()
    const provider = await screen.findByRole('combobox', { name: 'Provider' })

    await user.selectOptions(provider, gemini.id)
    expect(modelField()).toHaveValue(gemini.example_model)

    await user.clear(modelField())
    await user.type(modelField(), 'my-own-model')
    await user.selectOptions(provider, groq.id)
    expect(modelField()).toHaveValue('my-own-model')
  })

  it('saves after the test call: the card shows the new key, and the field is emptied', async () => {
    const saves = recordSaves()
    const user = userEvent.setup()
    await openKeyTab()
    await form()

    await user.type(keyField(), '  gsk_live_key_ABCD  ')
    await user.click(screen.getByRole('button', { name: 'Test and save' }))

    expect(await screen.findByText('Your own Groq key')).toBeVisible()
    expect(screen.getByText('ends in …ABCD')).toBeVisible()
    expect(screen.getByText(/Key saved\. The next question runs on Groq/)).toBeInTheDocument()
    expect(keyField()).toHaveValue('') // the key is not left on the page
    expect(await form()).toHaveAccessibleName('Replace the key')
    // Trimmed: a copied key often brings a space along.
    expect(saves).toEqual([
      { provider: 'groq', model: groq.example_model, api_key: 'gsk_live_key_ABCD' },
    ])
  })

  it('a refused key: the reason next to the form, the key field marked, nothing saved', async () => {
    const user = userEvent.setup()
    await openKeyTab()
    await form()

    await user.type(keyField(), INVALID_KEY)
    await user.click(screen.getByRole('button', { name: 'Test and save' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent("That key can't be used.")
    expect(alert).toHaveTextContent('Groq refused this key. Check it was copied whole')
    expect(keyField()).toHaveAttribute('aria-invalid', 'true')
    expect(keyField()).toHaveAccessibleDescription(/Groq refused this key/)
    expect(screen.getByText('QueryMind’s free demo key')).toBeVisible()
  })

  it('too many keys tried: a countdown, and the button waits it out', async () => {
    const saves = recordSaves()
    const user = userEvent.setup()
    await openKeyTab()
    await form()

    await user.type(keyField(), TOO_MANY_TRIES_KEY)
    await user.click(screen.getByRole('button', { name: 'Test and save' }))

    const alert = await screen.findByRole('alert')
    expect(alert).toHaveTextContent('Too many keys tried in a short time.')
    expect(screen.getByText('Try again in 30 seconds')).toBeInTheDocument()
    const button = screen.getByRole('button', { name: 'Test and save' })
    expect(button).toHaveAttribute('aria-disabled', 'true')

    await user.click(button)
    await user.type(keyField(), '{Enter}')
    expect(saves).toHaveLength(1) // neither a click nor Enter sent anything
  })

  it('a wait that starts after the form sat open still counts from the 429', async () => {
    const user = userEvent.setup()
    await openKeyTab()
    await form()
    // The form has been on screen for 5 minutes when the 429 comes.
    const opened = Date.now()
    const later = vi.spyOn(Date, 'now').mockReturnValue(opened + 300_000)

    await user.type(keyField(), TOO_MANY_TRIES_KEY)
    await user.click(screen.getByRole('button', { name: 'Test and save' }))

    expect(await screen.findByText('Try again in 30 seconds')).toBeInTheDocument()
    later.mockRestore()
  })

  it('a key saved before: what it is, never the key itself', async () => {
    saveMockKey({ provider: 'gemini', model: 'gemini-3.5-flash', api_key: 'AIza-secret-WXYZ' })
    await openKeyTab()

    expect(await screen.findByText('Your own Google Gemini key')).toBeVisible()
    expect(screen.getByText('gemini-3.5-flash', { selector: 'dd' })).toBeVisible()
    expect(screen.getByText('ends in …WXYZ')).toBeVisible()
    expect(document.body).not.toHaveTextContent('AIza-secret')
    // The form starts from what is saved, ready to replace it.
    expect(screen.getByRole('combobox', { name: 'Provider' })).toHaveValue('gemini')
  })

  it('remove asks first; Cancel changes nothing and gives focus back', async () => {
    saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_saved_key_1234' })
    const user = userEvent.setup()
    await openKeyTab()

    const remove = await screen.findByRole('button', { name: 'Remove key' })
    await user.click(remove)
    const dialog = await screen.findByRole('dialog', { name: 'Remove your Groq key?' })
    expect(dialog).toHaveTextContent('you’ll have to paste it again')

    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(remove).toHaveFocus())
    expect(screen.getByText('Your own Groq key')).toBeVisible()
  })

  it('remove: back to the demo key, and focus on the tab heading', async () => {
    saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_saved_key_1234' })
    const user = userEvent.setup()
    await openKeyTab()

    await user.click(await screen.findByRole('button', { name: 'Remove key' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Remove key' }))

    expect(await screen.findByText('QueryMind’s free demo key')).toBeVisible()
    await waitFor(() =>
      expect(screen.getByRole('heading', { level: 2, name: 'LLM key' })).toHaveFocus(),
    )
    expect(screen.getByText('Key removed. Questions use the demo key again.')).toBeInTheDocument()
    expect(await getLLMKey()).toBeNull()
  })

  it('a failed remove says why, inside the dialog, and keeps the key', async () => {
    saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_saved_key_1234' })
    server.use(http.delete(LLM_KEY_URL, () => apiError('accounts_unavailable')))
    const user = userEvent.setup()
    await openKeyTab()

    await user.click(await screen.findByRole('button', { name: 'Remove key' }))
    const dialog = await screen.findByRole('dialog')
    await user.click(within(dialog).getByRole('button', { name: 'Remove key' }))

    expect(await within(dialog).findByRole('alert')).toHaveTextContent(
      "Logins aren't working right now.",
    )
    expect(screen.getByText('Your own Groq key')).toBeInTheDocument()
  })

  it.each(['analyst', 'viewer'] as const)(
    'an %s sees which key answers, but no form and no remove',
    async (role) => {
      saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_saved_key_1234' })
      signedInAs(role)
      await openKeyTab()

      expect(await screen.findByText('Your own Groq key')).toBeVisible()
      expect(screen.getByText(/Only an admin of this workspace can save or remove/)).toBeVisible()
      expect(screen.queryByRole('form')).not.toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Remove key' })).not.toBeInTheDocument()
    },
  )

  it("an admin whose role just changed hears the API's 403 next to the form", async () => {
    server.use(http.put(LLM_KEY_URL, () => apiError('forbidden')))
    const user = userEvent.setup()
    await openKeyTab()
    await form()

    await user.type(keyField(), 'gsk_live_key_ABCD')
    await user.click(screen.getByRole('button', { name: 'Test and save' }))

    expect(await screen.findByRole('alert')).toHaveTextContent("Your role can't open this.")
  })

  it("when the key can't be read: why, and a way to try again", async () => {
    server.use(http.get(LLM_KEY_URL, () => apiError('accounts_unavailable')))
    await openKeyTab()

    expect(await screen.findByText("The key settings can't be shown right now.")).toBeVisible()
    server.resetHandlers()
    await userEvent.setup().click(screen.getByRole('button', { name: 'Try again' }))
    expect(await screen.findByText('QueryMind’s free demo key')).toBeVisible()
  })
})

describe('GET /llm/key', () => {
  it('null (no key saved) is an answer, not a bad response', async () => {
    expect(await getLLMKey()).toBeNull()
  })

  it('a saved key comes back without the key', async () => {
    server.use(
      http.get(LLM_KEY_URL, () =>
        HttpResponse.json({
          provider: 'groq',
          provider_label: 'Groq',
          model: 'openai/gpt-oss-120b',
          key_hint: 'WXYZ',
          updated_at: '2026-11-16T10:00:00Z',
        }),
      ),
    )

    expect(await getLLMKey()).toMatchObject({ provider: 'groq', key_hint: 'WXYZ' })
  })
})
