import { render, screen, within } from '@testing-library/react'
import { MemoryRouter } from 'react-router'
import { describe, expect, it, vi } from 'vitest'

import type { AskResponse } from '@/api/client'
import { answer, followup, repair } from '@/mocks/fixtures'

import { ShowYourWork } from './ShowYourWork'
import { triesOf } from './work'

function renderWork(reply: AskResponse) {
  const onOpenChange = vi.fn<(open: boolean) => void>()
  render(
    <MemoryRouter>
      <ShowYourWork reply={reply} open onOpenChange={onOpenChange} opener={{ current: null }} />
    </MemoryRouter>,
  )
  return screen.getByRole('dialog', { name: 'How this answer was made' })
}

const section = (dialog: HTMLElement, name: string | RegExp) => {
  const heading = within(dialog).getByRole('heading', { level: 3, name })
  return heading.closest('section') as HTMLElement
}

describe('ShowYourWork on the recorded repair', () => {
  it('shows every try: the failure and why, then what changed, then which was used', () => {
    const dialog = renderWork(repair)

    const tries = within(section(dialog, 'The SQL, 2 tries')).getAllByRole('listitem')
    expect(tries).toHaveLength(2)
    expect(tries[0]).toHaveTextContent(/^Try 1/)
    expect(tries[0]).toHaveTextContent('GROUP BY region')
    expect(tries[0]).toHaveTextContent('The database rejected it.')
    expect(tries[0]).toHaveTextContent(/must appear in the GROUP BY clause/)
    expect(tries[1]).toHaveTextContent(/^Try 2 · used for the answer/)
    expect(tries[1]?.querySelector('del')).toHaveTextContent('removed: region')
    expect(tries[1]?.querySelector('ins')).toHaveTextContent(/^added: /)
  })

  it('shows how long it took and how many queries ran', () => {
    const dialog = renderWork(repair)

    expect(within(dialog).getByText(/^Took .* · 2 SQL queries run$/)).toBeInTheDocument()
  })

  it('says why the confidence is lower', () => {
    const dialog = renderWork(repair)

    expect(section(dialog, 'Confidence: medium')).toHaveTextContent(
      'It needed 1 repair to get this result.',
    )
  })
})

