import { act, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { afterEach, describe, expect, it, vi } from 'vitest'

import { answer } from '@/mocks/fixtures'
import { addSavedQuestion, recordTurn } from '@/mocks/conversations'
import { apiError, SAVED_QUESTIONS_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

import { UNDO_MS } from './useDeleteWithUndo'

const LIST_URL = '/api/conversations'

/** Saves a conversation in the fake API, as if asked earlier. Returns its id. */
function saved(question: string): string {
  const id = crypto.randomUUID()
  recordTurn({ ...answer, question, conversation_id: id }, [])
  return id
}

const history = () => screen.getByRole('region', { name: 'History' })
const row = (title: string) => within(history()).getByRole('link', { name: title })

afterEach(() => {
  vi.useRealTimers()
})

describe('HistoryRail', () => {
  it('lists saved conversations under their day, the open one marked', async () => {
    const id = saved('Sales per category?')
    saved('Top 3 artists')

    renderApp(`/c/${id}`)

    expect(await within(history()).findByRole('heading', { name: 'Today' })).toBeInTheDocument()
    await waitFor(() => expect(row('Sales per category?')).toHaveAttribute('aria-current', 'page'))
    expect(row('Top 3 artists')).not.toHaveAttribute('aria-current')
    expect(row('Top 3 artists')).toHaveAttribute('href', expect.stringMatching(/^\/c\//))
  })

  it('says so when there is nothing yet', async () => {
    renderApp('/')

    expect(
      await within(history()).findByText('Your past conversations will be listed here.'),
    ).toBeInTheDocument()
  })

  it('a new answer appears in the list without a reload', async () => {
    const user = userEvent.setup()
    renderApp('/')

    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'Sales per category?')
    await user.keyboard('{Control>}{Enter}{/Control}')

    expect(
      await within(history()).findByRole('link', { name: 'Sales per category?' }),
    ).toBeVisible()
  })

  it('searches after a pause in the typing', async () => {
    saved('Sales per category?')
    saved('Top 3 artists')
    const urls: string[] = []
    server.events.on('request:start', ({ request }) => {
      if (request.url.includes(LIST_URL)) urls.push(request.url)
    })
    const user = userEvent.setup()
    renderApp('/')
    await within(history()).findByRole('link', { name: 'Top 3 artists' })
    urls.length = 0

    await user.type(within(history()).getByRole('searchbox'), 'artists')

    await waitFor(() =>
      expect(within(history()).queryByRole('link', { name: 'Sales per category?' })).toBeNull(),
    )
    expect(row('Top 3 artists')).toBeInTheDocument()
    expect(urls).toHaveLength(1) // not one request per letter
    server.events.removeAllListeners()
  })

  it('says when a search finds nothing', async () => {
    saved('Sales per category?')
    const user = userEvent.setup()
    renderApp('/')
    await within(history()).findByRole('link', { name: 'Sales per category?' })

    await user.type(within(history()).getByRole('searchbox'), 'zebra')

    expect(await within(history()).findByText('No conversation matches.')).toBeInTheDocument()
  })

  it('renames with Enter, and gives the focus back', async () => {
    saved('Sales per category?')
    const user = userEvent.setup()
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', { name: 'Rename “Sales per category?”' }),
    )
    const box = within(history()).getByRole('textbox', { name: 'New title' })
    expect(box).toHaveFocus()
    await user.clear(box)
    await user.keyboard('Weekly numbers{Enter}')

    expect(await within(history()).findByRole('link', { name: 'Weekly numbers' })).toBeVisible()
    await waitFor(() => expect(screen.getByRole('button', { name: /^Rename/ })).toHaveFocus())
  })

  it('Escape cancels a rename, and an empty title is not saved', async () => {
    saved('Sales per category?')
    const user = userEvent.setup()
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', { name: 'Rename “Sales per category?”' }),
    )
    const box = within(history()).getByRole('textbox', { name: 'New title' })
    await user.clear(box)
    await user.keyboard('{Enter}')
    expect(within(history()).getByRole('textbox', { name: 'New title' })).toBeInTheDocument()

    await user.keyboard('Other{Escape}')

    expect(
      await within(history()).findByRole('link', { name: 'Sales per category?' }),
    ).toBeVisible()
  })

  it('pins to the top, and unpins', async () => {
    saved('Sales per category?')
    saved('Top 3 artists')
    const user = userEvent.setup()
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', { name: 'Pin “Sales per category?”' }),
    )

    const pinned = await within(history()).findByRole('heading', { name: 'Pinned' })
    const section = pinned.closest('section') as HTMLElement
    expect(within(section).getByRole('link', { name: /Sales per category\?/ })).toBeVisible()

    await user.click(within(history()).getByRole('button', { name: 'Unpin “Sales per category?”' }))
    await waitFor(() =>
      expect(within(history()).queryByRole('heading', { name: 'Pinned' })).toBeNull(),
    )
  })

  it('delete hides the row at once, and Undo brings it back with nothing sent', async () => {
    saved('Sales per category?')
    const deletes: string[] = []
    server.events.on('request:start', ({ request }) => {
      if (request.method === 'DELETE') deletes.push(request.url)
    })
    const user = userEvent.setup()
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', { name: 'Delete “Sales per category?”' }),
    )

    expect(within(history()).queryByRole('link', { name: 'Sales per category?' })).toBeNull()
    expect(within(history()).getByRole('searchbox')).toHaveFocus() // the button has gone
    await user.click(await screen.findByRole('button', { name: 'Undo' }))

    expect(
      await within(history()).findByRole('link', { name: 'Sales per category?' }),
    ).toBeVisible()
    expect(deletes).toEqual([])
    server.events.removeAllListeners()
  })

  it('without an Undo, the delete really goes out when the time ends', async () => {
    const id = saved('Sales per category?')
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', { name: 'Delete “Sales per category?”' }),
    )
    await act(() => vi.advanceTimersByTimeAsync(UNDO_MS))

    await waitFor(() =>
      expect(localStorage.getItem('querymind-mock-conversations')).not.toContain(id),
    )
  })

  it('closing the page sends a waiting delete at once', async () => {
    const id = saved('Sales per category?')
    const user = userEvent.setup()
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', { name: 'Delete “Sales per category?”' }),
    )
    expect(localStorage.getItem('querymind-mock-conversations')).toContain(id)

    window.dispatchEvent(new Event('pagehide'))

    await waitFor(() =>
      expect(localStorage.getItem('querymind-mock-conversations')).not.toContain(id),
    )
  })

  it('deleting the open conversation goes to an empty canvas', async () => {
    const id = saved('Sales per category?')
    const user = userEvent.setup()
    const { router } = renderApp(`/c/${id}`)

    await screen.findByRole('article', { name: 'Sales per category?' })
    await user.click(
      await within(history()).findByRole('button', { name: 'Delete “Sales per category?”' }),
    )

    await waitFor(() => expect(router.state.location.pathname).toBe('/'))
    expect(screen.queryByRole('article')).not.toBeInTheDocument()
  })

  it('a failed delete brings the row back and says so', async () => {
    saved('Sales per category?')
    server.use(http.delete(`${LIST_URL}/:id`, () => apiError('history_unavailable')))
    vi.useFakeTimers({ shouldAdvanceTime: true })
    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime })
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', { name: 'Delete “Sales per category?”' }),
    )
    await act(() => vi.advanceTimersByTimeAsync(UNDO_MS))

    expect(
      await within(history()).findByRole('link', { name: 'Sales per category?' }),
    ).toBeVisible()
    expect(await screen.findByText("History isn't available.")).toBeInTheDocument()
  })

  it('with history off it says so calmly, and questions still work', async () => {
    server.use(http.get(LIST_URL, () => apiError('history_unavailable')))
    const user = userEvent.setup()
    renderApp('/')

    expect(await within(history()).findByText(/isn't available on this server/)).toBeVisible()

    await user.type(screen.getByRole('textbox', { name: 'Your question' }), 'Sales per category?')
    await user.keyboard('{Control>}{Enter}{/Control}')
    expect(await screen.findByText(answer.answer)).toBeInTheDocument()
  })

  it('shows more when there are more', async () => {
    server.use(
      http.get(LIST_URL, ({ request }) => {
        const limit = Number(new URL(request.url).searchParams.get('limit'))
        return HttpResponse.json({
          conversations: Array.from({ length: limit }, (_, index) => ({
            id: crypto.randomUUID(),
            title: `Question ${index + 1}`,
            pinned: false,
            created_at: new Date().toISOString(),
            updated_at: new Date().toISOString(),
            turns: 1,
          })),
          has_more: limit < 60,
        })
      }),
    )
    const user = userEvent.setup()
    renderApp('/')

    await user.click(await within(history()).findByRole('button', { name: 'Show more' }))

    expect(await within(history()).findByRole('link', { name: 'Question 60' })).toBeVisible()
    expect(within(history()).queryByRole('button', { name: 'Show more' })).toBeNull()
  })
})

