import type { AblationArm, AblationStudy, EvalScore } from '@/api/evals'

import { percentText, scoreText } from './format'

export type WaterfallRow = {
  arm: AblationArm
  score: EvalScore
  /** Where the bar's change starts and ends, as a percentage of the questions scored. */
  from: number
  to: number
  /** In words: the first arm has no change to report. */
  change: 'first' | 'none' | 'gain' | 'loss'
  withinNoise: boolean
}

const percent = ({ passed, total }: EvalScore) => (total ? (100 * passed) / total : 0)

/** The score this study is judged by: BIRD's official rule for BIRD, our match for our own sets. */
export const headlineScore = (arm: AblationArm, study: AblationStudy) =>
  study.headline === 'ex' ? arm.ex : arm.match

export const headlineName = (study: AblationStudy) =>
  study.headline === 'ex' ? 'BIRD EX' : 'QueryMind match'

/** Two runs of one setup differ by about `noise_questions`, so a smaller change may not be real. */
function isWithinNoise(change: number, score: EvalScore, noiseQuestions: number) {
  return score.total > 0 && Math.abs(change) <= (100 * noiseQuestions) / score.total
}

/** One row per arm that has run, each starting where the arm before it ended. */
export function waterfallRows(arms: AblationArm[], study: AblationStudy): WaterfallRow[] {
  const rows: WaterfallRow[] = []
  for (const arm of arms) {
    const score = headlineScore(arm, study)
    if (!score) continue
    const previous = rows.at(-1)
    const to = percent(score)
    const change = arm.delta_points
    rows.push({
      arm,
      score,
      from: previous?.to ?? 0,
      to,
      change: !previous ? 'first' : !change ? 'none' : change > 0 ? 'gain' : 'loss',
      withinNoise:
        change !== null && change !== 0 && isWithinNoise(change, score, study.noise_questions),
    })
  }
  return rows
}

/** "+20.7 pts", "no change", or nothing for the first arm: the same text as the report's column. */
export function changeText({ change, arm }: WaterfallRow): string {
  if (change === 'first' || arm.delta_points === null) return ''
  if (change === 'none') return 'no change'
  return `${arm.delta_points > 0 ? '+' : ''}${arm.delta_points.toFixed(1)} pts`
}

/** The chart in one sentence. */
export function describeWaterfall(rows: WaterfallRow[]): string {
  return rows
    .map((row) => {
      const score = `${scoreText(row.score)} (${percentText(row.score)})`
      const change = changeText(row)
      const noise = row.withinNoise ? ', within noise' : ''
      return `Arm ${row.arm.key} ${score}${change ? `, ${change}${noise}` : ''}`
    })
    .join('; ')
}

/** Arms are scored on different questions when one lost some: their percentages compare less well. */
export function differentQuestions(rows: WaterfallRow[]): boolean {
  return new Set(rows.map(({ score }) => score.total)).size > 1
}

/** What the last arm costs against the one before, when that's a real difference. */
export function tokenChange(rows: WaterfallRow[]): string | null {
  const [before, last] = rows.slice(-2).map(({ arm }) => arm)
  if (!before?.input_tokens || !last?.input_tokens) return null
  const change = 1 - last.input_tokens / before.input_tokens
  if (Math.abs(change) < 0.2) return null
  const size = `${Math.round(Math.abs(change) * 100)}%`
  return `Arm ${last.key} reads ${size} ${change > 0 ? 'fewer' : 'more'} input tokens per question than arm ${before.key} (${last.input_tokens.toLocaleString('en-US')} against ${before.input_tokens.toLocaleString('en-US')}).`
}
