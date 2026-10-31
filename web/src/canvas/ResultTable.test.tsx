import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it } from 'vitest'

import { answer, answerWithRows } from '@/mocks/fixtures'

import { ResultTable } from './ResultTable'

const ROWS: unknown[][] = [
  ['Beverages', 3, '1997-07-04'],
  ['Produce', null, '1996-12-31'],
  ['condiments', 10, null],
  ['Dairy', -2, '1998-01-01'],
]
const COLUMNS = ['name', 'total', 'first_order']

function renderTable(columns = COLUMNS, rows = ROWS) {
  const user = userEvent.setup()
  render(<ResultTable columns={columns} rows={rows} question="Test question" />)
  return user
}

/** The text of one column, top to bottom, for the drawn rows. */
function columnText(index: number): string[] {
  const body = screen.getAllByRole('rowgroup')[1] as HTMLElement
  return within(body)
    .getAllByRole('row')
    .map((row) => within(row).getAllByRole('cell')[index]?.textContent ?? '')
}

function sortButton(name: string) {
  return screen.getByRole('button', { name })
}

describe('ResultTable', () => {
  it('shows the rows in the order the query returned them, numbers on the right', () => {
    renderTable()

    expect(columnText(0)).toEqual(['Beverages', 'Produce', 'condiments', 'Dairy'])
    expect(screen.getAllByRole('cell')[1]).toHaveClass('text-right')
    expect(screen.getByRole('columnheader', { name: 'total' })).toHaveAttribute('aria-sort', 'none')
    expect(screen.getByRole('table')).toHaveAccessibleName(
      'Result rows for: Test question. In the order the query returned them.',
    )
  })

  it('sorts numbers ascending, descending, then back, with null last both ways', async () => {
    const user = renderTable()
    const header = screen.getByRole('columnheader', { name: 'total' })

    await user.click(sortButton('total'))
    expect(columnText(1)).toEqual(['-2', '3', '10', 'null'])
    expect(header).toHaveAttribute('aria-sort', 'ascending')
    expect(screen.getByRole('table')).toHaveAccessibleName(
      'Result rows for: Test question. Sorted by total, ascending.',
    )

    await user.click(sortButton('total'))
    expect(columnText(1)).toEqual(['10', '3', '-2', 'null'])
    expect(header).toHaveAttribute('aria-sort', 'descending')

    await user.click(sortButton('total'))
    expect(columnText(0)).toEqual(['Beverages', 'Produce', 'condiments', 'Dairy'])
    expect(header).toHaveAttribute('aria-sort', 'none')
  })

  it('sorts text the way people read it (case ignored)', async () => {
    const user = renderTable()

    await user.click(sortButton('name'))

    expect(columnText(0)).toEqual(['Beverages', 'condiments', 'Dairy', 'Produce'])
  })

  it('sorts dates by time, with null last', async () => {
    const user = renderTable()

    await user.click(sortButton('first_order'))
    expect(columnText(0)).toEqual(['Produce', 'Beverages', 'Dairy', 'condiments'])

    await user.click(sortButton('first_order'))
    expect(columnText(0)).toEqual(['Dairy', 'Beverages', 'Produce', 'condiments'])
  })

  it('formats numbers and dates for people, and shows null as "null"', () => {
    renderTable()

    const firstRow = screen.getAllByRole('row')[1] as HTMLElement
    expect(
      within(firstRow)
        .getAllByRole('cell')
        .map((cell) => cell.textContent),
    ).toEqual([
      'Beverages',
      '3',
      new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(1997, 6, 4)),
    ])
    expect(screen.getAllByText('null')).toHaveLength(2)
  })

  it('keeps two columns with the same name apart', async () => {
    const user = renderTable(
      ['name', 'name'],
      [
        ['b', 'x'],
        ['a', 'y'],
      ],
    )

    await user.click(screen.getAllByRole('button', { name: 'name' })[1] as HTMLElement)

    expect(columnText(1)).toEqual(['x', 'y'])
  })

  it('shows the recorded answer', () => {
    renderTable(answer.columns, answer.rows)

    expect(screen.getAllByRole('row')).toHaveLength(answer.rows.length + 1)
    expect(screen.getByRole('table')).toHaveAttribute('aria-rowcount', '9')
  })

  it('draws only a window of 1,000 rows, and says where each row is in all of them', async () => {
    const { columns, rows } = answerWithRows(1000)
    const user = renderTable(columns, rows)

    const drawn = screen.getAllByRole('row').slice(1)
    // 384 px / 36 px a row ≈ 11 rows, plus 10 drawn below: far fewer than 1,000.
    expect(drawn.length).toBeGreaterThan(5)
    expect(drawn.length).toBeLessThan(40)
    expect(screen.getByRole('table')).toHaveAttribute('aria-rowcount', '1001')
    expect(drawn[0]).toHaveAttribute('aria-rowindex', '2')

    // Sorting works on all 1,000 rows, not just the drawn ones: the largest total comes first.
    await user.click(sortButton('total_sales'))
    await user.click(sortButton('total_sales'))
    const totals = rows.map((row) => row[1]).filter((value) => typeof value === 'number')
    const largest = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(
      Math.max(...totals),
    )
    expect(columnText(1)[0]).toBe(largest)
  })
})
