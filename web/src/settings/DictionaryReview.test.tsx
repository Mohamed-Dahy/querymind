import { screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { describe, expect, it } from 'vitest'

import {
  onboardingKey,
  type DataDictionary,
  type DictionaryEdit,
  type OnboardingInfo,
} from '@/api/onboarding'
import { setSession } from '@/api/session'
import type { ConnectionInfo, SaveConnectionRequest } from '@/api/settings'
import { CONNECTIONS_URL } from '@/mocks/handlers'
import { mockDictionary, pollMockOnboarding, startMockOnboarding } from '@/mocks/onboarding'
import { server } from '@/mocks/server'
import { MOCK_ROLE_KEY, mockSession } from '@/mocks/session'
import { saveMockConnection, saveMockKey } from '@/mocks/settings'
import { renderApp } from '@/test/render'

const SALES: SaveConnectionRequest = {
  name: 'Sales',
  host: 'db.example.com',
  port: 5432,
  dbname: 'sales',
  user: 'querymind_reader',
  password: 'secret password',
  sslmode: 'verify-full',
}

const DICTIONARY_URL = `${CONNECTIONS_URL}/:id/dictionary`

/** A saved database, onboarded in the fake API: the demo's Northwind descriptions, none reviewed. */
function onboarded({ run = true } = {}): ConnectionInfo {
  saveMockKey({ provider: 'groq', model: 'openai/gpt-oss-120b', api_key: 'gsk_test_1234' })
  const sales = saveMockConnection(SALES)!
  if (run) {
    startMockOnboarding(sales)
    for (let poll = 0; poll < 3; poll++) pollMockOnboarding(sales)
  }
  return sales
}

async function openReview(sales: ConnectionInfo, table?: string) {
  const query = table ? `?table=${table}` : ''
  const rendered = renderApp(`/settings/databases/${sales.id}/dictionary${query}`)
  await screen.findByRole('heading', { level: 2, name: 'Descriptions of Sales' })
  return rendered
}

/** Answer GET …/dictionary with `dictionary`, and record each PUT's body (answered as saved). */
function serve(dictionary: DataDictionary) {
  const sent: DictionaryEdit[] = []
  server.use(
    http.get(DICTIONARY_URL, () => HttpResponse.json(dictionary)),
    http.put(DICTIONARY_URL, async ({ request }) => {
      const edit = (await request.json()) as DictionaryEdit
      sent.push(edit)
      const tables = { ...dictionary.tables }
      for (const [name, change] of Object.entries(edit.tables)) {
        tables[name] = {
          reviewed: change.reviewed ?? false,
          description: change.description ?? '',
          columns: { ...tables[name]!.columns, ...change.columns },
          pii: change.pii ?? tables[name]!.pii ?? null,
        }
      }
      return HttpResponse.json({ ...dictionary, tables })
    }),
  )
  return sent
}

const columnNames = (form: HTMLElement) =>
  within(within(form).getByRole('group', { name: 'Columns' }))
    .getAllByRole('listitem')
    .map((row) => row.querySelector('label')?.textContent)

describe('Settings → a database’s descriptions', () => {
  it('lists the tables, opens the first, with its columns in the table’s own order', async () => {
    const sales = onboarded()
    await openReview(sales)

    const tables = await screen.findByRole('navigation', { name: 'Tables' })
    const links = within(tables).getAllByRole('link')
    expect(links[0]).toHaveAccessibleName('categories')
    expect(links[0]).toHaveAttribute('aria-current', 'page')
    expect(links.map((link) => link.textContent)).not.toContain('us_states') // skipped by the run
    expect(screen.getByText(/0 of 13 tables reviewed · drafted by groq/)).toBeVisible()

    const form = await screen.findByRole('form', { name: 'categories' })
    expect(within(form).getByLabelText('What one row is')).toHaveValue(
      'Each row represents a product category, used to classify items sold in the business.',
    )
    expect(columnNames(form)).toEqual(['category_id', 'category_name', 'description', 'picture'])
    expect(within(form).getByText('bytea')).toBeVisible()
    await waitFor(() => expect(document.title).toBe('Descriptions · Settings · QueryMind'))
  })

  it('orders columns as the table does, not as the dictionary is stored (JSON key order)', async () => {
    const sales = onboarded()
    const stored = mockDictionary(sales.id)!
    const categories = stored.tables['categories']!
    const reversed = Object.fromEntries(Object.entries(categories.columns).reverse())
    serve({ ...stored, tables: { categories: { ...categories, columns: reversed } } })
    await openReview(sales)

    const form = await screen.findByRole('form', { name: 'categories' })
    expect(columnNames(form)).toEqual(['category_id', 'category_name', 'description', 'picture'])
  })

  it('saving one column keeps the description and the mark, and sends no personal-data flags', async () => {
    const user = userEvent.setup()
    const sales = onboarded()
    // Reviewed, and drafted before 5.4: not flagged yet (pii null), so names are guessed from.
    const sent = serve({
      database: 'Sales',
      generated_by: 'groq',
      tables: {
        customers: {
          reviewed: true,
          description: 'One row per customer.',
          columns: { customer_id: 'Its id', city: 'Where', phone: 'How to call' },
          pii: null,
        },
      },
    })
    await openReview(sales)

    const form = await screen.findByRole('form', { name: 'customers' })
    const city = within(form).getByLabelText('city')
    await user.clear(city)
    await user.type(city, 'Their city')
    await user.click(within(form).getByRole('button', { name: 'Save' }))

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]).toEqual({
      tables: {
        customers: {
          description: 'One row per customer.',
          columns: { customer_id: 'Its id', city: 'Their city', phone: 'How to call' },
          reviewed: true,
        },
      },
    })
    expect(await screen.findByText('customers: saved.')).toBeVisible()
  })

  it('personal-data boxes start from what is masked now; ticking one sends the whole list', async () => {
    const user = userEvent.setup()
    const sales = onboarded()
    const stored = mockDictionary(sales.id)!
    // Not flagged yet: the boxes show the server's flags (the explorer's), not an empty list.
    const sent = serve({
      ...stored,
      tables: { shippers: { ...stored.tables['shippers']!, pii: null } },
    })
    await openReview(sales)

    const form = await screen.findByRole('form', { name: 'shippers' })
    const phone = within(form).getByRole('checkbox', { name: 'Personal data (phone)' })
    await waitFor(() => expect(phone).toBeChecked())
    await user.click(within(form).getByRole('checkbox', { name: 'Personal data (company_name)' }))
    await user.click(within(form).getByRole('button', { name: 'Save and mark reviewed' }))

    await waitFor(() => expect(sent).toHaveLength(1))
    expect(sent[0]!.tables['shippers']!.pii).toEqual(['phone', 'company_name'])
    expect(sent[0]!.tables['shippers']!.reviewed).toBe(true)
  })

  it('keeps a draft per table while another is open, marks it, and can discard it', async () => {
    const user = userEvent.setup()
    const sales = onboarded()
    await openReview(sales)

    const form = await screen.findByRole('form', { name: 'categories' })
    await user.type(within(form).getByLabelText('category_name'), ' (edited)')
    const tables = screen.getByRole('navigation', { name: 'Tables' })
    expect(within(tables).getByRole('link', { name: 'categories, unsaved changes' })).toBeVisible()

    await user.click(within(tables).getByRole('link', { name: 'customers' }))
    await screen.findByRole('form', { name: 'customers' })
    await user.click(within(tables).getByRole('link', { name: 'categories, unsaved changes' }))
    const again = await screen.findByRole('form', { name: 'categories' })
    expect(within(again).getByLabelText('category_name')).toHaveValue(
      'Name of the product category. (edited)',
    )

    await user.click(within(again).getByRole('button', { name: 'Discard changes' }))
    expect(within(again).getByLabelText('category_name')).toHaveValue(
      'Name of the product category.',
    )
    expect(within(tables).getByRole('link', { name: 'categories' })).toBeVisible()
  })

  it('Save and mark reviewed: saved, marked in the list, and focus on the table’s name', async () => {
    const user = userEvent.setup()
    const sales = onboarded()
    await openReview(sales)

    const form = await screen.findByRole('form', { name: 'categories' })
    await user.click(within(form).getByRole('button', { name: 'Save and mark reviewed' }))

    expect(await screen.findByText('categories: saved and marked reviewed.')).toBeVisible()
    expect(within(form).getByRole('heading', { level: 3, name: 'categories' })).toHaveFocus()
    expect(within(form).getByText('Reviewed')).toBeVisible()
    expect(within(form).queryByRole('button', { name: 'Save and mark reviewed' })).toBeNull()
    const tables = screen.getByRole('navigation', { name: 'Tables' })
    expect(within(tables).getByRole('link', { name: 'categories, reviewed' })).toBeVisible()
    expect(mockDictionary(sales.id)!.tables['categories']!.reviewed).toBe(true)
  })

  it('leaving with unsaved changes asks first: Stay keeps them, Leave goes', async () => {
    const user = userEvent.setup()
    const sales = onboarded()
    const { router } = await openReview(sales)

    const form = await screen.findByRole('form', { name: 'categories' })
    await user.type(within(form).getByLabelText('What one row is'), ' More.')
    await user.click(screen.getByRole('link', { name: 'Back to databases' }))

    const dialog = await screen.findByRole('dialog', { name: 'Leave without saving?' })
    expect(dialog).toHaveTextContent('Your changes to categories aren’t saved.')
    await user.click(within(dialog).getByRole('button', { name: 'Stay' }))
    expect(router.state.location.pathname).toBe(`/settings/databases/${sales.id}/dictionary`)
    expect(within(form).getByLabelText('What one row is')).toHaveValue(
      'Each row represents a product category, used to classify items sold in the business. More.',
    )

    await user.click(screen.getByRole('link', { name: 'Back to databases' }))
    const asked = await screen.findByRole('dialog', { name: 'Leave without saving?' })
    await user.click(within(asked).getByRole('button', { name: 'Leave' }))
    await waitFor(() => expect(router.state.location.pathname).toBe('/settings/databases'))
  })

  it('a refused save says why, next to the buttons', async () => {
    const user = userEvent.setup()
    const sales = onboarded()
    server.use(
      http.put(DICTIONARY_URL, () =>
        HttpResponse.json(
          { error: 'dictionary_invalid', message: "Table 'categories' has no column 'icon'." },
          { status: 422 },
        ),
      ),
    )
    await openReview(sales)

    const form = await screen.findByRole('form', { name: 'categories' })
    await user.click(within(form).getByRole('button', { name: 'Save and mark reviewed' }))
    const alert = await within(form).findByRole('alert')
    expect(alert).toHaveTextContent("That change doesn't fit the dictionary.")
    expect(alert).toHaveTextContent("Table 'categories' has no column 'icon'.")
  })

  it('while onboarding runs, the descriptions are read only, with a note', async () => {
    const sales = onboarded()
    const running: OnboardingInfo = {
      status: 'drafting',
      tables_total: 3,
      tables_drafted: 1,
      tables_skipped: 0,
      error: null,
      generated_by: '',
      has_dictionary: true,
      started_at: '2026-11-16T10:00:00Z',
      updated_at: '2026-11-16T10:00:00Z',
    }
    server.use(http.get(`${CONNECTIONS_URL}/:id/onboarding`, () => HttpResponse.json(running)))
    await openReview(sales)

    expect(
      await screen.findByText(/Onboarding is running: the descriptions can be edited/),
    ).toBeVisible()
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    expect(screen.getByRole('region', { name: 'categories' })).toHaveTextContent(
      'Each row represents a product category',
    )
  })

  it('a viewer reads the descriptions and the personal-data marks, without a form', async () => {
    localStorage.setItem(MOCK_ROLE_KEY, 'viewer')
    setSession({ ...mockSession, user: { ...mockSession.user, role: 'viewer' } })
    const sales = onboarded()
    await openReview(sales, 'shippers')

    const table = await screen.findByRole('region', { name: 'shippers' })
    expect(screen.queryByRole('form')).not.toBeInTheDocument()
    const phone = within(table).getByText('phone').closest('dt')!
    expect(phone).toHaveTextContent('Personal data')
  })

  it('opened during a first run, it shows the descriptions as soon as the run ends', async () => {
    const sales = onboarded({ run: false })
    startMockOnboarding(sales) // queued; each poll below moves it one stage
    const { client } = await openReview(sales)

    expect(await screen.findByText(/Sales has no descriptions yet/)).toBeVisible()
    // The page polls the run; ask now instead of waiting for the timer.
    for (let poll = 0; poll < 3; poll++) {
      await client.refetchQueries({ queryKey: onboardingKey(sales.id) })
    }
    expect(await screen.findByRole('form', { name: 'categories' })).toBeVisible()
  })

  it('a database not onboarded yet says so, and where to onboard it', async () => {
    const sales = onboarded({ run: false })
    await openReview(sales)

    expect(await screen.findByText(/Sales has no descriptions yet/)).toBeVisible()
    expect(screen.getByRole('link', { name: 'Onboard it on the Databases tab' })).toHaveAttribute(
      'href',
      '/settings/databases',
    )
  })

  it('a database this workspace doesn’t have says so', async () => {
    renderApp('/settings/databases/9a7c1e2f-0000-4000-8000-00000000dead/dictionary')

    expect(await screen.findByText(/This workspace has no such database/)).toBeVisible()
  })
})
