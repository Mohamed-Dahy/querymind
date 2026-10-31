import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'

import type { AskResponse } from '@/api/client'
import * as csv from '@/lib/csv'
import { answer } from '@/mocks/fixtures'

import { AnswerCell } from './AnswerCell'

// Variants of the recorded answer, built by spreading it: still the API's type, never hand-copied.
function renderAnswer(changes: Partial<AskResponse> = {}) {
  const user = userEvent.setup()
  render(<AnswerCell reply={{ ...answer, ...changes }} />)
  return user
}

afterEach(() => {
  vi.restoreAllMocks()
})

describe('AnswerCell', () => {
  it('shows the answer, high confidence, the row count and the table', () => {
    renderAnswer()

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

  it('says when only the first rows are shown', () => {
    renderAnswer({ truncated: true, row_count: 1000 })

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

    await user.click(screen.getByRole('button', { name: 'Download CSV' }))

    expect(download).toHaveBeenCalledExactlyOnceWith(
      'what-is-the-total-sales-amount-per-category.csv',
      csv.toCsv(answer.columns, answer.rows),
    )
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
})
