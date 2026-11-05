import type { ModelRow, ModelStudy } from '@/api/evals'

import { dollars } from './format'

export type ScatterPoint = {
  name: string
  accuracy: number
  cost: number
  passed: number
  questions: number
}

/** "groq / openai/gpt-oss-20b" → "gpt-oss-20b": short enough to sit beside a point on a phone. */
export const shortModel = (llm: string) => llm.split('/').at(-1)?.trim() ?? llm

const accuracyOf = ({ passed, questions }: ModelRow) => (questions ? (100 * passed) / questions : 0)

/** Models with a cost per correct answer. A log axis can't place the others, so they're named. */
export function scatterPoints(study: ModelStudy): ScatterPoint[] {
  return study.rows.flatMap((row) =>
    row.cost_per_correct
      ? [
          {
            name: shortModel(row.llm),
            accuracy: accuracyOf(row),
            cost: row.cost_per_correct,
            passed: row.passed,
            questions: row.questions,
          },
        ]
      : [],
  )
}

export const unpricedModels = (study: ModelStudy) =>
  study.rows.filter((row) => !row.cost_per_correct).map((row) => shortModel(row.llm))

const LOG_STEPS = [1, 2, 5]

/** Round numbers for a log axis (…0.0005, 0.001, 0.002…), with room around the outer points. */
export function logTicks(low: number, high: number): number[] {
  const ticks: number[] = []
  for (let power = Math.floor(Math.log10(low)) - 1; power <= Math.ceil(Math.log10(high)); power++) {
    for (const step of LOG_STEPS) ticks.push(Number((step * 10 ** power).toPrecision(1)))
  }
  const start = ticks.findLast((tick) => tick <= low / 1.5) ?? ticks[0] ?? low
  const end = ticks.find((tick) => tick >= high * 1.5) ?? ticks.at(-1) ?? high
  return ticks.filter((tick) => tick >= start && tick <= end)
}

/** The accuracy axis's start: a round number just under the lowest point. */
export const accuracyFloor = (points: ScatterPoint[]) =>
  Math.max(0, Math.floor((Math.min(...points.map(({ accuracy }) => accuracy)) - 1) / 5) * 5)

/** Even steps from the floor to 100%, so no gridline lands by accident on a point's label. */
export function accuracyTicks(floor: number): number[] {
  const step = 100 - floor <= 20 ? 5 : 10
  const ticks: number[] = []
  for (let tick = 100; tick >= floor; tick -= step) ticks.unshift(tick)
  return ticks
}

const pointText = ({ name, passed, questions, accuracy, cost }: ScatterPoint) =>
  `${name}: ${passed}/${questions} correct (${accuracy.toFixed(1)}%), ${dollars(cost)} per correct answer`

export const describeScatter = (points: ScatterPoint[]) =>
  `Scatter chart of ${points.length} models, accuracy against dollars per correct answer. ${points.map(pointText).join('. ')}.`

// Below this, a cost gap could come from a few tokens more or less on one run.
const CLEAR_COST_GAP = 2

/** What the gaps between the points are worth, from the data: never a fixed sentence. */
export function gapNote(points: ScatterPoint[], noiseQuestions: number): string {
  const passed = points.map((point) => point.passed)
  const costs = points.map(({ cost }) => cost)
  const gap = Math.max(...passed) - Math.min(...passed)
  const ratio = Math.max(...costs) / Math.min(...costs)
  const questions = points[0]?.questions ?? 0
  const accuracy = `The accuracy gap is ${gap} of ${questions} questions, ${
    gap <= noiseQuestions ? 'inside the noise' : 'more than the noise'
  }: two runs of one setup differ by about ${noiseQuestions}.`
  return ratio >= CLEAR_COST_GAP
    ? `${accuracy} The cost gap is about ${Math.round(ratio)} times, which noise can't explain: it comes from list prices and the tokens each model used.`
    : accuracy
}

/** The longest time in a study: every speed bar shares this one scale. */
export const slowest = (rows: ModelRow[]) =>
  Math.max(...rows.map(({ p95_seconds }) => p95_seconds ?? 0), 0)
