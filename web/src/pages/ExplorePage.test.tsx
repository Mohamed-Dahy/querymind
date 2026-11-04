import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import { catalog, semantic } from '@/mocks/fixtures'
import { apiError } from '@/mocks/handlers'
import { server } from '@/mocks/server'
import { renderApp } from '@/test/render'

// jsdom's stand-in for matchMedia never matches (test/setup.ts), so these see what a phone sees: the
// list. The graph is checked in a real browser (e2e/explorer.spec.ts).
const list = () => screen.findByRole('region', { name: 'List of tables' })
const row = (name: string | RegExp) => screen.findByRole('button', { name })

describe('ExplorePage', () => {
  it('lists every table under its business', async () => {
    renderApp('/explore')

    const tables = await list()

    expect(within(tables).getByText(`${catalog.tables.length} tables`)).toBeInTheDocument()
    const chinook = within(tables).getByRole('region', { name: 'Chinook' })
    const northwind = within(tables).getByRole('region', { name: 'Northwind' })
    expect(within(chinook).getByRole('button', { name: /^album/ })).toBeInTheDocument()
    expect(within(northwind).getByRole('button', { name: /^order_details/ })).toBeInTheDocument()
  })

  it('shows row counts, and marks tables with personal data in words', async () => {
    renderApp('/explore')

    const customers = await row(/^customers/)
    expect(customers).toHaveTextContent(/91 rows/)
    expect(customers).toHaveTextContent('PII')
    expect(await row(/^region/)).not.toHaveTextContent('PII')
  })

  it('says when a row count is unknown', async () => {
    server.use(
      http.get('/api/catalog', () =>
        HttpResponse.json({
          tables: catalog.tables.map((table) => ({ ...table, row_count: null })),
        }),
      ),
    )
    renderApp('/explore')

    expect(await row(/^region/)).toHaveTextContent('rows unknown')
  })

  it.each([
    ['its name', 'order_det', /^order_details/],
    ['another word for it', 'clients', /^customers/],
    ['one of its columns', 'ship_via', /^orders/],
  ])('finds a table by %s', async (_, words, expected) => {
    const user = userEvent.setup()
    renderApp('/explore')
    const tables = await list()

    await user.type(within(tables).getByRole('searchbox'), words)

    expect(within(tables).getByRole('button', { name: expected })).toBeInTheDocument()
    expect(within(tables).queryByRole('button', { name: /^region/ })).toBeNull()
  })

  it('says how many it found, and when nothing matches', async () => {
    const user = userEvent.setup()
    renderApp('/explore')
    const tables = await list()

    await user.type(within(tables).getByRole('searchbox'), 'zebra')

    expect(within(tables).getByText('No table matches.')).toBeInTheDocument()
    expect(within(tables).getByText(`0 of ${catalog.tables.length} tables`)).toBeInTheDocument()
  })

  it('choosing a table goes in the URL, and shows what it joins', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/explore')

    await user.click(await row(/^territories/))

    await waitFor(() => expect(router.state.location.search).toBe('?table=territories'))
    const chosen = await row(/^territories/)
    expect(chosen).toHaveAttribute('aria-pressed', 'true')
    expect(chosen).toHaveFocus()
    expect(screen.getByRole('button', { name: 'region' })).toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'employee_territories' })).toBeInTheDocument()
  })

  it('a chosen table links to its own page', async () => {
    renderApp('/explore?table=track')

    expect(await screen.findByRole('link', { name: 'Everything about track' })).toHaveAttribute(
      'href',
      '/explore/tables/track',
    )
  })

  it('follows a join from one table to the next, and Back returns', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/explore?table=territories')

    await user.click(await screen.findByRole('button', { name: 'region' }))

    await waitFor(() => expect(router.state.location.search).toBe('?table=region'))
    await router.navigate(-1)
    await waitFor(() => expect(router.state.location.search).toBe('?table=territories'))
  })

  it('choosing it again lets go of it', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/explore?table=region')

    await user.click(await row(/^region/))

    await waitFor(() => expect(router.state.location.search).toBe(''))
  })

  it('a table with no joins says so', async () => {
    renderApp('/explore?table=us_states')

    expect(await screen.findByText('This table joins no other table.')).toBeInTheDocument()
  })

  it('a table name that does not exist in the URL chooses nothing', async () => {
    renderApp('/explore?table=nope')

    const tables = await list()
    expect(within(tables).queryByRole('button', { pressed: true })).toBeNull()
  })

  it('marks the tables an answer used and was shown, in words', async () => {
    renderApp('/explore?used=orders,customers&shown=region')

    expect(await row(/^orders/)).toHaveTextContent('used')
    expect(await row(/^customers/)).toHaveTextContent('used')
    expect(await row(/^region/)).toHaveTextContent('shown')
    expect(await row(/^track/)).not.toHaveTextContent(/used|shown/)
    expect(screen.getByText(/One answer's tables/)).toBeInTheDocument()
  })

  it('ignores a name in the link that is not a table, and a table in both lists is used', async () => {
    renderApp('/explore?used=orders,nope&shown=orders')

    expect(await row(/^orders/)).toHaveTextContent('used')
    expect(await row(/^orders/)).not.toHaveTextContent('shown')
  })

  it('a link with nothing real in it marks nothing', async () => {
    renderApp('/explore?used=nope')

    await list()
    expect(screen.queryByText(/One answer's tables/)).toBeNull()
  })

  it('choosing a table keeps the marks, and Clear removes everything', async () => {
    const user = userEvent.setup()
    const { router } = renderApp('/explore?used=orders')

    await user.click(await row(/^region/))
    await waitFor(() => expect(router.state.location.search).toContain('table=region'))
    expect(router.state.location.search).toContain('used=orders')

    await user.click(screen.getByRole('button', { name: 'Clear' }))
    await waitFor(() => expect(router.state.location.search).toBe(''))
    expect(screen.queryByText(/One answer's tables/)).toBeNull()
  })

  it('with the database down it says so, and Try again brings the tables back', async () => {
    let down = true
    server.use(
      http.get('/api/catalog', () =>
        down ? apiError('db_unavailable') : HttpResponse.json(catalog),
      ),
    )
    const user = userEvent.setup()
    renderApp('/explore')

    await screen.findByText(/can't be listed right now/)
    down = false
    await user.click(screen.getByRole('button', { name: 'Try again' }))

    expect(await list()).toBeInTheDocument()
  })

  it('needs the meanings too: no list without them, or PII badges would be wrong', async () => {
    server.use(http.get('/api/semantic', () => apiError('internal_error')))
    renderApp('/explore')

    await screen.findByText(/can't be listed right now/)
    expect(screen.queryByRole('region', { name: 'List of tables' })).toBeNull()
    expect(semantic.businesses.length).toBeGreaterThan(0)
  })
})
