import { describe, expect, it } from 'vitest'

import type { EvalOutcome, EvalsReply } from '@/api/evals'
import { evals } from '@/mocks/fixtures'

import {
  columnScore,
  defaultColumn,
  failingQuestions,
  filterOptions,
  filterQuestions,
  NO_FILTERS,
  questionHref,
  questionsOf,
  runColumns,
  verdictOf,
} from './questionRows'

const data = evals as EvalsReply
const outcome = (fields: Partial<EvalOutcome>): EvalOutcome => ({
  question_id: 'q',
  scored: true,
  passed: true,
  ex: true,
  reason: '',
  ex_reason: '',
  sql: 'SELECT 1',
  sql_attempts: 1,
  seconds: 1,
  input_tokens: 1,
  output_tokens: 1,
  ...fields,
})

describe('verdictOf', () => {
  it('tells the four states apart', () => {
    expect(verdictOf(undefined, 'golden')).toBe('not-run')
    expect(verdictOf(outcome({ scored: false }), 'golden')).toBe('not-scored')
    expect(verdictOf(outcome({ passed: true }), 'golden')).toBe('passed')
    expect(verdictOf(outcome({ passed: false }), 'golden')).toBe('failed')
  })

  it('judges BIRD by its official rule, and our sets by our match', () => {
    const split = outcome({ passed: true, ex: false })

    expect(verdictOf(split, 'bird')).toBe('failed')
    expect(verdictOf(split, 'golden')).toBe('passed')
  })
})

describe('runColumns', () => {
  it('joins each run across its suites: 6 for golden, 2 for BIRD', () => {
    expect(runColumns(data, 'golden').map(({ label }) => label)).toEqual([
      'Arm A',
      'Arm B',
      'Arm C',
      'Arm D',
      'gpt-oss-20b',
      'qwen3.8-27b',
    ])
    expect(runColumns(data, 'bird').map(({ label }) => label)).toEqual([
      'Arm C',
      'Arm C, 4 workers',
    ])
  })
})

describe('columnScore', () => {
  it.each([
    ['golden', 'Arm A', 45, 58],
    ['golden', 'Arm C', 57, 58],
    ['golden', 'Arm D', 53, 53],
    ['golden', 'gpt-oss-20b', 41, 43],
    ['golden', 'qwen3.8-27b', 42, 43],
    ['bird', 'Arm C', 57, 100],
    ['bird', 'Arm C, 4 workers', 60, 100],
  ] as const)('%s %s: %i of %i, as the reports say', (study, label, passed, total) => {
    const column = runColumns(data, study).find((candidate) => candidate.label === label)

    expect(column && columnScore(column, study)).toEqual({ passed, total })
  })
})

describe('filters', () => {
  const golden = questionsOf(data, 'golden')
  const columns = runColumns(data, 'golden')

  it('offers what the study has, with tiers from easy to hard', () => {
    const options = filterOptions(golden)

    expect(options.suites.toSorted()).toEqual(['business', 'chinook', 'golden'])
    expect(options.tiers).toEqual(['easy', 'medium', 'hard'])
    expect(filterOptions(questionsOf(data, 'bird')).databases).toHaveLength(11)
  })

  it('narrows by suite and tier together', () => {
    const found = filterQuestions(
      golden,
      columns,
      { ...NO_FILTERS, suite: 'chinook', tier: 'hard' },
      'golden',
    )

    expect(found).toHaveLength(4)
  })

  it('failures only keeps exactly the questions some run failed', () => {
    const found = filterQuestions(golden, columns, { ...NO_FILTERS, failuresOnly: true }, 'golden')

    expect(found.map(({ id }) => id).toSorted()).toEqual(
      failingQuestions(data)
        .filter(({ study }) => study === 'golden')
        .map(({ id }) => id)
        .toSorted(),
    )
    expect(found.length).toBeGreaterThan(0)
    expect(found.length).toBeLessThan(golden.length)
  })
})

describe('defaultColumn', () => {
  const columns = runColumns(data, 'golden')

  it('opens a question at the first run that failed it', () => {
    expect(defaultColumn(columns, 'b01_key_account_count', 'golden')?.label).toBe('Arm A')
  })

  it('opens a question nobody failed at the first run that has it', () => {
    const passedByAll = questionsOf(data, 'golden').find(
      ({ id }) => !failingQuestions(data).some((failing) => failing.id === id),
    )

    expect(passedByAll && defaultColumn(columns, passedByAll.id, 'golden')?.label).toBe('Arm A')
  })
})

it('builds links that survive any id or run name', () => {
  expect(questionHref('a b')).toBe('/evals/questions/a%20b')
  expect(questionHref('q', 'Arm C, 4 workers')).toBe(
    '/evals/questions/q?run=Arm%20C%2C%204%20workers',
  )
})
