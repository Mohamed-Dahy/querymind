import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { http, HttpResponse } from 'msw'
import { MemoryRouter } from 'react-router'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AskResponse } from '@/api/client'
import type { RerunResult } from '@/api/conversations'
import { Toaster } from '@/components/ui/sonner'
import * as csv from '@/lib/csv'
import { answer, monthly, single } from '@/mocks/fixtures'
import { server } from '@/mocks/server'
import { renderWithQueryClient } from '@/test/render'

import { AnswerCell } from './AnswerCell'

// Variants of the recorded answer, built by spreading it: still the API's type, never hand-copied.
function renderAnswer(changes: Partial<AskResponse> = {}) {
  const user = userEvent.setup()
  render(
    <MemoryRouter>
      <AnswerCell reply={{ ...answer, ...changes }} />
      <Toaster />
    </MemoryRouter>,
  )
  return user
}

afterEach(() => {
  vi.restoreAllMocks()
})

/** The recorded answer opens on its chart; these tests look at the table. */
async function openTable(user: ReturnType<typeof userEvent.setup>) {
  await user.click(screen.getByRole('tab', { name: 'Table' }))
}

describe('AnswerCell', () => {
  it('says which database the answer came from, in words', () => {
    renderAnswer()

    const line = screen.getByText(/^From/)
    expect(line).toHaveTextContent('From northwind: Our food trading business')
  })

  it('names both databases when one query read both', () => {
    renderAnswer({
      sources: [
        { database: 'northwind', description: 'Our food trading business.' },
        { database: 'chinook', description: 'Our online music store.' },
      ],
    })

    expect(screen.getByText(/^From/)).toHaveTextContent(
      'From northwind and chinook: Our food trading business. Our online music store.',
    )
  })

  it('says nothing about a database when no known table was read', () => {
    renderAnswer({ sources: [] })

    expect(screen.queryByText(/^From/)).not.toBeInTheDocument()
  })

  it('shows the answer, high confidence, the row count and the table', async () => {
    await openTable(renderAnswer())

    expect(screen.getByText(answer.answer)).toBeInTheDocument()
    expect(screen.getByRole('meter', { name: 'Confidence' })).toHaveAttribute(
      'aria-valuetext',
      'High',
    )
    expect(screen.getByText('High confidence')).toBeInTheDocument()
    expect(screen.getByText('8 rows')).toBeInTheDocument()
    const table = screen.getByRole('table')
    expect(within(table).getAllByRole('row')).toHaveLength(9)
    expect(within(table).getByRole('columnheader', { name: 'total_sales' })).toBeInTheDocument()
    // No badge or warnings on a plain answer.
    expect(screen.queryByText('Answer from a template')).not.toBeInTheDocument()
    expect(screen.queryByRole('list', { name: 'Warnings' })).not.toBeInTheDocument()
  })

  it.each([
    ['medium', 'Medium', 2],
    ['low', 'Low', 1],
  ] as const)('shows %s confidence with its reasons', (confidence, label, filled) => {
    renderAnswer({ confidence, confidence_reasons: ['It needed 1 repair to get this result.'] })

    const meter = screen.getByRole('meter', { name: 'Confidence' })
    expect(meter).toHaveAttribute('aria-valuetext', label)
    expect(meter).toHaveAttribute('aria-valuenow', String(filled))
    expect(
      within(screen.getByRole('list', { name: 'Why the confidence is lower' })).getByText(
        'It needed 1 repair to get this result.',
      ),
    ).toBeInTheDocument()
  })

  it('shows each warning with its text', () => {
    renderAnswer({
      warnings: [
        { code: 'duplicate_rows', message: 'Some rows appear more than once.' },
        { code: 'empty_result', message: 'The query returned no rows.' },
      ],
    })

    const warnings = screen.getByRole('list', { name: 'Warnings' })
    expect(
      within(warnings)
        .getAllByRole('listitem')
        .map((item) => item.textContent),
    ).toEqual(['Some rows appear more than once.', 'The query returned no rows.'])
  })

  it('says when only the first rows are shown', async () => {
    await openTable(renderAnswer({ truncated: true, row_count: 1000 }))

    expect(
      screen.getByText(
        /Showing the first 1,000 rows: more rows matched than the row limit allows\./,
      ),
    ).toBeInTheDocument()
  })

  it('explains a template answer in a tooltip, by keyboard too', async () => {
    const user = renderAnswer({ answer_from_llm: false })

    await user.tab()
    expect(screen.getByRole('button', { name: 'Answer from a template' })).toHaveFocus()
    expect(await screen.findByRole('tooltip')).toHaveTextContent(/built directly from the rows/)
  })

  it('a result with no rows has no table and nothing to download', () => {
    renderAnswer({ columns: ['region'], rows: [], row_count: 0 })
    expect(screen.getByText('0 rows')).toBeInTheDocument()
    expect(screen.queryByRole('table')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: 'Download CSV' })).toBeDisabled()
  })

  it('formats numbers and shows null in the table', () => {
    renderAnswer({ columns: ['name', 'total'], rows: [[null, 1234.5678]], row_count: 1 })

    const cells = within(screen.getByRole('table')).getAllByRole('cell')
    expect(cells[0]).toHaveTextContent('null')
    expect(cells[1]).toHaveTextContent(new Intl.NumberFormat().format(1234.57))
  })

  it('downloads the rows as CSV, named after the question', async () => {
    const download = vi.spyOn(csv, 'downloadCsv').mockImplementation(() => {})
    const user = renderAnswer()

    await openTable(user)
    await user.click(screen.getByRole('button', { name: 'Download CSV' }))

    expect(download).toHaveBeenCalledExactlyOnceWith(
      'what-is-the-total-sales-amount-per-category.csv',
      csv.toCsv(answer.columns, answer.rows),
    )
    // The browser's download shows nowhere in the page, so a toast says it happened.
    const toast = (
      await screen.findByText('Downloaded what-is-the-total-sales-amount-per-category.csv')
    ).closest('li') as HTMLElement
    expect(toast).toHaveTextContent(`${answer.rows.length} rows`)
  })

  it('shows the SQL coloured, with its explanation, on the SQL tab', async () => {
    const user = renderAnswer()

    await user.click(screen.getByRole('tab', { name: 'SQL' }))

    expect(screen.getByText(answer.explanation)).toBeInTheDocument()
    // Plain text at once, colours when Shiki has loaded.
    await waitFor(() =>
      expect(screen.getByText('SELECT')).toHaveStyle({ color: 'var(--shiki-token-keyword)' }),
    )
  })

  it('copies the SQL and says so', async () => {
    const user = renderAnswer()
    // user-event puts a fake clipboard on navigator; watch it.
    const writeText = vi.spyOn(navigator.clipboard, 'writeText')

    await user.click(screen.getByRole('tab', { name: 'SQL' }))
    await user.click(screen.getByRole('button', { name: 'Copy SQL' }))

    expect(writeText).toHaveBeenCalledExactlyOnceWith(answer.sql)
    expect(await screen.findByText('SQL copied.')).toBeInTheDocument()
  })

  it('says how to copy by hand when copying fails (no clipboard on plain HTTP, or refused)', async () => {
    const user = renderAnswer()
    vi.spyOn(navigator.clipboard, 'writeText').mockRejectedValue(new DOMException('Denied'))

    await user.click(screen.getByRole('tab', { name: 'SQL' }))
    await user.click(screen.getByRole('button', { name: 'Copy SQL' }))

    expect(
      await screen.findByText("Couldn't copy. Select the SQL and press Ctrl+C."),
    ).toBeInTheDocument()
  })

  it('opens on a bar chart for sales per category, described for screen readers', async () => {
    renderAnswer()

    const tabs = screen.getAllByRole('tab').map((tab) => tab.textContent)
    expect(tabs).toEqual(['Chart', 'Table', 'SQL'])
    expect(screen.getByRole('tab', { name: 'Chart' })).toHaveAttribute('aria-selected', 'true')
    // Recharts loads lazily; the figure is one image named by its summary.
    expect(
      await screen.findByRole('img', {
        name: /^Bar chart of total_sales by category_name, 8 bars\./,
      }),
    ).toBeInTheDocument()
  })

  it('opens on a line chart for the monthly sales', async () => {
    render(<AnswerCell reply={monthly} />)

    expect(
      await screen.findByRole('img', { name: /^Line chart of total_sales by month, 12 points/ }),
    ).toBeInTheDocument()
  })

  it('Show your work opens the drawer, and Escape gives the focus back', async () => {
    const user = renderAnswer()
    const button = screen.getByRole('button', { name: 'Show your work' })

    await user.click(button)

    const dialog = await screen.findByRole('dialog', { name: 'How this answer was made' })
    expect(within(dialog).getByText(answer.question)).toBeInTheDocument()

    await user.keyboard('{Escape}')

    await waitFor(() => expect(screen.queryByRole('dialog')).not.toBeInTheDocument())
    await waitFor(() => expect(button).toHaveFocus())
  })

  describe('a saved answer', () => {
    const conversation_id = '3f2a9c1e-0000-4000-8000-000000000000'
    const reply = { ...answer, rows: [], conversation_id }
    const fresh = {
      columns: answer.columns,
      rows: answer.rows,
      row_count: answer.rows.length,
      truncated: false,
      ran_at: '2026-11-03T09:00:00Z',
    }

    function renderSaved(onRerun = vi.fn<(result: RerunResult) => void>()) {
      const user = userEvent.setup()
      renderWithQueryClient(
        <AnswerCell
          reply={reply}
          saved={{ n: 1, createdAt: '2026-11-01T10:00:00Z' }}
          onRerun={onRerun}
        />,
      )
      return { user, onRerun }
    }

    it('says its rows are not stored, and has no chart until they are back', () => {
      renderSaved()

      expect(screen.queryByRole('tab', { name: 'Chart' })).not.toBeInTheDocument()
      expect(screen.getByText(/rows of this answer aren't stored/)).toBeInTheDocument()
      expect(screen.getByRole('button', { name: 'Re-run for rows' })).toBeInTheDocument()
      expect(screen.queryByRole('button', { name: 'Download CSV' })).not.toBeInTheDocument()
      expect(screen.getByText(answer.answer)).toBeInTheDocument()
    })

    it('a re-run asks only for that turn, and hands over the rows', async () => {
      let url = ''
      server.use(
        http.post('/api/conversations/:id/turns/:n/rerun', ({ request }) => {
          url = request.url
          return HttpResponse.json(fresh)
        }),
      )
      const { user, onRerun } = renderSaved()

      await user.click(screen.getByRole('button', { name: 'Re-run for rows' }))

      await waitFor(() => expect(onRerun).toHaveBeenCalledExactlyOnceWith(fresh))
      expect(url).toContain(`/conversations/${conversation_id}/turns/1/rerun`)
    })

    it('a failed re-run says why, and can be tried again', async () => {
      server.use(
        http.post('/api/conversations/:id/turns/:n/rerun', () =>
          HttpResponse.json(
            { error: 'unsafe_sql', message: 'Only SELECT queries are allowed.' },
            { status: 422 },
          ),
        ),
      )
      const { user } = renderSaved()

      await user.click(screen.getByRole('button', { name: 'Re-run for rows' }))

      expect(await screen.findByRole('alert')).toHaveTextContent("I couldn't answer that safely.")
      expect(screen.getByRole('button', { name: 'Re-run for rows' })).toHaveAttribute(
        'aria-disabled',
        'false',
      )
    })

    it('with fresh rows: the chart, the table, the CSV, and a note about the dates', async () => {
      renderWithQueryClient(
        <AnswerCell
          reply={{ ...reply, rows: answer.rows }}
          saved={{ n: 1, createdAt: '2026-11-01T10:00:00Z', freshAt: '2026-11-03T09:00:00Z' }}
        />,
      )

      expect(screen.getByText(/^Fresh rows from .*so they may not match\.$/)).toBeInTheDocument()
      expect(screen.getByRole('tab', { name: 'Chart' })).toHaveAttribute('aria-selected', 'true')
    })

    it('a saved answer with no rows at all has nothing to re-run', () => {
      renderWithQueryClient(
        <AnswerCell
          reply={{ ...reply, row_count: 0 }}
          saved={{ n: 1, createdAt: '2026-11-01T10:00:00Z' }}
        />,
      )

      expect(screen.queryByRole('button', { name: 'Re-run for rows' })).not.toBeInTheDocument()
      expect(screen.getByText('0 rows')).toBeInTheDocument()
    })
  })

  it('has no Chart tab for one number, and opens on the table', () => {
    render(<AnswerCell reply={single} />)

    expect(screen.queryByRole('tab', { name: 'Chart' })).not.toBeInTheDocument()
    expect(screen.getByRole('tab', { name: 'Table' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.getByRole('table')).toBeInTheDocument()
  })
})
