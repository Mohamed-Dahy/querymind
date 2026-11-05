import { describe, expect, it } from 'vitest'

import type { EvalsReply } from '@/api/evals'
import { evals } from '@/mocks/fixtures'

import {
  dayOf,
  dollars,
  headlineTiles,
  lastArmRun,
  newestRun,
  percentText,
  scoreText,
} from './format'

const tile = (data: EvalsReply, title: RegExp) => {
  const found = headlineTiles(data).find((t) => title.test(t.title))
  if (!found) throw new Error(`no tile matching ${title}`)
  return found
}

describe('numbers as text', () => {
  it('writes scores the way the reports do', () => {
    expect(scoreText({ passed: 45, total: 58 })).toBe('45/58')
    expect(percentText({ passed: 45, total: 58 })).toBe('77.6%')
    expect(percentText({ passed: 0, total: 0 })).toBe('none scored')
  })

  it('writes dollars the way the reports do', () => {
    expect(dollars(0.00040774)).toBe('$0.00041')
    expect(dollars(4.06)).toBe('$4.06')
    expect(dollars(null)).toBe('no price')
  })

  it('keeps only the day of a date', () => {
    expect(dayOf('2026-10-24 19:45 UTC')).toBe('2026-10-24')
  })
})

// These strings are copied from docs/eval-reports/phase4-ablations.md and phase4-models.md.
// tests/unit/test_eval_snapshot.py checks the file against those reports.
describe('the headline tiles match the reports', () => {
  it('golden says 53/53 and that 5 of 58 were not scored', () => {
    const golden = tile(evals, /^Golden/)

    expect(golden.title).toBe('Golden 58')
    expect(golden.value).toBe('53/53')
    expect(golden.details.join(' ')).toContain("5 of 58 questions weren't scored")
    expect(golden.model).toBe('gemini / gemini-3.5-flash-lite')
    expect(golden.sources).toContain('phase4-ablation-golden-D-chinook')
    expect(golden.date).toBe('2026-10-24')
  })

  it('BIRD says EX 57/100, the subset size, and the second run', () => {
    const bird = tile(evals, /^BIRD/)

    expect(bird.title).toBe('BIRD-100')
    expect(bird.value).toBe('57/100')
    expect(bird.caption).toContain('subset of 100')
    expect(bird.details[0]).toContain('60/100')
    expect(bird.details[0]).toContain('about 3 questions')
    expect(bird.sources).toEqual(['phase4-bird-100-seq'])
    expect(bird.link?.label).toContain('CC BY-SA 4.0')
  })

  it('the gate shows its baseline and says it has not been seen failing', () => {
    const gate = tile(evals, /^CI gate/)

    expect(gate.value).toBe('12/12')
    expect(gate.caption).toContain('more than 1')
    expect(gate.model).toBe('groq / openai/gpt-oss-20b')
    expect(gate.details.join(' ')).toContain("hasn't been seen failing")
    expect(gate.link?.href).toContain('/actions/workflows/evals.yml')
  })

  it('the gate stops warning once a failure has been seen', () => {
    const proven = { ...evals, gate: { ...evals.gate, failure_proven: true } }

    expect(tile(proven, /^CI gate/).details).toEqual([])
  })

  it('the cheapest model is gpt-oss-20b at $0.00041, on 43 questions', () => {
    const cheapest = tile(evals, /^Cheapest/)

    expect(cheapest.value).toBe('$0.00041')
    expect(cheapest.model).toBe('groq / openai/gpt-oss-20b')
    expect(cheapest.caption).toContain('41/43')
    expect(cheapest.sources).toEqual([
      'phase4-models-gpt-oss-20b-business',
      'phase4-models-gpt-oss-20b-golden',
    ])
    expect(cheapest.details.join(' ')).toContain('so far only golden, business')
    expect(cheapest.details.join(' ')).toContain('2026-10-25')
  })

  it('every tile names its report, date and model', () => {
    for (const { sources, date, model } of headlineTiles(evals)) {
      expect(sources.length).toBeGreaterThan(0)
      expect(date).toMatch(/^\d{4}-\d{2}-\d{2}$/)
      expect(model).not.toBe('unknown')
    }
  })
})

describe('when something has not run', () => {
  it('takes the last arm that ran, not the last arm', () => {
    const bird = evals.ablations.find(({ name }) => name === 'bird')

    expect(bird && lastArmRun(bird)?.key).toBe('C')
  })

  it('leaves a tile out rather than showing a zero', () => {
    const none = {
      ...evals,
      ablations: evals.ablations.map((study) => ({
        ...study,
        views: study.views.map((view) => ({
          ...view,
          arms: view.arms.map((arm) => ({ ...arm, match: null, ex: null })),
        })),
      })),
    }

    expect(headlineTiles(none).map(({ title }) => title)).not.toContain('Golden 58')
    expect(headlineTiles(none).map(({ title }) => title)).toContain('CI gate baseline')
  })

  it('knows how old the results are', () => {
    expect(newestRun(evals)).toBe('2026-10-25')
  })
})
