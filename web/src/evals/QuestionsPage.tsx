import { Link, useSearchParams } from 'react-router'

import type { EvalQuestion, EvalsReply } from '@/api/evals'
import { cn } from '@/lib/utils'

import { EvalsFrame } from './EvalsFrame'
import { scoreText } from './format'
import {
  columnScore,
  filterOptions,
  filterQuestions,
  questionHref,
  questionsOf,
  questionTitle,
  runColumns,
  VERDICT_WORDS,
  verdictIn,
  type RunColumn,
  type Study,
  type Verdict,
} from './questionRows'
import { VerdictMark } from './VerdictMark'

const FILTERS_CLASS = 'inline-flex min-h-11 items-center rounded-lg border px-3 text-sm'
const SUITE_NAMES: Record<string, string> = {
  golden: 'Northwind',
  business: 'Business terms',
  chinook: 'Chinook',
  bird: 'BIRD',
}

/** Every question against every run: where to look for a failure and see why it failed. */
export default function QuestionsPage() {
  return (
    <EvalsFrame
      title="Every question"
      intro="Each question against each run. Open a question for its SQL, the reference and why it failed."
    >
      {(data) => <Matrix data={data} />}
    </EvalsFrame>
  )
}

function Matrix({ data }: { data: EvalsReply }) {
  const [params, setParams] = useSearchParams()
  const study: Study = params.get('study') === 'bird' ? 'bird' : 'golden'
  const questions = questionsOf(data, study)
  const columns = runColumns(data, study)
  const options = filterOptions(questions)
  // A value from an old or mistyped link that isn't an option is ignored, not an empty table.
  const chosen = (name: string, from: string[]) => {
    const value = params.get(name)
    return value !== null && from.includes(value) ? value : null
  }
  const filters = {
    suite: chosen('suite', options.suites),
    tier: chosen('tier', options.tiers),
    database: chosen('database', options.databases),
    failuresOnly: params.get('failures') === '1',
  }
  const shown = filterQuestions(questions, columns, filters, study)

  const change = (name: string, value: string | null) =>
    setParams(
      (previous) => {
        const next = new URLSearchParams(previous)
        if (value === null || value === '') next.delete(name)
        else next.set(name, value)
        return next
      },
      { replace: true },
    )
  const base = study === 'bird' ? '/evals/questions?study=bird' : '/evals/questions'

  return (
    <div className="space-y-4">
      <nav aria-label="Which study" className="flex flex-wrap gap-2">
        {(['golden', 'bird'] as const).map((name) => {
          const count = questionsOf(data, name).length
          return (
            <Link
              key={name}
              to={name === 'bird' ? '/evals/questions?study=bird' : '/evals/questions'}
              aria-current={name === study ? 'true' : undefined}
              className={cn(
                FILTERS_CLASS,
                name === study ? 'border-input bg-muted font-semibold' : 'hover:bg-muted',
              )}
            >
              {name === 'bird' ? `BIRD-${count}` : `Golden ${count}`}
            </Link>
          )
        })}
      </nav>

      <form
        aria-label="Filter the questions"
        className="flex flex-wrap items-end gap-3"
        onSubmit={(event) => event.preventDefault()}
      >
        <FilterSelect
          name="suite"
          label="Suite"
          value={filters.suite}
          options={options.suites}
          words={SUITE_NAMES}
          onChange={change}
        />
        <FilterSelect
          name="tier"
          label="Tier"
          value={filters.tier}
          options={options.tiers}
          onChange={change}
        />
        <FilterSelect
          name="database"
          label="Database"
          value={filters.database}
          options={options.databases}
          onChange={change}
        />
        <label className="inline-flex min-h-11 items-center gap-2 text-sm">
          <input
            type="checkbox"
            className="size-4"
            checked={filters.failuresOnly}
            onChange={(event) => change('failures', event.target.checked ? '1' : null)}
          />
          Failures only
        </label>
      </form>

      <Legend study={study} />
      <p role="status" className="text-sm">
        Showing {shown.length} of {questions.length} questions.
      </p>

      {shown.length > 0 ? (
        <MatrixTable study={study} columns={columns} questions={shown} />
      ) : (
        <p className="rounded-cell border bg-surface p-4 text-sm">
          No question matches these filters.{' '}
          <Link to={base} className="text-info underline underline-offset-4">
            Clear the filters
          </Link>
        </p>
      )}
    </div>
  )
}

