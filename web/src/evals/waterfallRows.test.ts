import { describe, expect, it } from 'vitest'

import type { AblationStudy } from '@/api/evals'
import { evals } from '@/mocks/fixtures'

import {
  changeText,
  describeWaterfall,
  differentQuestions,
  headlineName,
  tokenChange,
  waterfallRows,
} from './waterfallRows'

const study = (name: string): AblationStudy => {
  const found = evals.ablations.find((s) => s.name === name)
  if (!found) throw new Error(name)
  return found
}
const golden = study('golden')
const bird = study('bird')
const armsOf = (s: AblationStudy, scope = 'all') =>
  s.views.find((view) => view.scope === scope)?.arms ?? []

// The numbers are copied from docs/eval-reports/phase4-ablations.md.
describe('the golden 58 waterfall', () => {
  const rows = waterfallRows(armsOf(golden), golden)

  it('says each change the way the report does', () => {
    expect(rows.map(({ arm }) => arm.key)).toEqual(['A', 'B', 'C', 'D'])
    expect(rows.map(changeText)).toEqual(['', 'no change', '+20.7 pts', '+1.7 pts'])
  })

  it('starts each bar where the one before it ended', () => {
    expect(rows[0]?.from).toBe(0)
    expect(rows[1]?.from).toBeCloseTo(rows[0]?.to ?? -1)
    expect(rows[2]?.from).toBeCloseTo(77.6, 1)
    expect(rows[2]?.to).toBeCloseTo(98.3, 1)
  })

  it('marks a small gain as within noise, and a big one not', () => {
    expect(rows.map(({ withinNoise }) => withinNoise)).toEqual([false, false, false, true])
  })

  it('has no warning when every arm scored every question', () => {
    expect(differentQuestions(rows)).toBe(false)
  })

  it('warns when the arms were scored on different questions', () => {
    const [a, b, c, d] = armsOf(golden)
    const short = { ...d!, match: { passed: 53, total: 53 }, not_scored: 5 }

    expect(differentQuestions(waterfallRows([a!, b!, c!, short], golden))).toBe(true)
  })

  it('reads as one sentence', () => {
    expect(describeWaterfall(rows)).toBe(
      'Arm A 45/58 (77.6%); Arm B 45/58 (77.6%), no change; Arm C 57/58 (98.3%), +20.7 pts; Arm D 58/58 (100.0%), +1.7 pts, within noise',
    )
  })

  it('says what the last arm saves', () => {
    expect(tokenChange(rows)).toBe(
      'Arm D reads 49% fewer input tokens per question than arm C (3,454 against 6,724).',
    )
  })

  it('uses our own match as its headline', () => {
    expect(headlineName(golden)).toBe('QueryMind match')
  })
})

describe('other views', () => {
  it('business terms: the semantic layer is the whole story', () => {
    const rows = waterfallRows(armsOf(golden, 'business'), golden)

    expect(rows.map(({ score }) => `${score.passed}/${score.total}`)).toEqual([
      '6/13',
      '6/13',
      '13/13',
      '13/13',
    ])
    expect(rows[2]?.withinNoise).toBe(false)
    expect(differentQuestions(rows)).toBe(false)
  })

  it('BIRD uses EX: the hints are the big step, self-correction adds nothing', () => {
    const rows = waterfallRows(armsOf(bird), bird)

    expect(headlineName(bird)).toBe('BIRD EX')
    expect(rows.map(({ arm }) => arm.key)).toEqual(['A', 'B', 'C'])
    expect(rows.map(({ score }) => score.passed)).toEqual([40, 57, 57])
    expect(rows.map(changeText)).toEqual(['', '+17.0 pts', 'no change'])
    expect(tokenChange(rows)).toBeNull()
  })

  it('arms that never ran have no bar and no zero', () => {
    const [a, b, c] = armsOf(bird)
    const notRun = (arm: typeof a) => ({ ...arm!, match: null, ex: null, missing: ['bird'] })
    const rows = waterfallRows([notRun(a), notRun(b), c!], bird)

    expect(rows.map(({ arm }) => arm.key)).toEqual(['C'])
    expect(rows[0]?.score).toEqual({ passed: 57, total: 100 })
    expect(changeText(rows[0]!)).toBe('')
  })
})

describe('a loss', () => {
  it('says so with its sign', () => {
    const [first, second] = armsOf(golden)
    const worse = {
      ...second!,
      match: { passed: 40, total: 58 },
      delta_points: -8.6,
    }
    const rows = waterfallRows([first!, worse], golden)

    expect(rows[1]?.change).toBe('loss')
    expect(changeText(rows[1]!)).toBe('-8.6 pts')
    expect(rows[1]?.withinNoise).toBe(false)
  })
})
