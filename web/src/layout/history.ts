/** How the history list is grouped. Pure, so it is tested with fixed dates. */

const WEEK_MS = 7 * 24 * 60 * 60 * 1000

export type HistoryGroup<Item> = { title: string; items: Item[] }

type Dated = { pinned: boolean; updated_at: string }

function isSameDay(a: Date, b: Date): boolean {
  return a.toDateString() === b.toDateString()
}

/**
 * Pinned, Today, This week, Older, in the order given (the API sends pinned first, then the most
 * recent). "Today" is the viewer's own day; empty groups are left out.
 */
export function groupConversations<Item extends Dated>(
  items: readonly Item[],
  now: Date,
): HistoryGroup<Item>[] {
  const groups: Record<'Pinned' | 'Today' | 'This week' | 'Older', Item[]> = {
    Pinned: [],
    Today: [],
    'This week': [],
    Older: [],
  }
  for (const item of items) {
    const updated = new Date(item.updated_at)
    if (item.pinned) groups.Pinned.push(item)
    else if (isSameDay(updated, now)) groups.Today.push(item)
    else if (now.getTime() - updated.getTime() < WEEK_MS) groups['This week'].push(item)
    else groups.Older.push(item)
  }
  return Object.entries(groups)
    .filter(([, group]) => group.length > 0)
    .map(([title, group]) => ({ title, items: group }))
}