type FilterSelectProps = {
  name: string
  label: string
  value: string | null
  options: string[]
  words?: Record<string, string>
  onChange: (name: string, value: string | null) => void
}

function FilterSelect({ name, label, value, options, words, onChange }: FilterSelectProps) {
  // With one choice there is nothing to filter by.
  if (options.length < 2) return null
  return (
    <label className="grid gap-1 text-sm">
      {label}
      <select
        value={value ?? ''}
        onChange={(event) => onChange(name, event.target.value)}
        className="min-h-11 rounded-lg border border-input bg-surface px-2"
      >
        <option value="">All</option>
        {options.map((option) => (
          <option key={option} value={option}>
            {words?.[option] ?? option}
          </option>
        ))}
      </select>
    </label>
  )
}

const LEGEND: Verdict[] = ['passed', 'failed', 'not-scored', 'not-run']

function Legend({ study }: { study: Study }) {
  return (
    <div className="text-sm">
      <ul aria-label="What the marks mean" className="flex flex-wrap gap-x-4 gap-y-1">
        {LEGEND.map((verdict) => (
          <li key={verdict} className="inline-flex items-center gap-1.5">
            <span className="inline-flex size-4 items-center justify-center">
              <VerdictMark verdict={verdict} />
            </span>
            {VERDICT_WORDS[verdict]}
          </li>
        ))}
      </ul>
      <p className="mt-1 text-xs text-fg-muted">
        Judged by {study === 'bird' ? "BIRD's official EX" : 'QueryMind match'}. Not scored: the
        provider stayed unavailable, so there is no verdict. The count under each run covers the
        whole study, whatever the filters show.
      </p>
    </div>
  )
}

type MatrixTableProps = { study: Study; columns: RunColumn[]; questions: EvalQuestion[] }

function MatrixTable({ study, columns, questions }: MatrixTableProps) {
  return (
    <div
      tabIndex={0}
      role="region"
      aria-label="Questions against runs"
      className="relative max-h-[75vh] overflow-auto rounded-cell border bg-surface"
    >
      <table className="w-full border-separate border-spacing-0 text-sm">
        <caption className="sr-only">
          Each question&apos;s verdict in each run. The first column links to the question.
        </caption>
        <thead>
          <tr>
            <th
              scope="col"
              className="sticky top-0 z-20 min-w-56 border-b bg-surface px-3 py-2 text-left align-bottom md:left-0"
            >
              Question
            </th>
            {columns.map((column) => (
              <th
                key={column.label}
                scope="col"
                className="sticky top-0 z-10 min-w-20 border-b bg-surface px-2 py-2 text-center align-bottom font-normal"
              >
                <span className="block font-semibold">{column.label}</span>
                <span className="block text-xs text-fg-muted tabular-nums">
                  {scoreText(columnScore(column, study))}
                </span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {questions.map((question) => (
            <tr key={question.id}>
              <th
                scope="row"
                className="border-b bg-surface px-3 py-2 text-left align-top font-normal md:sticky md:left-0"
              >
                {/* The row's one Tab stop: hundreds of cells to tab through would help nobody. */}
                <Link
                  to={questionHref(question.id)}
                  className="text-info underline underline-offset-4"
                >
                  {questionTitle(question)}
                </Link>
                <span className="mt-0.5 block text-xs break-all text-fg-muted">
                  {question.question ? `${question.id} · ` : ''}
                  {question.tier}
                </span>
              </th>
              {columns.map((column) => {
                const verdict = verdictIn(column, question.id, study)
                return (
                  <td key={column.label} className="border-b p-0 align-middle">
                    <Link
                      to={questionHref(question.id, column.label)}
                      tabIndex={-1}
                      className="flex min-h-11 items-center justify-center hover:bg-muted"
                    >
                      <VerdictMark verdict={verdict} />
                      <span className="sr-only">
                        {column.label}: {VERDICT_WORDS[verdict]}
                      </span>
                    </Link>
                  </td>
                )
              })}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
