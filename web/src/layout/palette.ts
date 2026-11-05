/**
 * What the command palette lists for what was typed: a pure function, so the ranking and the
 * "Enter never spends a question by accident" rule are unit-tested without a browser.
 */
import type { ThemeChoice } from '@/lib/theme'

export type PaletteAction =
  | { kind: 'navigate'; to: string }
  | { kind: 'new-conversation' }
  | { kind: 'theme'; choice: ThemeChoice }
  | { kind: 'ask'; question: string }

export type PaletteItem = {
  /** Unique in the palette: cmdk's value for the item. */
  id: string
  label: string
  /** A second, muted line: the business, the date… */
  hint?: string
  /** Other words that find it (synonyms, columns), weighted below the label. */
  keywords?: readonly string[]
  action: PaletteAction
  /** Why it can't be chosen right now; the item is shown but not selectable. */
  disabledReason?: string
}

export type GroupName = 'Actions' | 'Pages' | 'Conversations' | 'Tables' | 'Terms' | 'Ask'

export type PaletteGroup = { name: GroupName; items: PaletteItem[] }

/** A list that comes from the API, which may still be loading or have failed. */
export type Source = { items: PaletteItem[]; note?: string }

export type PaletteSources = {
  actions: PaletteItem[]
  pages: PaletteItem[]
  conversations: Source
  tables: Source
  terms: Source
}

export type AskState = {
  /** A question with no conversation id is running: starting over would cancel it. */
  busy: boolean
  maxLength: number
}

/** Conversations shown before anything is typed: the most recent ones. */
export const RECENT_CONVERSATIONS = 5

const SCORE_EXACT = 100
const SCORE_PREFIX = 80
const SCORE_WORD = 60
const SCORE_INSIDE = 40
// A keyword match counts for less than the same match on the label.
const KEYWORD_FACTOR = 0.5

// Inside one kind of match, the closer one wins: "order" puts `orders` before `order_details`.
// Always under 10, so it never lifts a match into the kind above (they are 20 apart).
const CLOSENESS = 9

function textScore(text: string, needle: string): number {
  const value = text.toLowerCase()
  if (value === needle) return SCORE_EXACT
  const closeness = (CLOSENESS * needle.length) / value.length
  if (value.startsWith(needle)) return SCORE_PREFIX + closeness
  // The start of a later word: "ship" finds "Late shipment", "dat" finds "Data explorer".
  if (value.split(/[^\p{L}\p{N}]+/u).some((word) => word.startsWith(needle))) {
    return SCORE_WORD + closeness
  }
  if (value.includes(needle)) return SCORE_INSIDE + closeness
  return 0
}

/** How well an item matches (0 = not at all). `needle` is already trimmed and lower case. */
export function scoreItem(item: PaletteItem, needle: string): number {
  const label = textScore(item.label, needle)
  const keywords = Math.max(0, ...(item.keywords ?? []).map((word) => textScore(word, needle)))
  return Math.max(label, keywords * KEYWORD_FACTOR)
}

function askItem(question: string, ask: AskState): PaletteItem {
  const length = [...question].length
  const disabledReason = ask.busy
    ? 'Wait for the question that is running to finish, or stop it.'
    : length > ask.maxLength
      ? `Questions can be at most ${ask.maxLength.toLocaleString('en')} characters.`
      : undefined
  return {
    id: 'ask',
    label: `Ask “${question}”`,
    hint: 'In a new conversation',
    action: { kind: 'ask', question },
    ...(disabledReason !== undefined && { disabledReason }),
  }
}

/**
 * The groups to show, best match first.
 * - Nothing typed: the actions, the pages and the most recent conversations.
 * - Something typed: every matching item, each group sorted by score, and the groups ordered by
 *   their best item, so the first item (the one Enter picks) is the best match overall.
 * - "Ask" always comes last, so it is the Enter default only when nothing else matches: typing
 *   "orders" and pressing Enter opens the table instead of spending a question.
 */
export function paletteGroups(
  query: string,
  sources: PaletteSources,
  ask: AskState,
): PaletteGroup[] {
  const needle = query.trim().toLowerCase()
  const lists: [GroupName, PaletteItem[]][] = [
    ['Actions', sources.actions],
    ['Pages', sources.pages],
    ['Conversations', sources.conversations.items],
    ['Tables', sources.tables.items],
    ['Terms', sources.terms.items],
  ]

  if (!needle) {
    return lists
      .filter(([name]) => name === 'Actions' || name === 'Pages' || name === 'Conversations')
      .map(([name, items]) => ({
        name,
        items: name === 'Conversations' ? items.slice(0, RECENT_CONVERSATIONS) : items,
      }))
      .filter(({ items }) => items.length > 0)
  }

  const ranked = lists
    .map(([name, items]) => {
      const scored = items
        .map((item, index) => ({ item, index, score: scoreItem(item, needle) }))
        .filter(({ score }) => score > 0)
        // Ties keep the source's order (pinned and recent conversations first, for example).
        .sort((a, b) => b.score - a.score || a.index - b.index)
      return { name, items: scored.map(({ item }) => item), best: scored[0]?.score ?? 0 }
    })
    .filter(({ items }) => items.length > 0)
  // Array.sort is stable, so equal groups keep the order above.
  ranked.sort((a, b) => b.best - a.best)

  const groups: PaletteGroup[] = ranked.map(({ name, items }) => ({ name, items }))
  groups.push({ name: 'Ask', items: [askItem(query.trim(), ask)] })
  return groups
}

/** One line per list that couldn't be loaded (or is still loading), for under the results. */
export function paletteNotes(sources: PaletteSources): string[] {
  return [sources.conversations.note, sources.tables.note, sources.terms.note].filter(
    (note): note is string => note !== undefined,
  )
}