describe('ShowYourWork on recorded answers', () => {
  it('says how many tables were picked, and which', () => {
    const dialog = renderWork(answer)
    const tables = section(dialog, 'Tables shown to the AI')
    const { schema_selection: selection } = answer

    expect(tables).toHaveTextContent(
      `Retrieved ${selection?.tables.length} of ${selection?.total_tables} tables (hybrid search).`,
    )
    expect(
      within(tables)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(selection?.tables)
  })

  it('one try has no diff, and is the one used', () => {
    const dialog = renderWork(answer)
    const sql = section(dialog, 'The SQL')

    expect(within(sql).getByRole('listitem')).toHaveTextContent(/^Try 1 · used for the answer/)
    expect(sql.querySelector('del, ins')).toBeNull()
    expect(sql).toHaveTextContent(answer.sql)
  })

  it('lists the similar solved questions', () => {
    const dialog = renderWork(answer)

    expect(answer.example_questions.length).toBeGreaterThan(0)
    for (const question of answer.example_questions) {
      expect(within(dialog).getByText(question)).toBeInTheDocument()
    }
  })

  it('shows what a follow-up was searched as', () => {
    const dialog = renderWork({ ...followup, search_question: 'Total sales per category in 1997?' })

    expect(section(dialog, 'Searched as')).toHaveTextContent('Total sales per category in 1997?')
  })

  it('high confidence says so plainly', () => {
    const dialog = renderWork({ ...answer, confidence: 'high', confidence_reasons: [] })

    expect(section(dialog, 'Confidence: high')).toHaveTextContent('No sign of a problem.')
  })
})

describe('ShowYourWork with less to show', () => {
  it('a reply without the new fields leaves those parts out', () => {
    const dialog = renderWork({
      ...answer,
      schema_selection: null,
      search_question: null,
      example_questions: [],
      sql_attempts: null,
      duration_ms: null,
    })

    expect(within(dialog).queryByText(/^Took /)).not.toBeInTheDocument()
    expect(within(dialog).queryByRole('heading', { name: 'Tables shown to the AI' })).toBeNull()
    expect(within(dialog).queryByRole('heading', { name: 'Searched as' })).toBeNull()
    expect(within(dialog).queryByRole('heading', { name: /Similar solved/ })).toBeNull()
    expect(section(dialog, 'The SQL')).toHaveTextContent(answer.sql)
  })

  it.each([
    ['full', 'The whole schema, 25 tables.'],
    ['fallback', "The whole schema, 25 tables: the search couldn't narrow it down."],
  ] as const)('explains the %s schema', (mode, text) => {
    const dialog = renderWork({
      ...answer,
      schema_selection: { mode, tables: ['orders'], total_tables: 25, search: '' },
    })

    expect(section(dialog, 'Tables shown to the AI')).toHaveTextContent(text)
  })

  it('shows warnings when there are some', () => {
    const dialog = renderWork({
      ...answer,
      warnings: [{ code: 'empty', message: 'No rows came back.' }],
    })

    expect(section(dialog, 'Warnings')).toHaveTextContent('No rows came back.')
  })
})

describe('triesOf', () => {
  const failed = { sql: 'SELECT 1', kind: 'db_error', message: 'No.' } as const

  it('the answer comes after the failed tries', () => {
    const tries = triesOf({ ...answer, attempts: [failed], sql: 'SELECT 2' })

    expect(tries.map((one) => [one.sql, one.used])).toEqual([
      ['SELECT 1', false],
      ['SELECT 2', true],
    ])
  })

  it('a suspicious result that was kept in the end is listed once, as the one used', () => {
    const suspicious = { sql: 'SELECT 2', kind: 'suspicious', message: 'Empty.' } as const

    const tries = triesOf({ ...answer, attempts: [suspicious], sql: 'SELECT 2' })

    expect(tries).toEqual([
      { sql: 'SELECT 2', failed: { kind: 'suspicious', message: 'Empty.' }, used: true },
    ])
  })

  it('a suspicious result that was repaired is not the one used', () => {
    const suspicious = { sql: 'SELECT 2', kind: 'suspicious', message: 'Empty.' } as const

    const tries = triesOf({ ...answer, attempts: [suspicious], sql: 'SELECT 3' })

    expect(tries.map((one) => one.used)).toEqual([false, true])
  })
})

describe('ShowYourWork and the explorer', () => {
  const seeTables = (dialog: HTMLElement) =>
    within(dialog).queryByRole('link', { name: 'See these tables' })
  const paramsOf = (link: HTMLElement | null) =>
    new URL(link?.getAttribute('href') ?? '', 'http://x').searchParams

  it('lists the tables the SQL read, and links to them, with the shown ones apart', () => {
    const reply: AskResponse = {
      ...answer,
      tables_used: ['orders'],
      schema_selection: {
        mode: 'retrieved',
        tables: ['orders', 'customers'],
        total_tables: 25,
        search: 'hybrid',
      },
    }
    const dialog = renderWork(reply)

    const read = within(dialog).getByRole('list', { name: 'Tables read' })
    expect(within(read).getByText('orders')).toBeInTheDocument()
    const params = paramsOf(seeTables(dialog))
    expect(params.get('used')).toBe('orders')
    expect(params.get('shown')).toBe('customers')
  })

  it('the whole schema shown says nothing, so it is left out', () => {
    const reply: AskResponse = {
      ...answer,
      tables_used: ['orders'],
      schema_selection: {
        mode: 'full',
        tables: ['orders', 'customers'],
        total_tables: 25,
        search: '',
      },
    }

    expect(paramsOf(seeTables(renderWork(reply))).get('shown')).toBeNull()
  })

  it('an answer saved before this existed says so, and still shows what it was shown', () => {
    const reply: AskResponse = {
      ...answer,
      tables_used: [],
      schema_selection: { mode: 'retrieved', tables: ['orders'], total_tables: 25, search: '' },
    }
    const dialog = renderWork(reply)

    expect(within(dialog).getByText(/saved before QueryMind kept this/)).toBeInTheDocument()
    expect(paramsOf(seeTables(dialog)).get('shown')).toBe('orders')
  })

  it('with nothing to show there is no link', () => {
    const reply: AskResponse = { ...answer, tables_used: [], schema_selection: null }

    expect(seeTables(renderWork(reply))).toBeNull()
  })
})
