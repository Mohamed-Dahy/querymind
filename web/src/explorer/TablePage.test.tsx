import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import type { AskRequest } from '@/api/client'
import { answer, catalog, clarification, semantic } from '@/mocks/fixtures'
import { recordTurn } from '@/mocks/conversations'
import { apiError, replyStream, STREAM_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

const section = (name: string | RegExp) => screen.findByRole('region', { name })
const meaningOf = (name: string) =>
  semantic.businesses.flatMap(({ tables }) => tables).find((table) => table.name === name)

function recordAsks(): AskRequest[] {
  const bodies: AskRequest[] = []
  server.use(
    http.post(STREAM_URL, async ({ request }) => {
      const body = (await request.json()) as AskRequest
      bodies.push(body)
      return replyStream(body)
    }),
  )
  return bodies
}

describe('TablePage', () => {
  const SECTIONS = [
    'What it is',
    /^Columns \(\d+\)$/,
    'Joins',
    'Business terms',
    'Questions to ask',
  ]

  it.each(catalog.tables.map(({ name }) => name))(
    '%s: every section has something in it, or says why not',
    async (name) => {
      renderApp(`/explore/tables/${name}`)

      expect(await screen.findByRole('heading', { level: 1, name })).toBeInTheDocument()
      for (const title of SECTIONS) {
        const found = await section(title)
        const heading = within(found).getByRole('heading', { level: 2 }).textContent ?? ''
        const rest = (found.textContent ?? '').replace(heading, '').trim()
        expect(rest, `${name}: "${String(title)}" is empty`).not.toBe('')
      }
    },
  )

  it('shows the columns with their types, keys and PII badges', async () => {
    renderApp('/explore/tables/customers')

    const columns = await section(/^Columns \(/)
    const contact = within(columns).getByRole('row', { name: /^contact_name/ })
    expect(contact).toHaveTextContent('PII')
    expect(contact).toHaveTextContent('character varying')
    const key = within(columns).getByRole('row', { name: /^customer_id/ })
    expect(key).toHaveTextContent('primary key')
    expect(key).not.toHaveTextContent('PII')
  })

  it('says which column points where', async () => {
    renderApp('/explore/tables/orders')

    const columns = await section(/^Columns \(/)
    expect(within(columns).getByRole('row', { name: /^customer_id/ })).toHaveTextContent(
      'points to customers',
    )
  })

  it('marks notes nobody has reviewed as drafts, and explains the mark', async () => {
    expect(meaningOf('album')?.dictionary_reviewed).toBe(false)
    renderApp('/explore/tables/album')

    const about = await section('What it is')
    expect(within(about).getByText('AI draft:')).toBeInTheDocument()
    const columns = await section(/^Columns \(/)
    expect(within(columns).getByText(/not checked by a person yet/)).toBeInTheDocument()
    expect(within(columns).getAllByText('AI draft:').length).toBeGreaterThan(0)
  })

  it('a reviewed dictionary is not called a draft', async () => {
    const reviewed = {
      businesses: semantic.businesses.map((business) => ({
        ...business,
        tables: business.tables.map((table) => ({ ...table, dictionary_reviewed: true })),
      })),
    }
    server.use(http.get('/api/semantic', () => HttpResponse.json(reviewed)))
    renderApp('/explore/tables/album')

    const about = await section('What it is')
    expect(within(about).getByText('Data dictionary:')).toBeInTheDocument()
    expect(screen.queryByText('AI draft:')).toBeNull()
    expect(screen.queryByText(/not checked by a person yet/)).toBeNull()
  })

  it('shows joins both ways, and links to the other table', async () => {
    const user = userEvent.setup()
    renderApp('/explore/tables/orders')

    const joins = await section('Joins')
    expect(within(joins).getByText('orders.customer_id')).toBeInTheDocument()
    expect(within(joins).getByText('order_details.order_id')).toBeInTheDocument()

    await user.click(within(joins).getByRole('link', { name: 'Open customers' }))

    expect(await screen.findByRole('heading', { level: 1, name: 'customers' })).toBeInTheDocument()
  })

  it('a key to the same table is listed too', async () => {
    renderApp('/explore/tables/employees')

    const joins = await section('Joins')
    expect(within(joins).getByText('employees.reports_to')).toBeInTheDocument()
    expect(within(joins).getAllByText('employees.employee_id').length).toBeGreaterThan(0)
  })

  it('a table with no joins says so', async () => {
    renderApp('/explore/tables/us_states')

    expect(
      within(await section('Joins')).getByText(/no foreign keys, and no other table points to it/),
    ).toBeInTheDocument()
  })

  it('shows the terms defined on it, with their SQL', async () => {
    renderApp('/explore/tables/order_details')

    const terms = await section('Business terms')
    expect(within(terms).getByText('Revenue')).toBeInTheDocument()
    expect(terms).toHaveTextContent('sum(order_details.unit_price') // split by the colours
  })

  it('a suggested question fills a new conversation and sends nothing', async () => {
    const asks = recordAsks()
    const question = meaningOf('album')?.suggested_questions[0] as string
    const user = userEvent.setup()
    const { router } = renderApp('/explore/tables/album')

    await user.click(await screen.findByRole('button', { name: question }))

    const box = await screen.findByRole('textbox', { name: 'Your question' })
    await waitFor(() => expect(box).toHaveValue(question))
    await waitFor(() => expect(box).toHaveFocus())
    expect(router.state.location.pathname).toBe('/')
    expect(screen.queryByRole('article')).toBeNull()
    expect(asks).toEqual([])
  })

  it('then sending it starts a new conversation, not a follow-up of the last one', async () => {
    const asks = recordAsks()
    const question = meaningOf('album')?.suggested_questions[0] as string
    const user = userEvent.setup()
    const { router } = renderApp('/')

    // A question back: it names a conversation like an answer does, with no chart to draw first.
    await user.type(
      screen.getByRole('textbox', { name: 'Your question' }),
      'Which products are the most popular?',
    )
    await user.keyboard('{Control>}{Enter}{/Control}')
    await screen.findByText(clarification.clarifying_question)
    await waitFor(() => expect(router.state.location.pathname).toMatch(/^\/c\//))
    await router.navigate('/explore/tables/album')

    await user.click(await screen.findByRole('button', { name: question }))
    const box = await screen.findByRole('textbox', { name: 'Your question' })
    await waitFor(() => expect(box).toHaveValue(question))
    expect(screen.queryByRole('article')).toBeNull()
    await user.keyboard('{Control>}{Enter}{/Control}')

    await waitFor(() => expect(asks).toHaveLength(2))
    expect(asks[1]).toEqual({ question, conversation_id: null })
  })

  it('a reload does not fill the composer again', async () => {
    const question = meaningOf('album')?.suggested_questions[0] as string
    const user = userEvent.setup()
    const { router } = renderApp('/explore/tables/album')

    await user.click(await screen.findByRole('button', { name: question }))

    await waitFor(() => expect(router.state.location.state).toEqual({ keepFocus: true }))
  })

  it('Back from the filled composer returns to the table', async () => {
    const question = meaningOf('album')?.suggested_questions[0] as string
    const user = userEvent.setup()
    const { router } = renderApp('/explore/tables/album')
    await user.click(await screen.findByRole('button', { name: question }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/'))

    await router.navigate(-1)

    expect(await screen.findByRole('heading', { level: 1, name: 'album' })).toBeInTheDocument()
  })

  it('lists saved questions that used it, and they open their conversation', async () => {
    const id = crypto.randomUUID()
    recordTurn(
      {
        ...answer,
        question: 'Which albums sell best?',
        conversation_id: id,
        tables_used: ['album'],
      },
      [],
    )
    recordTurn(
      {
        ...answer,
        question: 'Something else',
        conversation_id: crypto.randomUUID(),
        tables_used: ['track'],
      },
      [],
    )
    const user = userEvent.setup()
    const { router } = renderApp('/explore/tables/album')

    const recent = await section('Recent questions that used it')
    expect(within(recent).queryByText('Something else')).toBeNull()
    await user.click(within(recent).getByRole('link', { name: 'Which albums sell best?' }))

    await waitFor(() => expect(router.state.location.pathname).toBe(`/c/${id}`))
  })

  it('says when no saved question has used it', async () => {
    renderApp('/explore/tables/album')

    expect(
      within(await section('Recent questions that used it')).getByText(/No saved question/),
    ).toBeInTheDocument()
  })

  it('with history off that section is not there, and the page is fine', async () => {
    server.use(
      http.get('/api/catalog/tables/:table/questions', () => apiError('history_unavailable')),
    )
    renderApp('/explore/tables/album')

    await section('Questions to ask')
    await waitFor(() =>
      expect(screen.queryByRole('region', { name: 'Recent questions that used it' })).toBeNull(),
    )
    expect(screen.getByRole('heading', { level: 1, name: 'album' })).toBeInTheDocument()
  })

  it('links back to the explorer with this table chosen', async () => {
    renderApp('/explore/tables/album')

    expect(await screen.findByRole('link', { name: 'All tables' })).toHaveAttribute(
      'href',
      '/explore?table=album',
    )
  })

  it('a table that does not exist is the 404 page', async () => {
    renderApp('/explore/tables/nope')

    expect(await screen.findByRole('heading', { level: 1, name: 'Page not found' })).toBeVisible()
  })

  it('with the database down it says so, not "not found"', async () => {
    server.use(http.get('/api/catalog', () => apiError('db_unavailable')))
    renderApp('/explore/tables/album')

    expect(await screen.findByText(/can't be listed right now/)).toBeInTheDocument()
    expect(screen.queryByRole('heading', { name: 'Page not found' })).toBeNull()
  })
})