describe('saved questions in the rail', () => {
  const ask = (question: string) =>
    within(history()).findByRole('button', { name: `Ask “${question}”` })

  it('shows no section until something is saved', async () => {
    renderApp('/')

    await within(history()).findByText('Your past conversations will be listed here.')
    expect(within(history()).queryByRole('heading', { name: 'Saved' })).toBeNull()
  })

  it('lists them under their own heading', async () => {
    addSavedQuestion('Sales this week?')
    renderApp('/')

    expect(await ask('Sales this week?')).toBeVisible()
    expect(within(history()).getByRole('heading', { name: 'Saved' })).toBeInTheDocument()
  })

  it('one click asks it as a new conversation', async () => {
    addSavedQuestion('Sales this week?')
    const user = userEvent.setup()
    const { router } = renderApp('/')

    await user.click(await ask('Sales this week?'))

    expect(await screen.findByRole('article', { name: 'Sales this week?' })).toBeInTheDocument()
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
  })

  it('removes one, and gives the focus somewhere that stays', async () => {
    addSavedQuestion('Sales this week?')
    const user = userEvent.setup()
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', {
        name: 'Remove “Sales this week?” from saved',
      }),
    )

    await waitFor(() =>
      expect(within(history()).queryByRole('heading', { name: 'Saved' })).toBeNull(),
    )
    expect(within(history()).getByRole('searchbox')).toHaveFocus()
    expect(localStorage.getItem('querymind-mock-saved-questions')).toBe('[]')
  })

  it('a failed remove brings the row back and says so', async () => {
    addSavedQuestion('Sales this week?')
    server.use(http.delete(`${SAVED_QUESTIONS_URL}/:id`, () => apiError('history_unavailable')))
    const user = userEvent.setup()
    renderApp('/')

    await user.click(
      await within(history()).findByRole('button', {
        name: 'Remove “Sales this week?” from saved',
      }),
    )

    expect(await screen.findByText("History isn't available.")).toBeInTheDocument()
    expect(await ask('Sales this week?')).toBeVisible()
  })

  it('steps aside during a search', async () => {
    addSavedQuestion('Sales this week?')
    const user = userEvent.setup()
    renderApp('/')
    await ask('Sales this week?')

    await user.type(within(history()).getByRole('searchbox'), 'zebra')

    await waitFor(() =>
      expect(within(history()).queryByRole('heading', { name: 'Saved' })).toBeNull(),
    )
  })

  it('with history off there is nothing to show, and no error of its own', async () => {
    server.use(
      http.get(LIST_URL, () => apiError('history_unavailable')),
      http.get(SAVED_QUESTIONS_URL, () => apiError('history_unavailable')),
    )
    renderApp('/')

    await within(history()).findByText(/isn't available on this server/)
    expect(within(history()).queryByRole('heading', { name: 'Saved' })).toBeNull()
  })
})
