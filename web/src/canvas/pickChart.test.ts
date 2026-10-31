import { describe, expect, it } from 'vitest'

import { answer, monthly, single } from '@/mocks/fixtures'

import { describeChart, MAX_BARS, pickChart, type ChartSpec } from './pickChart'

const NUMBER = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })

function labels(spec: ChartSpec): string[] {
  return spec.kind === 'none' ? [] : spec.points.map((point) => point.label)
}

describe('pickChart on the recorded answers', () => {
  it('sales per category → a bar chart, biggest first', () => {
    const spec = pickChart(answer.columns, answer.rows)

    expect(spec).toMatchObject({
      kind: 'bar',
      xName: 'category_name',
      seriesNames: ['total_sales'],
    })
    expect(labels(spec)[0]).toBe('Beverages')
    expect(labels(spec).at(-1)).toBe('Grains/Cereals')
  })

  it('sales per month (month = 1–12) → a line chart in month order, with month names', () => {
    const spec = pickChart(monthly.columns, monthly.rows)

    expect(spec).toMatchObject({ kind: 'line', xName: 'month', seriesNames: ['total_sales'] })
    const january = new Intl.DateTimeFormat(undefined, { month: 'short' }).format(
      new Date(2000, 0, 1),
    )
    expect(labels(spec)).toHaveLength(12)
    expect(labels(spec)[0]).toBe(january)
  })

  it('one number → no chart', () => {
    expect(pickChart(single.columns, single.rows)).toMatchObject({ kind: 'none' })
  })
})

describe('pickChart rules', () => {
  it('a date column + a number → a line in time order, nulls in the values kept as gaps', () => {
    const spec = pickChart(
      ['order_date', 'orders'],
      [
        ['1997-03-01', 5],
        ['1997-01-01', null],
        ['1997-02-01', 7],
      ],
    )

    expect(spec.kind).toBe('line')
    expect(spec.kind !== 'none' && spec.points.map((point) => point.values[0])).toEqual([
      null,
      7,
      5,
    ])
  })

  it('a timestamp column reads as dates when every time is midnight', () => {
    const spec = pickChart(
      ['month', 'sales'],
      [
        ['1997-01-01T00:00:00', 1],
        ['1997-02-01T00:00:00', 2],
      ],
    )
    expect(labels(spec)[0]).toBe(
      new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(1997, 0, 1)),
    )
  })

  it('"order_year" with whole numbers is time; "year" with decimals is not', () => {
    expect(
      pickChart(
        ['order_year', 'sales'],
        [
          [1997, 1],
          [1996, 2],
        ],
      ).kind,
    ).toBe('line')
    expect(
      labels(
        pickChart(
          ['order_year', 'sales'],
          [
            [1997, 1],
            [1996, 2],
          ],
        ),
      ),
    ).toEqual(['1996', '1997'])
    expect(
      pickChart(
        ['year', 'sales'],
        [
          [1.5, 1],
          [2.5, 2],
        ],
      ).kind,
    ).toBe('none')
  })

  it('up to 3 number columns become lines; a 4th stays in the table', () => {
    const spec = pickChart(
      ['year', 'a', 'b', 'c', 'd'],
      [
        [1996, 1, 2, 3, 4],
        [1997, 1, 2, 3, 4],
      ],
    )
    expect(spec.kind !== 'none' && spec.seriesNames).toEqual(['a', 'b', 'c'])
  })

  it(`stops at ${MAX_BARS} bars`, () => {
    const rows = (count: number) => Array.from({ length: count }, (_, i) => [`c${i}`, i])
    expect(pickChart(['name', 'n'], rows(MAX_BARS)).kind).toBe('bar')
    expect(pickChart(['name', 'n'], rows(MAX_BARS + 1))).toMatchObject({
      kind: 'none',
      reason: 'More than 30 bars would be too many to read.',
    })
  })

  it('puts a bar without a value last, and names an empty category', () => {
    const spec = pickChart(
      ['name', 'n'],
      [
        ['a', null],
        [null, 3],
        ['c', 5],
      ],
    )
    expect(labels(spec)).toEqual(['c', '(empty)', 'a'])
  })

  it.each([
    [
      'two text columns',
      ['first', 'last', 'orders'],
      [
        ['A', 'B', 1],
        ['C', 'D', 2],
      ],
    ],
    [
      'two numbers, no text',
      ['price', 'stock'],
      [
        [1, 2],
        [3, 4],
      ],
    ],
    [
      'no number at all',
      ['name', 'city'],
      [
        ['A', 'B'],
        ['C', 'D'],
      ],
    ],
  ])('gives no chart for %s', (_case, columns, rows) => {
    expect(pickChart(columns, rows).kind).toBe('none')
  })
})

describe('describeChart', () => {
  it('says what a sighted reader sees in the bar chart', () => {
    expect(describeChart(pickChart(answer.columns, answer.rows))).toBe(
      `Bar chart of total_sales by category_name, 8 bars. Highest: Beverages, ${NUMBER.format(267868.18)}. Lowest: Grains/Cereals, ${NUMBER.format(95744.59)}.`,
    )
  })

  it('names the range of a line chart', () => {
    const text = describeChart(pickChart(monthly.columns, monthly.rows))
    expect(text).toMatch(
      /^Line chart of total_sales by month, 12 points, from \S+ to \S+\. Highest: /,
    )
  })

  it('gives the reason when there is no chart', () => {
    expect(describeChart(pickChart(single.columns, single.rows))).toBe(
      'One row: the number says it all.',
    )
  })
})
