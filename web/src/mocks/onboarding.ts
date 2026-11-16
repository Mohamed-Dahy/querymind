/**
 * The fake API's onboarding (U7.1c), in `localStorage` like the saved databases. A run moves one
 * stage each time its progress is asked for (queued → reading → drafting → ready), so the progress
 * view can be seen in mock mode and stepped through in tests. The drafted dictionary is the demo's
 * Northwind descriptions (the tables a saved database shows in mock mode), with one table skipped,
 * as the real API would for a name it can't describe.
 *
 * Shapes and messages as recorded from the real API on 2026-11-16 (a live run on Groq): 404
 * `dictionary_not_found` before any run, 409 `llm_key_required` without a key, 409
 * `onboarding_running` while one goes on, 422 `dictionary_invalid` for a table or column it lacks.
 */
import type { ConnectionInfo } from '@/api/settings'
import type { DataDictionary, OnboardingInfo, TableEdit } from '@/api/onboarding'
import type { SemanticOverview, TableMeaning } from '@/api/explorer'

import { semantic } from './fixtures'

const KEY = 'querymind-mock-onboarding'

/** Name a database this to see a run fail while drafting (the provider refused the key). */
export const FAILING_DATABASE = 'Broken'
/** The real API's words for that failure (api/errors.py LLM_MESSAGES, for LLMAuthError). */
export const FAILED_MESSAGE =
  'The AI provider refused the API key. If your workspace saved its own key, an admin can update it in Settings.'
/** The table the fake run skips, as the real one skips a name it can't describe. */
export const SKIPPED_TABLE = 'us_states'

type Run = {
  /** How many times progress was asked for: the stage. */
  polls: number
  /** Tables to draft this run: 0 when the dictionary has them all already. */
  todo: number
  failing: boolean
  started_at: string
  updated_at: string
  dictionary: DataDictionary | null
}

function loadAll(): Record<string, Run> {
  try {
    return JSON.parse(localStorage.getItem(KEY) ?? '{}') as Record<string, Run>
  } catch {
    return {}
  }
}

function saveAll(runs: Record<string, Run>) {
  localStorage.setItem(KEY, JSON.stringify(runs))
}

const NORTHWIND = semantic.businesses.find(({ database }) => database === 'northwind')!

/** What drafting the demo's Northwind tables gives: their recorded descriptions, not reviewed. */
function drafted(connection: ConnectionInfo): DataDictionary {
  const tables: DataDictionary['tables'] = {}
  for (const table of NORTHWIND.tables) {
    if (table.name === SKIPPED_TABLE) continue
    tables[table.name] = {
      reviewed: false,
      description: table.dictionary_description,
      columns: Object.fromEntries(
        table.columns.map(({ name, dictionary_note }) => [name, dictionary_note]),
      ),
      pii: table.columns.filter(({ pii }) => pii).map(({ name }) => name),
    }
  }
  return { database: connection.name, generated_by: 'groq', tables }
}

const DESCRIBABLE = NORTHWIND.tables.length

/** Queue a run, as POST /connections/{id}/onboard does; `null` if one is going on already. */
export function startMockOnboarding(connection: ConnectionInfo): OnboardingInfo | null {
  const runs = loadAll()
  const before = runs[connection.id]
  if (before && stage(before) !== 'ready' && stage(before) !== 'failed') return null
  const now = new Date().toISOString()
  const run: Run = {
    polls: 0,
    // A new run drafts only what has no description yet (reviewed tables are kept).
    todo: before?.dictionary ? 0 : DESCRIBABLE,
    failing: connection.name === FAILING_DATABASE,
    started_at: now,
    updated_at: now,
    dictionary: before?.dictionary ?? null,
  }
  runs[connection.id] = run
  saveAll(runs)
  return info(run)
}

function stage(run: Run): OnboardingInfo['status'] {
  if (run.polls === 0) return 'queued'
  if (run.polls === 1) return 'reading'
  if (run.polls === 2) return run.failing ? 'failed' : 'drafting'
  return run.failing ? 'failed' : 'ready'
}

