import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { AskRequest } from '@/api/client'
import { answer } from '@/mocks/fixtures'
import { recordTurn } from '@/mocks/conversations'
import { apiError, replyStream, STREAM_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

const palette = () => screen.findByRole('dialog', { name: 'Command palette' })
const searchBox = () => screen.getByRole('combobox')
const option = (name: string | RegExp) => screen.findByRole('option', { name })

async function openWithKeys(user: ReturnType<typeof userEvent.setup>) {
  await user.keyboard('{Control>}k{/Control}')
  return palette()
}

/** Saves a conversation in the fake API, as if asked earlier. Returns its id. */
function saved(question: string): string {
  const id = crypto.randomUUID()
  recordTurn({ ...answer, question, conversation_id: id }, [])
  return id
}

describe('CommandPalette', () => {
  it('opens with Ctrl+K, and Escape gives focus back where it was', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const composer = await screen.findByRole('textbox', { name: 'Your question' })
    composer.focus()

    await openWithKeys(user)
    expect(searchBox()).toHaveFocus()
    expect(await option(/New conversation/)).toBeInTheDocument()

    await user.keyboard('{Escape}')
    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    expect(composer).toHaveFocus()
  })

  it('opens from the visible Search button in the top bar (phones have no Ctrl key)', async () => {
    const user = userEvent.setup()
    renderApp('/')

    const button = within(screen.getByRole('banner')).getByRole('button', { name: /Search/ })
    expect(button).toHaveAttribute('aria-keyshortcuts', 'Control+K Meta+K')
    await user.click(button)

    expect(await palette()).toBeInTheDocument()
  })

  it('Enter opens the best match: a table, not a question', async () => {
    const asks: AskRequest[] = []
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        asks.push((await request.json()) as AskRequest)
        return HttpResponse.error()
      }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/')
    await openWithKeys(user)

    await user.type(searchBox(), 'orders')
    expect(await option(/^orders/)).toHaveAttribute('aria-selected', 'true')
    // Ask is still there, last.
    expect(screen.getAllByRole('option').at(-1)).toHaveAccessibleName(/Ask “orders”/)
    await user.keyboard('{Enter}')

    await waitFor(() => expect(router.state.location.pathname).toBe('/explore/tables/orders'))
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument()
    expect(asks).toEqual([])
    // The new page has the focus, as after any link.
    await waitFor(() => expect(document.getElementById('main')).toHaveFocus())
  })

  it('finds a business term by another word for it, and opens it in the glossary', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/')
    await openWithKeys(user)

    await user.type(searchBox(), 'late ship')
    await user.click(await option(/Late shipment/))

    await waitFor(() => expect(router.state.location.hash).toBe('#term-late_shipment'))
    expect(router.state.location.pathname).toBe('/explore/glossary')
  })

  it('jumps to a saved conversation', async () => {
    const id = saved('Sales per category?')
    const user = userEvent.setup()
    const { router } = renderApp('/explore')
    await openWithKeys(user)

    // Before anything is typed, the most recent conversations are listed.
    await user.click(await option(/Sales per category\?/))

    await waitFor(() => expect(router.state.location.pathname).toBe(`/c/${id}`))
  })

  it('asks in a new conversation only when nothing else matches', async () => {
    const asks: AskRequest[] = []
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        const body = (await request.json()) as AskRequest
        asks.push(body)
        return replyStream(body)
      }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/explore')
    await openWithKeys(user)

    await user.type(searchBox(), 'Which tracks are longest?')
    const ask = await option(/Ask “Which tracks are longest\?”/)
    expect(ask).toHaveAttribute('aria-selected', 'true')
    await user.keyboard('{Enter}')

    await waitFor(() => expect(asks).toHaveLength(1))
    expect(asks[0]).toMatchObject({ question: 'Which tracks are longest?', conversation_id: null })
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
  })

  it('switches the theme and keeps focus where it was', async () => {
    const user = userEvent.setup()
    renderApp('/')
    const light = within(screen.getByRole('group', { name: 'Theme' })).getByRole('button', {
      name: 'Light',
    })
    const button = within(screen.getByRole('banner')).getByRole('button', { name: /Search/ })
    await user.click(button)

    await user.type(searchBox(), 'light')
    await user.keyboard('{Enter}')

    await waitFor(() => expect(document.documentElement.dataset.theme).toBe('light'))
    expect(light).toHaveAttribute('aria-pressed', 'true')
    await waitFor(() => expect(button).toHaveFocus())
  })

  it('says when history is off, and the other groups still work', async () => {
    server.use(http.get('/api/conversations', () => apiError('history_unavailable')))
    const user = userEvent.setup()
    renderApp('/')
    await openWithKeys(user)

    expect(
      await within(await palette()).findByText(/History isn't available on this server/),
    ).toBeInTheDocument()
    await user.type(searchBox(), 'customers')
    expect(await option(/^customers/)).toBeInTheDocument()
  })

  it('Ctrl+K again closes it', async () => {
    const user = userEvent.setup()
    renderApp('/')
    await openWithKeys(user)

    await user.keyboard('{Control>}k{/Control}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
  })
})

// The palette is a shortcut, not the only way: each kind of item has a normal, visible path.
describe('every palette item also has a visible path', () => {
  it('ask: the composer', async () => {
    renderApp('/')
    expect(await screen.findByRole('textbox', { name: 'Your question' })).toBeVisible()
  })

  it('new conversation: the top-bar button', () => {
    renderApp('/explore')
    expect(
      within(screen.getByRole('banner')).getByRole('button', { name: 'New conversation' }),
    ).toBeVisible()
  })

  it('a conversation: the history rail', async () => {
    saved('Sales per category?')
    renderApp('/')
    const history = screen.getByRole('region', { name: 'History' })
    expect(await within(history).findByRole('link', { name: 'Sales per category?' })).toBeVisible()
  })

  it('a table: the explorer list', async () => {
    const user = userEvent.setup()
    renderApp('/explore')
    await user.click(await screen.findByRole('button', { name: /^orders/ }))
    expect(screen.getByRole('link', { name: 'Everything about orders' })).toHaveAttribute(
      'href',
      '/explore/tables/orders',
    )
  })

  it('a business term: the glossary', async () => {
    renderApp('/explore/glossary')
    expect(await screen.findByRole('listitem', { name: 'Late shipment' })).toHaveAttribute(
      'id',
      'term-late_shipment',
    )
  })

  it('a page: the main rail and the evals sub-menu', async () => {
    renderApp('/evals')
    const main = screen.getByRole('navigation', { name: 'Main' })
    for (const name of ['Canvas', 'Explore', 'Evals']) {
      expect(within(main).getByRole('link', { name })).toBeVisible()
    }
    const views = await screen.findByRole('navigation', { name: 'Evals lab views' })
    for (const name of ['Scoreboard', 'What each part adds', 'Models', 'Questions']) {
      expect(within(views).getByRole('link', { name })).toBeVisible()
    }
  })

  it('the theme: the theme switcher', () => {
    renderApp('/')
    const themes = screen.getByRole('group', { name: 'Theme' })
    for (const name of ['Dark', 'Light', 'System']) {
      expect(within(themes).getByRole('button', { name })).toBeVisible()
    }
  })
})
