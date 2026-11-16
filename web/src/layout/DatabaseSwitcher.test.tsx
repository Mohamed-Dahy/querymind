import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it } from 'vitest'

import type { AskRequest } from '@/api/client'
import { getChosenDatabase } from '@/api/database'
import { recordTurn } from '@/mocks/conversations'
import { answer } from '@/mocks/fixtures'
import { STREAM_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { mockSession } from '@/mocks/session'
import { saveMockConnection, saveMockKey } from '@/mocks/settings'
import { renderApp } from '@/test/render'

import { explorerPath } from './useDatabase'

afterEach(() => server.events.removeAllListeners())

const STORAGE_KEY = `querymind-database:${mockSession.user.workspace.id}`

function saveSales() {
  const sales = saveMockConnection({
    name: 'Sales',
    host: 'db.example.com',
    port: 5432,
    dbname: 'sales',
    user: 'querymind_reader',
    password: 'secret',
    sslmode: 'verify-full',
  })
  if (sales === null) throw new Error('Sales was saved already')
  return sales
}

function saveKey() {
  saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_saved_1234' })
}

/** Every question the canvas sends, as the API receives it. */
function recordAsks(): AskRequest[] {
  const asks: AskRequest[] = []
  server.events.on('request:start', async ({ request }) => {
    if (new URL(request.url).pathname === STREAM_URL) {
      asks.push((await request.clone().json()) as AskRequest)
    }
  })
  return asks
}

/** Type a question and send it with Ctrl+Enter (Enter alone is a new line). */
async function ask(user: ReturnType<typeof userEvent.setup>, question: string) {
  await user.type(screen.getByRole('textbox', { name: 'Your question' }), question)
  await user.keyboard('{Control>}{Enter}{/Control}')
}

/** The top bar's switcher (the page's copy is for phones; jsdom has no screen width). */
const barSwitcher = async () =>
  within(screen.getByRole('banner')).findByRole('combobox', { name: 'Database' })

describe('the database switcher', () => {
  it('with no database of its own, the workspace sees the demo, as before', async () => {
    renderApp('/')
    await screen.findByRole('heading', { level: 1, name: 'Ask about your data' })

    expect(within(screen.getByRole('banner')).getByText('Northwind + Chinook')).toBeVisible()
    expect(screen.queryByRole('combobox', { name: 'Database' })).not.toBeInTheDocument()
  })

  it('lists the demo and each saved database, the demo chosen first', async () => {
    saveSales()
    renderApp('/')

    const switcher = await barSwitcher()
    expect(
      within(switcher)
        .getAllByRole('option')
        .map((option) => option.textContent),
    ).toEqual(['Demo: Northwind + Chinook', 'Sales'])
    expect(switcher).toHaveDisplayValue('Demo: Northwind + Chinook')
  })

  it('a new conversation asks the chosen database; a follow-up doesn’t say it again', async () => {
    const sales = saveSales()
    saveKey()
    const asks = recordAsks()
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await user.selectOptions(await barSwitcher(), 'Sales')
    expect(await screen.findByText('Ask anything about Sales')).toBeVisible()
    await ask(user, 'Top clients?')
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    await ask(user, 'Only 2024?')
    await waitFor(() => expect(asks).toHaveLength(2))

    expect(asks[0]).toEqual({
      question: 'Top clients?',
      conversation_id: null,
      connection_id: sales.id,
    })
    expect(asks[1]).toEqual({ question: 'Only 2024?', conversation_id: expect.any(String) })
    // The history says which database it was about.
    const history = screen.getByRole('region', { name: 'History' })
    expect(await within(history).findByText('Sales')).toBeInTheDocument()
  })

  it('your own database without your own key: the reason, and a link to save one', async () => {
    saveSales()
    const user = userEvent.setup()
    renderApp('/')

    await user.selectOptions(await barSwitcher(), 'Sales')
    await ask(user, 'Top clients?')

    // In the cell (the screen reader's announcement says it too).
    const cells = await screen.findByRole('list', { name: 'Questions and answers' })
    expect(
      await within(cells).findByText('Your own database needs your own LLM key.'),
    ).toBeVisible()
    expect(screen.getByRole('link', { name: 'Save your own key' })).toHaveAttribute(
      'href',
      '/settings/key',
    )
  })

  it('switching in a conversation starts a new one', async () => {
    saveSales()
    recordTurn({ ...answer, conversation_id: '3f2a9c1e-0000-4000-8000-0000000000aa' }, [], null)
    const user = userEvent.setup()
    const { router } = renderApp('/c/3f2a9c1e-0000-4000-8000-0000000000aa')
    await screen.findByText(answer.answer)

    await user.selectOptions(await barSwitcher(), 'Sales')

    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(await screen.findByText('Ask anything about Sales')).toBeVisible()
  })

  it('opening a past conversation about another database switches to it', async () => {
    const sales = saveSales()
    recordTurn({ ...answer, conversation_id: '3f2a9c1e-0000-4000-8000-0000000000bb' }, [], sales.id)
    renderApp('/c/3f2a9c1e-0000-4000-8000-0000000000bb')

    await waitFor(async () => expect(await barSwitcher()).toHaveDisplayValue('Sales'))
    expect(getChosenDatabase()).toBe(sales.id)
  })

  it('Back to a conversation after switching away switches back to its database', async () => {
    const sales = saveSales()
    recordTurn({ ...answer, conversation_id: '3f2a9c1e-0000-4000-8000-0000000000dd' }, [], sales.id)
    const user = userEvent.setup()
    const { router } = renderApp('/c/3f2a9c1e-0000-4000-8000-0000000000dd')
    await waitFor(async () => expect(await barSwitcher()).toHaveDisplayValue('Sales'))

    await user.selectOptions(await barSwitcher(), 'Demo: Northwind + Chinook')
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    await router.navigate(-1)

    await waitFor(() =>
      expect(router.state.location.pathname).toBe('/c/3f2a9c1e-0000-4000-8000-0000000000dd'),
    )
    await waitFor(async () => expect(await barSwitcher()).toHaveDisplayValue('Sales'))
  })

  it("an answer's tables in the explorer stay on its database", async () => {
    const sales = saveSales()
    recordTurn({ ...answer, conversation_id: '3f2a9c1e-0000-4000-8000-0000000000ee' }, [], sales.id)
    const user = userEvent.setup()
    renderApp('/c/3f2a9c1e-0000-4000-8000-0000000000ee')

    await user.click(await screen.findByRole('button', { name: 'Show your work' }))
    const link = await screen.findByRole('link', { name: 'See these tables' })

    const href = new URL(link.getAttribute('href') ?? '', 'http://x')
    expect(href.pathname).toBe('/explore')
    expect(href.searchParams.get('used')).toBeTruthy()
    expect(href.searchParams.get('db')).toBe(sales.id)
  })

  it('is remembered for the workspace; a database deleted since falls back to the demo', async () => {
    const sales = saveSales()
    localStorage.setItem(STORAGE_KEY, sales.id)
    const first = renderApp('/')
    expect(await barSwitcher()).toHaveDisplayValue('Sales')
    first.unmount()

    localStorage.setItem('querymind-mock-connections', '[]') // deleted elsewhere
    renderApp('/')
    await screen.findByRole('heading', { level: 1, name: 'Ask about your data' })

    await waitFor(() => expect(getChosenDatabase()).toBeNull())
    expect(localStorage.getItem(STORAGE_KEY)).toBeNull()
  })
})

describe('the explorer on a saved database', () => {
  it('follows the switcher, with ?db= in its URL and its links', async () => {
    const sales = saveSales()
    const user = userEvent.setup()
    const { router } = renderApp('/explore')
    await screen.findByRole('heading', { level: 1, name: 'Data explorer' })

    await user.selectOptions(await barSwitcher(), 'Sales')

    await waitFor(() => expect(router.state.location.search).toBe(`?db=${sales.id}`))
    expect(await screen.findByText(/No descriptions yet/)).toBeVisible()
    const list = screen.getByRole('region', { name: 'List of tables' })
    expect(within(list).getByRole('button', { name: /^customers/ })).toBeInTheDocument()
    expect(within(list).queryByRole('button', { name: /^album/ })).not.toBeInTheDocument() // Chinook's
    expect(screen.getByRole('link', { name: /Business glossary/ })).toHaveAttribute(
      'href',
      `/explore/glossary?db=${sales.id}`,
    )
  })

  it('a link with ?db= sets the whole app to that database', async () => {
    const sales = saveSales()
    renderApp(`/explore?db=${sales.id}`)

    await waitFor(async () => expect(await barSwitcher()).toHaveDisplayValue('Sales'))
    expect(getChosenDatabase()).toBe(sales.id)
  })

  it('a database that isn’t one of yours says so', async () => {
    renderApp('/explore?db=3f2a9c1e-0000-4000-8000-0000000000cc')

    expect(await screen.findByText('Database not found.')).toBeVisible()
  })

  it('the glossary says your own database has no business terms yet', async () => {
    const sales = saveSales()
    renderApp(`/explore/glossary?db=${sales.id}`)

    expect(await screen.findByText(/only for the demo databases/)).toBeVisible()
  })
})

describe('explorerPath', () => {
  it.each([
    ['/explore', null, '/explore'],
    ['/explore', 'abc', '/explore?db=abc'],
    ['/explore?table=orders', 'abc', '/explore?table=orders&db=abc'],
    ['/explore/glossary#revenue', 'abc', '/explore/glossary?db=abc#revenue'],
  ])('%s on %s → %s', (path, database, expected) => {
    expect(explorerPath(path, database)).toBe(expected)
  })
})