function info(run: Run): OnboardingInfo {
  const status = stage(run)
  const total = status === 'queued' || status === 'reading' ? 0 : run.todo
  const skipped = status === 'ready' && run.todo > 0 ? 1 : 0
  const drafted =
    status === 'ready' ? total - skipped : status === 'drafting' ? Math.floor(total / 2) : 0
  return {
    status,
    tables_total: total,
    tables_drafted: drafted,
    tables_skipped: skipped,
    error: status === 'failed' ? FAILED_MESSAGE : null,
    generated_by: status === 'ready' ? 'groq' : '',
    has_dictionary: run.dictionary !== null,
    started_at: run.started_at,
    updated_at: run.updated_at,
  }
}

/** GET …/onboarding: where the run is (and one stage further next time), or `null`: never run. */
export function pollMockOnboarding(connection: ConnectionInfo): OnboardingInfo | null {
  const runs = loadAll()
  const run = runs[connection.id]
  if (!run) return null
  const before = stage(run)
  if (before !== 'ready' && before !== 'failed') {
    run.polls += 1
    run.updated_at = new Date().toISOString()
    if (stage(run) === 'ready' && run.dictionary === null) run.dictionary = drafted(connection)
    saveAll(runs)
  }
  return info(run)
}

export function isMockOnboardingRunning(id: string): boolean {
  const run = loadAll()[id]
  return run !== undefined && !['ready', 'failed'].includes(stage(run))
}

export function mockDictionary(id: string): DataDictionary | null {
  return loadAll()[id]?.dictionary ?? null
}

/**
 * PUT …/dictionary, as api/onboarding.py `apply_edit` does: each table sent is replaced (its
 * description, its mark, its `pii` when sent); a table or column the dictionary lacks is refused.
 * Returns the saved dictionary, or the refusal's message.
 */
export function editMockDictionary(
  id: string,
  edit: Record<string, TableEdit>,
): DataDictionary | { refused: string } {
  const runs = loadAll()
  const run = runs[id]
  if (!run?.dictionary) return { refused: 'This database has no data dictionary yet: onboard it.' }
  const tables = { ...run.dictionary.tables }
  for (const [name, change] of Object.entries(edit)) {
    const current = tables[name]
    if (!current) return { refused: `The dictionary has no table '${name}'.` }
    const unknown = [...Object.keys(change.columns ?? {}), ...(change.pii ?? [])]
      .filter((column) => !(column in current.columns))
      .sort()
    if (unknown.length > 0) return { refused: `Table '${name}' has no column '${unknown[0]}'.` }
    const pii = change.pii === undefined || change.pii === null ? current.pii : change.pii
    tables[name] = {
      reviewed: change.reviewed ?? false,
      description: (change.description ?? '').trim(),
      columns: Object.fromEntries(
        Object.entries(current.columns).map(([column, text]) => [
          column,
          (change.columns?.[column] ?? text).trim(),
        ]),
      ),
      // In table order, once each.
      pii:
        pii == null ? pii : Object.keys(current.columns).filter((column) => pii.includes(column)),
    }
  }
  run.dictionary = { ...run.dictionary, tables }
  saveAll(runs)
  return run.dictionary
}

export function forgetMockOnboarding(id: string) {
  const runs = loadAll()
  delete runs[id]
  saveAll(runs)
}

/**
 * What the explorer shows of an onboarded saved database: one business named after it, with the
 * dictionary's descriptions and personal-data flags (the real API builds its layer the same way).
 */
export function onboardedOverview(
  connection: ConnectionInfo,
  dictionary: DataDictionary,
): SemanticOverview {
  const tables: TableMeaning[] = Object.entries(dictionary.tables).map(([name, table]) => ({
    name,
    description: table.description,
    synonyms: [],
    dictionary_description: table.description,
    dictionary_reviewed: table.reviewed,
    columns: Object.entries(table.columns).map(([column, note]) => ({
      name: column,
      description: note,
      synonyms: [],
      pii: (table.pii ?? []).includes(column),
      dictionary_note: note,
    })),
    suggested_questions: [],
  }))
  return {
    businesses: [
      {
        database: connection.name,
        description: `The database ${connection.dbname} of this workspace.`,
        tables,
        metrics: [],
        joins: [],
      },
    ],
  }
}
