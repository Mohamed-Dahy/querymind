import { describe, expect, it } from 'vitest'

import {
  paletteGroups,
  paletteNotes,
  RECENT_CONVERSATIONS,
  scoreItem,
  type PaletteItem,
  type PaletteSources,
} from './palette'

const item = (id: string, label: string, keywords: string[] = []): PaletteItem => ({
  id,
  label,
  keywords,
  action: { kind: 'navigate', to: `/${id}` },
})

function sources(overrides: Partial<PaletteSources> = {}): PaletteSources {
  return {
    actions: [{ id: 'new', label: 'New conversation', action: { kind: 'new-conversation' } }],
    pages: [item('explore', 'Data explorer', ['tables']), item('glossary', 'Business glossary')],
    conversations: {
      items: Array.from({ length: 8 }, (_, n) => item(`c${n}`, `Sales question ${n}`)),
    },
    tables: {
      items: [
        item('orders', 'orders', ['order_id', 'ship_country']),
        item('order_details', 'order_details', ['unit_price']),
        item('customers', 'customers', ['clients', 'country']),
      ],
    },
    terms: { items: [item('late', 'Late shipment', ['delayed order'])] },
    ...overrides,
  }
}

const IDLE = { busy: false, maxLength: 1000 }
const ids = (groups: ReturnType<typeof paletteGroups>) =>
  groups.flatMap(({ items }) => items.map(({ id }) => id))

describe('scoreItem', () => {
  it('ranks exact, then prefix, then word start, then inside the label', () => {
    expect(scoreItem(item('a', 'orders'), 'orders')).toBeGreaterThan(
      scoreItem(item('b', 'order_details'), 'orders'),
    )
    expect(scoreItem(item('b', 'order_details'), 'order')).toBeGreaterThan(
      scoreItem(item('c', 'Late shipment'), 'ship'),
    )
    expect(scoreItem(item('c', 'Late shipment'), 'ship')).toBeGreaterThan(
      scoreItem(item('d', 'membership'), 'ship'),
    )
    expect(scoreItem(item('d', 'membership'), 'ship')).toBeGreaterThan(0)
    expect(scoreItem(item('e', 'customers'), 'ship')).toBe(0)
  })

  it('puts the closer of two matches of the same kind first', () => {
    expect(scoreItem(item('a', 'orders'), 'order')).toBeGreaterThan(
      scoreItem(item('b', 'order_details'), 'order'),
    )
    // …but never above a better kind of match.
    expect(scoreItem(item('c', 'order details and more'), 'order')).toBeGreaterThan(
      scoreItem(item('d', 'x order'), 'order'),
    )
  })

  it('counts another word for it, below the same match on the label', () => {
    const bySynonym = scoreItem(item('customers', 'customers', ['clients']), 'clients')
    expect(bySynonym).toBeGreaterThan(0)
    expect(bySynonym).toBeLessThan(scoreItem(item('x', 'clients'), 'clients'))
  })
})

describe('paletteGroups', () => {
  it('with nothing typed: actions, pages and only the most recent conversations', () => {
    const groups = paletteGroups('', sources(), IDLE)

    expect(groups.map(({ name }) => name)).toEqual(['Actions', 'Pages', 'Conversations'])
    expect(groups[2]?.items).toHaveLength(RECENT_CONVERSATIONS)
    expect(ids(groups)).not.toContain('ask')
  })

  it('puts the best match first, so Enter opens the table, not a question', () => {
    const groups = paletteGroups('order', sources(), IDLE)

    expect(ids(groups).slice(0, 2)).toEqual(['orders', 'order_details'])
    expect(groups.at(-1)?.name).toBe('Ask')
    expect(ids(groups).at(-1)).toBe('ask')
  })

  it('orders the groups by their best item', () => {
    // "late" is a term's first word and only inside nothing else: Terms comes first.
    expect(paletteGroups('late', sources(), IDLE)[0]?.name).toBe('Terms')
    // "sales" starts every conversation's title.
    expect(paletteGroups('sales', sources(), IDLE)[0]?.name).toBe('Conversations')
  })

  it('lists every matching conversation when searching, not only the recent ones', () => {
    const groups = paletteGroups('sales question', sources(), IDLE)
    expect(groups.find(({ name }) => name === 'Conversations')?.items).toHaveLength(8)
  })

  it('makes Ask the only item, and so the Enter default, when nothing else matches', () => {
    const groups = paletteGroups('  Top 3 artists by sales  ', sources(), IDLE)

    expect(groups.map(({ name }) => name)).toEqual(['Ask'])
    expect(groups[0]?.items[0]).toMatchObject({
      label: 'Ask “Top 3 artists by sales”',
      action: { kind: 'ask', question: 'Top 3 artists by sales' },
    })
    expect(groups[0]?.items[0]?.disabledReason).toBeUndefined()
  })

  it("can't ask while a question at the start is running, and says why", () => {
    const [ask] = paletteGroups('anything', sources(), { busy: true, maxLength: 1000 })
    expect(ask?.items[0]?.disabledReason).toMatch(/Wait for the question/)
  })

  it("can't ask a question over the length limit (counted in characters, not bytes)", () => {
    expect(
      paletteGroups('é'.repeat(10), sources(), { busy: false, maxLength: 10 })[0]?.items[0]
        ?.disabledReason,
    ).toBeUndefined()
    expect(
      paletteGroups('é'.repeat(11), sources(), { busy: false, maxLength: 10 })[0]?.items[0]
        ?.disabledReason,
    ).toBe('Questions can be at most 10 characters.')
  })

  it('still lists the other groups when one source failed', () => {
    const groups = paletteGroups(
      'orders',
      sources({ conversations: { items: [], note: "History isn't available." } }),
      IDLE,
    )
    expect(ids(groups)[0]).toBe('orders')
  })
})

describe('paletteNotes', () => {
  it('gives one line per list that is loading or failed', () => {
    expect(paletteNotes(sources())).toEqual([])
    expect(
      paletteNotes(
        sources({
          conversations: { items: [], note: "History isn't available." },
          tables: { items: [], note: 'Loading tables…' },
        }),
      ),
    ).toEqual(["History isn't available.", 'Loading tables…'])
  })
})
