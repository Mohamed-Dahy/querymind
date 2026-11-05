import type { EvalOutcome, EvalQuestion, EvalRun, EvalScore, EvalsReply } from '@/api/evals'

export type Study = EvalQuestion['study']
export type Verdict = 'passed' | 'failed' | 'not-scored' | 'not-run'

export const VERDICT_WORDS: Record<Verdict, string> = {
  passed: 'Passed',
  failed: 'Failed',
  'not-scored': 'Not scored',
  'not-run': 'Not run',
}

/** One column of the matrix: a run across its suites (arm C's golden, business and Chinook). */
export type RunColumn = {
  label: string
  llm: string
  outcomes: Map<string, { outcome: EvalOutcome; run: EvalRun }>
}

export type Filters = {
  suite: string | null
  tier: string | null
  database: string | null
  failuresOnly: boolean
}

export const NO_FILTERS: Filters = { suite: null, tier: null, database: null, failuresOnly: false }

/** BIRD is judged by its official rule, our own sets by our match: as on the other evals views. */
export function verdictOf(outcome: EvalOutcome | undefined, study: Study): Verdict {
  if (!outcome) return 'not-run'
  if (!outcome.scored) return 'not-scored'
  return (study === 'bird' ? outcome.ex : outcome.passed) ? 'passed' : 'failed'
}

export const questionsOf = (data: EvalsReply, study: Study) =>
  data.questions.filter((question) => question.study === study)

/** The study's runs as columns, in the order the runs come. */
export function runColumns(data: EvalsReply, study: Study): RunColumn[] {
  const columns = new Map<string, RunColumn>()
  for (const run of data.runs) {
    if (run.study !== study) continue
    const column = columns.get(run.label) ?? { label: run.label, llm: run.llm, outcomes: new Map() }
    for (const outcome of run.outcomes) column.outcomes.set(outcome.question_id, { outcome, run })
    columns.set(run.label, column)
  }
  return [...columns.values()]
}

export const verdictIn = (column: RunColumn, questionId: string, study: Study): Verdict =>
  verdictOf(column.outcomes.get(questionId)?.outcome, study)

/** A column's score over the whole study: what the other evals views say about this run. */
export function columnScore(column: RunColumn, study: Study): EvalScore {
  const verdicts = [...column.outcomes.keys()].map((id) => verdictIn(column, id, study))
  return {
    passed: verdicts.filter((verdict) => verdict === 'passed').length,
    total: verdicts.filter((verdict) => verdict !== 'not-scored').length,
  }
}

/** The matrix names a question by its first line: BIRD's hint follows on the next (api/evals.py). */
export const questionTitle = ({ id, question }: EvalQuestion) => question?.split('\n')[0] ?? id

const TIER_ORDER = ['easy', 'simple', 'medium', 'moderate', 'hard', 'challenging']

const tierRank = (tier: string) => {
  const rank = TIER_ORDER.indexOf(tier)
  return rank === -1 ? TIER_ORDER.length : rank
}

/** What each filter can choose from, in this study. */
export function filterOptions(questions: EvalQuestion[]) {
  const distinct = (values: (string | null)[]) => [
    ...new Set(values.filter((value) => value !== null)),
  ]
  return {
    suites: distinct(questions.map(({ suite }) => suite)),
    tiers: distinct(questions.map(({ tier }) => tier)).sort(
      (a, b) => tierRank(a) - tierRank(b) || a.localeCompare(b),
    ),
    databases: distinct(questions.map(({ database }) => database)).sort(),
  }
}

const hasFailure = (columns: RunColumn[], id: string, study: Study) =>
  columns.some((column) => verdictIn(column, id, study) === 'failed')

export function filterQuestions(
  questions: EvalQuestion[],
  columns: RunColumn[],
  filters: Filters,
  study: Study,
): EvalQuestion[] {
  return questions.filter(
    ({ id, suite, tier, database }) =>
      (filters.suite === null || suite === filters.suite) &&
      (filters.tier === null || tier === filters.tier) &&
      (filters.database === null || database === filters.database) &&
      (!filters.failuresOnly || hasFailure(columns, id, study)),
  )
}

/** The run to open a question at: the first that failed it, since that's what people come for. */
export function defaultColumn(
  columns: RunColumn[],
  questionId: string,
  study: Study,
): RunColumn | undefined {
  return (
    columns.find((column) => verdictIn(column, questionId, study) === 'failed') ??
    columns.find((column) => column.outcomes.has(questionId)) ??
    columns[0]
  )
}

export const questionHref = (id: string, runLabel?: string) =>
  `/evals/questions/${encodeURIComponent(id)}${runLabel ? `?run=${encodeURIComponent(runLabel)}` : ''}`

/** Every question some run failed, across both studies. */
export function failingQuestions(data: EvalsReply): EvalQuestion[] {
  return data.questions.filter((question) =>
    hasFailure(runColumns(data, question.study), question.id, question.study),
  )
}
