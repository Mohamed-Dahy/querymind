import { describe, expect, it } from 'vitest'

import { groupConversations } from './history'

const NOW = new Date(2026, 10, 3, 15, 0)

function item(id: string, updated: Date, pinned = false) {
  return { id, pinned, updated_at: updated.toISOString() }
}

const names = (groups: ReturnType<typeof groupConversations<ReturnType<typeof item>>>) =>
  groups.map(({ title, items }) => [title, items.map(({ id }) => id)])

describe('groupConversations', () => {
  it('puts each conversation in its group, in the order given', () => {
    const groups = groupConversations(
      [
        item('pinned-old', new Date(2020, 0, 1), true),
        item('today-1', new Date(2026, 10, 3, 14, 0)),
        item('today-2', new Date(2026, 10, 3, 0, 1)),
        item('yesterday', new Date(2026, 10, 2, 23, 59)),
        item('six-days', new Date(2026, 9, 28, 16, 0)),
        item('old', new Date(2026, 9, 1)),
      ],
      NOW,
    )

    expect(names(groups)).toEqual([
      ['Pinned', ['pinned-old']],
      ['Today', ['today-1', 'today-2']],
      ['This week', ['yesterday', 'six-days']],
      ['Older', ['old']],
    ])
  })

  it('leaves out empty groups', () => {
    expect(names(groupConversations([item('a', new Date(2026, 10, 3, 9, 0))], NOW))).toEqual([
      ['Today', ['a']],
    ])
    expect(groupConversations([], NOW)).toEqual([])
  })

  it('exactly a week ago is Older', () => {
    const weekAgo = new Date(NOW.getTime() - 7 * 24 * 60 * 60 * 1000)

    expect(names(groupConversations([item('a', weekAgo)], NOW))).toEqual([['Older', ['a']]])
  })
})
