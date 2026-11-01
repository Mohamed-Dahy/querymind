import { describe, expect, it } from 'vitest'

import { diffWords, type DiffPart } from './diff'

const rebuilt = (parts: DiffPart[], kinds: DiffPart['kind'][]) =>
  parts
    .filter((part) => kinds.includes(part.kind))
    .map((part) => part.text)
    .join('')

describe('diffWords', () => {
  it('finds a changed word in a real repair', () => {
    const before = 'SELECT country, COUNT(*) FROM customers GROUP BY region LIMIT 1001'
    const after = 'SELECT country, COUNT(*) FROM customers GROUP BY country LIMIT 1001'

    expect(diffWords(before, after)).toEqual([
      { kind: 'same', text: 'SELECT country, COUNT(*) FROM customers GROUP BY ' },
      { kind: 'removed', text: 'region' },
      { kind: 'added', text: 'country' },
      { kind: 'same', text: ' LIMIT 1001' },
    ])
  })

  it('identical texts have one `same` part', () => {
    expect(diffWords('SELECT 1', 'SELECT 1')).toEqual([{ kind: 'same', text: 'SELECT 1' }])
  })

  it('handles empty texts', () => {
    expect(diffWords('', '')).toEqual([])
    expect(diffWords('', 'SELECT 1')).toEqual([{ kind: 'added', text: 'SELECT 1' }])
    expect(diffWords('SELECT 1', '')).toEqual([{ kind: 'removed', text: 'SELECT 1' }])
  })

  it('compares whole words, not letters', () => {
    expect(diffWords('order_date', 'order_day')).toEqual([
      { kind: 'removed', text: 'order_date' },
      { kind: 'added', text: 'order_day' },
    ])
  })

  it.each([
    ['SELECT a FROM t', 'SELECT b, a FROM t WHERE x = 1 ORDER BY a'],
    ['a b c d', 'd c b a'],
    ['SELECT\n  naïve_ç\nFROM t', 'SELECT naïve_ç, 1 FROM t'],
    ['WITH x AS (SELECT 1) SELECT * FROM x', 'SELECT 1'],
  ])('nothing is lost: both texts can be rebuilt (%s)', (before, after) => {
    const parts = diffWords(before, after)

    expect(rebuilt(parts, ['same', 'removed'])).toBe(before)
    expect(rebuilt(parts, ['same', 'added'])).toBe(after)
  })

  it('very long texts are simply replaced, in one go', () => {
    const before = 'a '.repeat(2000)
    const after = 'b '.repeat(2000)

    expect(diffWords(before, after)).toEqual([
      { kind: 'removed', text: before },
      { kind: 'added', text: after },
    ])
  })
})
