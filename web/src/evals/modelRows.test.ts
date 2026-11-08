import { describe, expect, it } from 'vitest'

import type { ModelRow, ModelStudy } from '@/api/evals'
import { evals } from '@/mocks/fixtures'

import {
  accuracyFloor,
  accuracyTicks,
  describeScatter,
  gapNote,
  logTicks,
  scatterPoints,
  shortModel,
  slowest,
  unpricedModels,
  type ScatterPoint,
} from './modelRows'

const golden = evals.models.studies.find(({ name }) => name === 'golden') as ModelStudy
const point = (name: string, passed: number, cost: number): ScatterPoint => ({
  name,
  passed,
  cost,
  questions: 43,
  accuracy: (100 * passed) / 43,
})

describe('shortModel', () => {
  it.each([
    ['groq / openai/gpt-oss-20b', 'gpt-oss-20b'],
    ['groq / qwen/qwen3.8-27b', 'qwen3.8-27b'],
    ['gemini / gemini-3.5-flash-lite', 'gemini-3.5-flash-lite'],
  ])('%s becomes %s', (llm, short) => {
    expect(shortModel(llm)).toBe(short)
  })
})

describe('scatterPoints', () => {
  it('gives one point per priced model', () => {
    const points = scatterPoints(golden)

    expect(points.map(({ name }) => name)).toEqual([
      'gemini-3.5-flash-lite',
      'qwen3.8-27b',
      'gpt-oss-20b',
      'gpt-oss-120b',
    ])
    expect(points[2]?.cost).toBe(0.00040774)
    expect(points[2]?.accuracy).toBeCloseTo(95.35, 1)
  })

  it('leaves out and names a model with no cost, which a log axis cannot place', () => {
    const rows = golden.rows.map((row, index): ModelRow =>
      index === 0 ? { ...row, cost_per_correct: null } : row,
    )
    const study = { ...golden, rows }

    expect(scatterPoints(study)).toHaveLength(3)
    expect(unpricedModels(study)).toEqual(['gemini-3.5-flash-lite'])
  })
})

describe('logTicks', () => {
  it('uses round numbers, with room around the outer points', () => {
    expect(logTicks(0.00041, 0.00415)).toEqual([0.0002, 0.0005, 0.001, 0.002, 0.005, 0.01])
  })

  it('works when both points are close together', () => {
    const ticks = logTicks(0.001, 0.0012)

    expect(ticks[0]).toBeLessThan(0.001)
    expect(ticks.at(-1)).toBeGreaterThan(0.0012)
  })
})

describe('accuracyFloor', () => {
  it('is a round number under the lowest point', () => {
    expect(accuracyFloor([point('a', 41, 1), point('b', 43, 1)])).toBe(90)
  })

  it('never goes below zero', () => {
    expect(accuracyFloor([point('a', 0, 1)])).toBe(0)
  })
})

describe('accuracyTicks', () => {
  it.each([
    [90, [90, 95, 100]],
    [50, [50, 60, 70, 80, 90, 100]],
  ])('from %i goes up in even steps', (floor, ticks) => {
    expect(accuracyTicks(floor)).toEqual(ticks)
  })
})

describe('gapNote', () => {
  it('on the real runs: a 3-question gap is just over the noise, the cost gap far over it', () => {
    const note = gapNote(scatterPoints(golden), 2)

    expect(note).toContain('The accuracy gap is 3 of 43 questions, more than the noise')
    expect(note).toContain('The cost gap is about 10 times')
  })

  it('says so when the accuracy gap is inside the noise', () => {
    const note = gapNote([point('a', 41, 0.0004), point('b', 43, 0.004)], 2)

    expect(note).toContain('The accuracy gap is 2 of 43 questions, inside the noise')
  })

  it('says so when the accuracy gap is more than the noise', () => {
    const note = gapNote([point('a', 30, 0.001), point('b', 43, 0.004)], 2)

    expect(note).toContain('13 of 43 questions, more than the noise')
  })

  it('claims nothing about a small cost gap', () => {
    expect(gapNote([point('a', 42, 0.001), point('b', 43, 0.0011)], 2)).not.toContain('cost gap')
  })
})

it('describes the chart with every point, for a screen reader', () => {
  const text = describeScatter(scatterPoints(golden))

  expect(text).toContain('Scatter chart of 4 models')
  expect(text).toContain('gpt-oss-20b: 41/43 correct (95.3%), $0.00041 per correct answer')
})

it('finds the slowest time, the one scale every speed bar shares', () => {
  expect(slowest(golden.rows)).toBe(29.4)
})
