import type { AblationArm, AblationStudy, EvalScore, EvalsReply } from '@/api/evals'

/** A headline number with everything needed to judge it: what it counted, and where it's from. */
export type Tile = {
  title: string
  value: string
  caption: string
  details: string[]
  sources: string[]
  date: string
  model: string
  link?: { label: string; href: string }
}

export const scoreText = ({ passed, total }: EvalScore) => `${passed}/${total}`

export const percentText = ({ passed, total }: EvalScore) =>
  total ? `${((100 * passed) / total).toFixed(1)}%` : 'none scored'

/** As in the reports (evals/models.py): small amounts need more digits to mean anything. */
export function dollars(value: number | null | undefined): string {
  if (value === null || value === undefined) return 'no price'
  return value < 0.01 ? `$${value.toFixed(5)}` : `$${value.toFixed(2)}`
}

/** "2026-10-24 19:45 UTC" → "2026-10-24". */
export const dayOf = (date: string) => date.slice(0, 10)

/** The newest date among some reports, as a day. */
function newestDay(data: EvalsReply, reports: string[]): string {
  const days = data.runs.filter(({ key }) => reports.includes(key)).map(({ date }) => dayOf(date))
  return days.sort().at(-1) ?? ''
}

/** The saved results' newest date: the page isn't live, so it says how old it is. */
export function newestRun(data: EvalsReply): string {
  return (
    data.runs
      .map(({ date }) => dayOf(date))
      .sort()
      .at(-1) ?? ''
  )
}

/** The last arm that has really run: the fullest agent so far. */
export function lastArmRun(study: AblationStudy): AblationArm | undefined {
  return study.views[0]?.arms.findLast(({ match }) => match !== null)
}

const study = (data: EvalsReply, name: string) => data.ablations.find((s) => s.name === name)

function goldenTile(data: EvalsReply): Tile | null {
  const golden = study(data, 'golden')
  const arm = golden && lastArmRun(golden)
  if (!golden || !arm?.match) return null
  const all = arm.match.total + arm.not_scored
  return {
    title: `Golden ${all}`,
    value: scoreText(arm.match),
    caption: `Correct answers, arm ${arm.key}: the full agent`,
    details:
      arm.not_scored > 0
        ? [
            `${arm.not_scored} of ${all} questions weren't scored, so this isn't a score out of ${all}.`,
          ]
        : [],
    sources: arm.reports,
    date: newestDay(data, arm.reports),
    model: golden.llm ?? 'unknown',
  }
}

function birdTile(data: EvalsReply): Tile | null {
  const bird = study(data, 'bird')
  const arm = bird && lastArmRun(bird)
  if (!bird || !arm?.ex) return null
  const second = data.runs.find((run) => run.study === 'bird' && !run.latency_valid)
  return {
    title: `BIRD-${arm.ex.total}`,
    value: scoreText(arm.ex),
    caption: `BIRD's official EX, on a fixed subset of ${arm.ex.total} of its 500 questions`,
    details: [
      ...(second
        ? [
            `The same setup run again: ${scoreText(second.ex)}. Two runs differ by about ${bird.noise_questions} questions.`,
          ]
        : []),
      data.bird.version_note,
    ],
    sources: arm.reports,
    date: newestDay(data, arm.reports),
    model: bird.llm ?? 'unknown',
    link: { label: `BIRD's data (${data.bird.licence})`, href: data.bird.url },
  }
}

function gateTile({ gate }: EvalsReply): Tile {
  return {
    title: 'CI gate baseline',
    value: `${gate.passed}/${gate.questions}`,
    caption: `Fails a change when more than ${gate.allowed_regressions} of these starts failing`,
    details: gate.failure_proven ? [] : [gate.note],
    sources: ['evals/ci_baseline.json'],
    date: gate.baseline_date,
    model: gate.llm,
    link: { label: 'Live status on GitHub', href: gate.actions_url },
  }
}

function cheapestTile(data: EvalsReply): Tile | null {
  const golden = data.models.studies.find(({ name }) => name === 'golden')
  const priced = (golden?.rows ?? []).filter((row) => row.cost_per_correct !== null)
  const cheapest = priced.reduce<(typeof priced)[number] | null>(
    (best, row) =>
      best === null || (row.cost_per_correct ?? 0) < (best.cost_per_correct ?? 0) ? row : best,
    null,
  )
  if (!golden || !cheapest) return null
  const reports = data.runs
    .filter(({ key, llm }) => golden.reports.includes(key) && llm === cheapest.llm)
    .map(({ key }) => key)
  return {
    title: 'Cheapest per correct answer',
    value: dollars(cheapest.cost_per_correct),
    caption: `${cheapest.passed}/${cheapest.questions} correct on the questions every model answered`,
    details: [
      `List price, prices checked ${data.models.prices_checked}. Our runs used free tiers.`,
      ...(golden.partial_note
        ? [`Not every model has run every suite: ${golden.partial_note}.`]
        : []),
    ],
    sources: reports,
    date: newestDay(data, reports),
    model: cheapest.llm,
  }
}

/** The four numbers at the top of the evals lab. */
export function headlineTiles(data: EvalsReply): Tile[] {
  return [goldenTile(data), birdTile(data), gateTile(data), cheapestTile(data)].filter(
    (tile) => tile !== null,
  )
}
