import { describe, expect, it } from 'vitest'

import { answer } from '@/mocks/fixtures'

import { columnTypes, compareValues, formatValue, inferColumnType, parseDate } from './columns'

describe('inferColumnType', () => {
  it.each([
    [[1, 2.5, -3], 'number'],
    [[1, null, 2], 'number'],
    [[true, false, null], 'boolean'],
    [['1997-07-04', '1998-01-01'], 'date'],
    [['1997-07-04T10:00:00', '1997-07-04T10:00:00+02:00', '1997-07-04 10:00'], 'datetime'],
    [['1997-07-04', '1997-07-04T10:00:00Z'], 'datetime'],
    [['Beverages', 'Produce'], 'text'],
    [[1, 'two'], 'text'],
    [['1997-07-04', 'soon'], 'text'],
    [['12:30:00'], 'text'],
    [[null, null], 'empty'],
    [[], 'empty'],
  ] as const)('%j → %s', (values, expected) => {
    expect(inferColumnType(values)).toBe(expected)
  })

  it('reads the recorded answer as text + number', () => {
    expect(columnTypes(answer.columns, answer.rows)).toEqual(['text', 'number'])
  })
})

describe('parseDate', () => {
  it('reads a date as that local day, not as UTC midnight', () => {
    const date = parseDate('1997-07-04')
    expect([date.getFullYear(), date.getMonth(), date.getDate()]).toEqual([1997, 6, 4])
    expect(date.getHours()).toBe(0)
  })
})

describe('formatValue', () => {
  it('formats numbers in the browser language, at most 2 decimals', () => {
    expect(formatValue(267868.17978624784, 'number')).toBe(
      new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(267868.18),
    )
    expect(formatValue(42, 'number')).toBe('42')
  })

  it('formats dates and timestamps for people', () => {
    expect(formatValue('1997-07-04', 'date')).toBe(
      new Intl.DateTimeFormat(undefined, { dateStyle: 'medium' }).format(new Date(1997, 6, 4)),
    )
    expect(formatValue('1997-07-04T10:30:00', 'datetime')).toBe(
      new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
        new Date(1997, 6, 4, 10, 30),
      ),
    )
  })

  it('leaves text alone and writes other JSON as JSON', () => {
    expect(formatValue('Beverages', 'text')).toBe('Beverages')
    expect(formatValue([1, 2], 'text')).toBe('[1,2]')
    expect(formatValue(null, 'number')).toBe('')
  })
})

describe('compareValues', () => {
  function sorted(values: unknown[], type: Parameters<typeof compareValues>[2]) {
    return [...values].sort((a, b) => compareValues(a, b, type))
  }

  it('sorts numbers as numbers (10 after 9, not after 1)', () => {
    expect(sorted([10, 9, -1, 2.5], 'number')).toEqual([-1, 2.5, 9, 10])
  })

  it('sorts dates and timestamps by time', () => {
    expect(sorted(['1998-01-01', '1996-12-31', '1997-07-04'], 'date')).toEqual([
      '1996-12-31',
      '1997-07-04',
      '1998-01-01',
    ])
    // Same moment written in two zones, and a later one.
    expect(compareValues('1997-07-04T10:00:00Z', '1997-07-04T12:00:00+02:00', 'datetime')).toBe(0)
  })

  it('sorts text like people do: "Item 2" before "Item 10", case ignored', () => {
    expect(sorted(['Item 10', 'item 2', 'Item 1'], 'text')).toEqual(['Item 1', 'item 2', 'Item 10'])
  })

  it('sorts booleans false first', () => {
    expect(sorted([true, false], 'boolean')).toEqual([false, true])
  })
})
