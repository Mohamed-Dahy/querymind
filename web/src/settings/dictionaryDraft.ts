/**
 * The dictionary review's unsaved edits (U7.1c), one draft per table, and what a save sends.
 *
 * Two rules of PUT /connections/{id}/dictionary shape this file:
 * - A table that is sent is replaced: its description, its "reviewed" mark, and its personal-data
 *   flags when `pii` is sent. So a save always sends the table's whole description and mark, even
 *   when only one column changed: leaving them out would blank the description and un-review it.
 * - `pii: null` (a table drafted before 5.4) means "not flagged yet": the column names are guessed
 *   from (email, phone…), and those columns are masked today. Sending `[]` would unmask them for
 *   every role. So `pii` is sent only when the admin ticked or unticked a box, and the boxes start
 *   from what is masked now (the explorer's flags), never from an empty list.
 */
import type { DictionaryTable, TableEdit } from '@/api/onboarding'
import type { SemanticOverview } from '@/api/explorer'

/** One table's edits, kept while the admin looks at other tables, until saved. */
export type TableDraft = {
  description: string
  /** Every column's text, as edited. */
  columns: Record<string, string>
  /** The personal columns as ticked, or null: untouched, so the flags stay as the API has them. */
  pii: string[] | null
}

export function startDraft(saved: DictionaryTable): TableDraft {
  return { description: saved.description, columns: { ...saved.columns }, pii: null }
}

/**
 * The columns masked now: the table's own flags, or (not flagged yet) the server's name guesses,
 * as the explorer shows them. `undefined` while those guesses are still loading.
 */
export function maskedNow(
  saved: DictionaryTable,
  guessed: string[] | undefined,
): string[] | undefined {
  return saved.pii ?? guessed
}

/** The ticked boxes: the draft's, else what is masked now. */
export function ticked(
  saved: DictionaryTable,
  draft: TableDraft | undefined,
  guessed: string[] | undefined,
): ReadonlySet<string> | undefined {
  const columns = draft?.pii ?? maskedNow(saved, guessed)
  return columns === undefined ? undefined : new Set(columns)
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  const left = new Set(a)
  return left.size === new Set(b).size && b.every((item) => left.has(item))
}

/** Does the draft differ from what is saved? An unchanged one is dropped (no "unsaved" mark). */
export function isChanged(
  draft: TableDraft,
  saved: DictionaryTable,
  guessed: string[] | undefined,
): boolean {
  if (draft.description !== saved.description) return true
  if (Object.entries(draft.columns).some(([name, text]) => text !== (saved.columns[name] ?? ''))) {
    return true
  }
  return draft.pii !== null && !sameSet(draft.pii, maskedNow(saved, guessed) ?? [])
}

/** The draft with one personal-data box ticked or unticked, starting from what is shown. */
export function withPersonal(
  draft: TableDraft,
  shown: ReadonlySet<string>,
  column: string,
  personal: boolean,
): TableDraft {
  const next = new Set(draft.pii ?? shown)
  if (personal) next.add(column)
  else next.delete(column)
  return { ...draft, pii: [...next] }
}

/** What saving sends for the table: all of it (see the top of the file), `pii` only if touched. */
export function tableEdit(
  saved: DictionaryTable,
  draft: TableDraft | undefined,
  reviewed: boolean,
): TableEdit {
  const edit: TableEdit = {
    description: draft?.description ?? saved.description,
    columns: draft?.columns ?? saved.columns,
    reviewed,
  }
  if (draft?.pii != null) edit.pii = draft.pii
  return edit
}

/**
 * The server's personal-data flags for one table of a saved database, from its overview, or
 * `undefined` when they aren't known (still loading, or a table the overview leaves out). Never `[]`
 * for "unknown": that would read as "nothing is masked".
 */
export function flaggedIn(
  overview: SemanticOverview | undefined,
  table: string,
): string[] | undefined {
  for (const business of overview?.businesses ?? []) {
    const found = business.tables.find(({ name }) => name === table)
    if (found) return found.columns.filter(({ pii }) => pii).map(({ name }) => name)
  }
  return undefined
}
