import { describe, expect, it } from 'vitest'

import type { DictionaryTable } from '@/api/onboarding'
import type { SemanticOverview } from '@/api/explorer'

import {
  flaggedIn,
  isChanged,
  startDraft,
  tableEdit,
  ticked,
  withPersonal,
} from './dictionaryDraft'

const REVIEWED: DictionaryTable = {
  reviewed: true,
  description: 'One row per customer.',
  columns: { customer_id: 'Its id', email: 'Where to write', city: 'Where they are' },
  pii: ['email'],
}

// Drafted before 5.4: not flagged yet, so the server masks what the names suggest (email).
const NOT_FLAGGED: DictionaryTable = { ...REVIEWED, reviewed: false, pii: null }

describe('what saving a table sends', () => {
  it('keeps the description and the "reviewed" mark when only a column changed', () => {
    // The API replaces a table that is sent: leaving these out would blank and un-review it.
    const draft = { ...startDraft(REVIEWED), columns: { ...REVIEWED.columns, city: 'Their city' } }

    expect(tableEdit(REVIEWED, draft, REVIEWED.reviewed)).toEqual({
      description: 'One row per customer.',
      columns: { customer_id: 'Its id', email: 'Where to write', city: 'Their city' },
      reviewed: true,
    })
  })

  it('never sends pii for a table whose boxes were not touched, so null stays null', () => {
    // Sending [] would unmask the guessed columns (email) for every role.
    const draft = { ...startDraft(NOT_FLAGGED), description: 'Customers.' }

    const edit = tableEdit(NOT_FLAGGED, draft, false)
    expect(edit).not.toHaveProperty('pii')
  })

  it('marks reviewed without a draft: the saved table as it is, plus the mark', () => {
    expect(tableEdit(NOT_FLAGGED, undefined, true)).toEqual({
      description: NOT_FLAGGED.description,
      columns: NOT_FLAGGED.columns,
      reviewed: true,
    })
  })

  it('a box ticked on a table not flagged yet starts from what is masked now', () => {
    const shown = ticked(NOT_FLAGGED, undefined, ['email'])!
    const draft = withPersonal(startDraft(NOT_FLAGGED), shown, 'city', true)

    expect(tableEdit(NOT_FLAGGED, draft, false).pii).toEqual(['email', 'city'])
  })
})

describe('which boxes are ticked', () => {
  it("shows the table's own flags, else the server's guesses, else unknown (never none)", () => {
    expect(ticked(REVIEWED, undefined, ['city'])).toEqual(new Set(['email']))
    expect(ticked(NOT_FLAGGED, undefined, ['email'])).toEqual(new Set(['email']))
    expect(ticked(NOT_FLAGGED, undefined, undefined)).toBeUndefined()
  })

  it("the draft's ticks win once a box was touched", () => {
    const draft = withPersonal(startDraft(REVIEWED), new Set(['email']), 'email', false)
    expect(ticked(REVIEWED, draft, undefined)).toEqual(new Set())
  })
})

describe('is there anything unsaved', () => {
  it('no after changing something back', () => {
    const draft = { ...startDraft(REVIEWED), description: REVIEWED.description }
    expect(isChanged(draft, REVIEWED, undefined)).toBe(false)
  })

  it('yes for a description, a column, or a different set of personal columns', () => {
    const start = startDraft(REVIEWED)
    expect(isChanged({ ...start, description: 'x' }, REVIEWED, undefined)).toBe(true)
    expect(
      isChanged({ ...start, columns: { ...start.columns, city: 'x' } }, REVIEWED, undefined),
    ).toBe(true)
    expect(isChanged({ ...start, pii: ['email', 'city'] }, REVIEWED, undefined)).toBe(true)
  })

  it('no when the ticks, in another order, are what is masked now', () => {
    const draft = { ...startDraft(NOT_FLAGGED), pii: ['email'] }
    expect(isChanged(draft, NOT_FLAGGED, ['email'])).toBe(false)
  })
})

describe("the server's flags for one table", () => {
  const overview: SemanticOverview = {
    businesses: [
      {
        database: 'Sales',
        description: '',
        metrics: [],
        joins: [],
        tables: [
          {
            name: 'customers',
            description: '',
            synonyms: [],
            dictionary_description: '',
            dictionary_reviewed: false,
            suggested_questions: [],
            columns: [
              { name: 'email', description: '', synonyms: [], pii: true, dictionary_note: '' },
              { name: 'city', description: '', synonyms: [], pii: false, dictionary_note: '' },
            ],
          },
        ],
      },
    ],
  }

  it('lists its personal columns', () => {
    expect(flaggedIn(overview, 'customers')).toEqual(['email'])
  })

  it('is unknown, not "none", for a table the overview leaves out or while it loads', () => {
    expect(flaggedIn(overview, 'orders')).toBeUndefined()
    expect(flaggedIn(undefined, 'customers')).toBeUndefined()
  })
})
