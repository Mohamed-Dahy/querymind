import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it, vi } from 'vitest'

import type { AskRequest } from '@/api/client'
import { semantic } from '@/mocks/fixtures'
import { apiError, STREAM_URL } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

import { metricQuestion, termName } from './data'

const metrics = semantic.businesses.flatMap(({ metrics }) => metrics)
const NAME = /^Search terms/ // the history rail has a search box too
const search = () => screen.findByRole('searchbox', { name: NAME })

describe('GlossaryPage', () => {
  it('shows every term in the semantic layer, under its business', async () => {
    renderApp('/explore/glossary')
    await search()

    for (const { database, metrics: inBusiness } of semantic.businesses) {
      const business = screen.getByRole('region', { name: new RegExp(database, 'i') })
      for (const { name } of inBusiness) {
        expect(within(business).getByRole('listitem', { name: termName(name) })).toBeVisible()
      }
    }
    expect(screen.getByText(`${metrics.length} terms`)).toBeInTheDocument()
  })

  it('a term has its meaning, kind, exact SQL, other words and table', async () => {
    renderApp('/explore/glossary')

    const revenue = await screen.findByRole('listitem', { name: 'Revenue' })

    const metric = metrics.find(({ name }) => name === 'revenue')
    expect(revenue).toHaveTextContent(metric?.description ?? '')
    expect(revenue).toHaveTextContent('a measure')
    expect(revenue).toHaveTextContent(metric?.sql ?? '')
    expect(revenue).toHaveTextContent('turnover')
    expect(within(revenue).getByRole('link', { name: 'order_details' })).toHaveAttribute(
      'href',
      '/explore/tables/order_details',
    )
    expect(await screen.findByRole('listitem', { name: 'Late shipment' })).toHaveTextContent(
      'a condition',
    )
  })

  it.each([
    ['another word for it', 'turnover', 'Revenue'],
    ['its name', 'late_ship', 'Late shipment'],
    ['its name in plain words', 'late ship', 'Late shipment'],
    ['its meaning', 'purchases', 'Big spender'],
    ['a synonym, in any case', 'AOV', 'Average order value'],
  ])('finds a term by %s', async (_, words, expected) => {
    const user = userEvent.setup()
    renderApp('/explore/glossary')

    await user.type(await search(), words)

    expect(screen.getByRole('listitem', { name: expected })).toBeVisible()
    expect(screen.queryByRole('listitem', { name: 'Long track' })).toBeNull()
    expect(screen.getByText(new RegExp(`of ${metrics.length} terms`))).toBeInTheDocument()
  })

  it('a business with nothing found goes away, and nothing found says so', async () => {
    const user = userEvent.setup()
    renderApp('/explore/glossary')

    await user.type(await search(), 'turnover')
    expect(screen.queryByRole('region', { name: 'Chinook' })).toBeNull()

    await user.clear(screen.getByRole('searchbox', { name: NAME }))
    await user.type(screen.getByRole('searchbox', { name: NAME }), 'zebra')
    expect(screen.getByText('No term matches.')).toBeInTheDocument()
  })

  it('"Ask with this term" fills a new conversation, and sends nothing', async () => {
    const asks: AskRequest[] = []
    server.use(
      http.post(STREAM_URL, async ({ request }) => {
        asks.push((await request.json()) as AskRequest)
        return HttpResponse.error()
      }),
    )
    const user = userEvent.setup()
    const { router } = renderApp('/explore/glossary')

    const revenue = await screen.findByRole('listitem', { name: 'Revenue' })
    const button = within(revenue).getByRole('button', { name: 'Ask with this term' })
    expect(button).toHaveAccessibleDescription(/^Revenue/)
    await user.click(button)

    const box = await screen.findByRole('textbox', { name: 'Your question' })
    await waitFor(() => expect(box).toHaveValue('What is our revenue?'))
    expect(router.state.location.pathname).toBe('/')
    expect(asks).toEqual([])
  })

  it('every term makes a question with its own words in it', () => {
    for (const metric of metrics) {
      expect(metricQuestion(metric).toLowerCase()).toContain(termName(metric.name).toLowerCase())
    }
  })

  it('works with the database down: it needs only the semantic layer', async () => {
    server.use(http.get('/api/catalog', () => apiError('db_unavailable')))
    renderApp('/explore/glossary')

    expect(await screen.findByRole('listitem', { name: 'Revenue' })).toBeVisible()
  })

  it('says so when the terms cannot be read, and Try again works', async () => {
    let broken = true
    server.use(
      http.get('/api/semantic', () =>
        broken ? apiError('internal_error') : HttpResponse.json(semantic),
      ),
    )
    const user = userEvent.setup()
    renderApp('/explore/glossary')

    expect(await screen.findByText(/business terms can't be shown/)).toBeInTheDocument()
    broken = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await screen.findByRole('listitem', { name: 'Revenue' })).toBeVisible()
  })

  it('a link to one term scrolls to it once it has loaded', async () => {
    const scroll = vi.fn<() => void>()
    window.HTMLElement.prototype.scrollIntoView = scroll
    renderApp('/explore/glossary#term-late_shipment')

    const term = await screen.findByRole('listitem', { name: 'Late shipment' })

    await waitFor(() => expect(scroll.mock.contexts).toContain(term))
    Reflect.deleteProperty(window.HTMLElement.prototype, 'scrollIntoView')
  })

  it('the explorer and a table page lead here', async () => {
    const user = userEvent.setup()
    renderApp('/explore/tables/orders')

    await user.click(await screen.findByRole('link', { name: 'Late shipment in the glossary' }))

    expect(
      await screen.findByRole('heading', { level: 1, name: 'Business glossary' }),
    ).toBeVisible()
  })
})
