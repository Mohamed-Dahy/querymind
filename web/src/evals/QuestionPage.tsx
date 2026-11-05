import { ExternalLink } from 'lucide-react'
import { Link, useParams, useSearchParams } from 'react-router'

import type { EvalOutcome, EvalQuestion, EvalsReply } from '@/api/evals'
import { SqlCode } from '@/canvas/SqlView'
import { cn } from '@/lib/utils'
import { NotFoundPage } from '@/pages/NotFoundPage'

import { EvalsFrame } from './EvalsFrame'
import { dayOf } from './format'
import {
  defaultColumn,
  questionHref,
  runColumns,
  VERDICT_WORDS,
  verdictIn,
  type RunColumn,
  type Study,
} from './questionRows'
import { useEvals } from './useEvals'
import { VerdictMark } from './VerdictMark'

const TEXT = 'px-3 py-2 text-left align-middle'
const NUMBER = 'px-3 py-2 text-right align-middle tabular-nums whitespace-nowrap'
const HEADING = 'text-lg font-semibold'

/** One question: what each run made of it, and why. */
export default function QuestionPage() {
  const { id = '' } = useParams()
  const { data } = useEvals()
  const question = data?.questions.find((candidate) => candidate.id === id)
  if (data && !question) return <NotFoundPage />
  return (
    <EvalsFrame
      title={`Question ${id}`}
      intro="Every run's verdict on this question, and the SQL behind it."
    >
      {(loaded) => (question ? <Detail data={loaded} question={question} /> : null)}
    </EvalsFrame>
  )
}

function Detail({ data, question }: { data: EvalsReply; question: EvalQuestion }) {
  const [params] = useSearchParams()
  const study = question.study
  const columns = runColumns(data, study)
  const chosen =
    columns.find(({ label }) => label === params.get('run')) ??
    defaultColumn(columns, question.id, study)
  return (
    <div className="space-y-8">
      <p>
        <Link
          to={study === 'bird' ? '/evals/questions?study=bird' : '/evals/questions'}
          className="text-sm text-info underline underline-offset-4"
        >
          All questions
        </Link>
      </p>
      <Asked data={data} question={question} />
      <RunsTable study={study} columns={columns} chosen={chosen} question={question} />
      {chosen && <RunDetail study={study} column={chosen} question={question} />}
      <Quality data={data} question={question} />
    </div>
  )
}

function Asked({ data, question }: { data: EvalsReply; question: EvalQuestion }) {
  return (
    <section aria-labelledby="asked">
      <h2 id="asked" className={HEADING}>
        The question
      </h2>
      {question.question ? (
        // BIRD's hint is on its own line, as the agent was given it.
        <p className="mt-1 whitespace-pre-line">{question.question}</p>
      ) : (
        <p className="mt-1 text-sm">
          BIRD&apos;s question text and reference SQL aren&apos;t bundled with this app, because
          they&apos;re under {data.bird.licence}: only their ids are. They appear when BIRD&apos;s
          downloaded file is on the server.{' '}
          <a
            href={data.bird.url}
            target="_blank"
            rel="noreferrer"
            className="inline-flex items-center gap-1 text-info underline underline-offset-4"
          >
            Read them on BIRD&apos;s site
            <ExternalLink aria-hidden="true" className="size-3.5" />
          </a>
        </p>
      )}
      <p className="mt-2 text-sm text-fg-muted">
        Suite {question.suite} · tier {question.tier}
        {/* Our three sets share one database, so naming it would only say the same thing. */}
        {question.study === 'bird' && question.database && ` · database ${question.database}`}
      </p>
    </section>
  )
}

const yesNo = (value: boolean) => (value ? 'Passed' : 'Failed')

type RunsTableProps = {
  study: Study
  columns: RunColumn[]
  chosen: RunColumn | undefined
  question: EvalQuestion
}

function RunsTable({ study, columns, chosen, question }: RunsTableProps) {
  return (
    <section aria-labelledby="every-run">
      <h2 id="every-run" className={HEADING}>
        Every run
      </h2>
      <div
        tabIndex={0}
        role="region"
        aria-label="Every run's verdict"
        className="mt-2 overflow-x-auto rounded-cell border bg-surface"
      >
        <table className="w-full text-sm">
          <caption className="sr-only">What each run made of this question</caption>
          <thead className="text-fg-muted">
            <tr>
              <th scope="col" className={TEXT}>
                Run
              </th>
              <th scope="col" className={TEXT}>
                Verdict
              </th>
              <th scope="col" className={TEXT}>
                QueryMind match
              </th>
              <th scope="col" className={TEXT}>
                BIRD EX
              </th>
              <th scope="col" className={NUMBER}>
                SQL tries
              </th>
              <th scope="col" className={NUMBER}>
                Seconds
              </th>
            </tr>
          </thead>
          <tbody>
            {columns.map((column) => {
              const found = column.outcomes.get(question.id)
              const outcome = found?.outcome
              const verdict = verdictIn(column, question.id, study)
              return (
                <tr key={column.label} className={cn('border-t', column === chosen && 'bg-muted')}>
                  <th scope="row" className={`${TEXT} font-normal whitespace-nowrap`}>
                    <Link
                      to={questionHref(question.id, column.label)}
                      aria-current={column === chosen ? 'true' : undefined}
                      className="inline-flex min-h-11 items-center text-info underline underline-offset-4"
                    >
                      {column.label}
                    </Link>
                  </th>
                  <td className={TEXT}>
                    <span className="inline-flex min-h-11 items-center gap-1.5 whitespace-nowrap">
                      <span className="inline-flex size-4 items-center justify-center">
                        <VerdictMark verdict={verdict} />
                      </span>
                      {VERDICT_WORDS[verdict]}
                    </span>
                  </td>
                  <td className={TEXT}>{outcome?.scored ? yesNo(outcome.passed) : ''}</td>
                  <td className={TEXT}>{outcome?.scored ? yesNo(outcome.ex) : ''}</td>
                  <td className={NUMBER}>{outcome?.scored ? outcome.sql_attempts : ''}</td>
                  <td className={NUMBER}>{outcome?.scored ? outcome.seconds.toFixed(1) : ''}</td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </section>
  )
}

function RunDetail({
  study,
  column,
  question,
}: {
  study: Study
  column: RunColumn
  question: EvalQuestion
}) {
  const found = column.outcomes.get(question.id)
  const verdict = verdictIn(column, question.id, study)
  return (
    <section aria-labelledby="this-run">
      <h2 id="this-run" className={`${HEADING} flex items-center gap-2`}>
        <span className="inline-flex size-4 items-center justify-center">
          <VerdictMark verdict={verdict} />
        </span>
        {column.label}: {VERDICT_WORDS[verdict].toLowerCase()}
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        {column.llm}
        {found && ` · report ${found.run.key} · ${dayOf(found.run.date)}`}
      </p>
      {!found && <p className="mt-2 text-sm">This run didn&apos;t include this question.</p>}
      {found && !found.outcome.scored && <p className="mt-2 text-sm">{found.outcome.reason}</p>}
      {found?.outcome.scored && <Reasons outcome={found.outcome} />}
      {found?.outcome.scored && (
        <p className="mt-2 text-xs text-fg-muted tabular-nums">
          {found.outcome.input_tokens.toLocaleString('en-US')} tokens in,{' '}
          {found.outcome.output_tokens.toLocaleString('en-US')} out.
        </p>
      )}
      <div className="mt-4 grid gap-4 md:grid-cols-2">
        <div className="min-w-0">
          <h3 className="mb-1 text-sm font-semibold">Our SQL</h3>
          {found?.outcome.sql ? (
            <SqlCode sql={found.outcome.sql} />
          ) : (
            <p className="text-sm text-fg-muted">No SQL from this run.</p>
          )}
        </div>
        <div className="min-w-0">
          <h3 className="mb-1 text-sm font-semibold">Reference SQL</h3>
          {question.reference_sql ? (
            <SqlCode sql={question.reference_sql} />
          ) : (
            <p className="text-sm text-fg-muted">Not bundled: see the note above.</p>
          )}
        </div>
      </div>
    </section>
  )
}

function Reasons({ outcome }: { outcome: EvalOutcome }) {
  return (
    <dl className="mt-2 grid gap-1 text-sm">
      <div>
        <dt className="inline font-semibold">QueryMind match: </dt>
        <dd className="inline">
          {outcome.passed ? 'Passed.' : `Failed. ${outcome.reason || 'No reason saved.'}`}
        </dd>
      </div>
      <div>
        <dt className="inline font-semibold">BIRD EX: </dt>
        <dd className="inline">
          {outcome.ex
            ? 'Passed.'
            : `Failed. ${outcome.ex_reason || outcome.reason || 'No reason saved.'}`}
        </dd>
      </div>
    </dl>
  )
}

function Quality({ data, question }: { data: EvalsReply; question: EvalQuestion }) {
  const panel = data.quality
  const judged = panel?.questions.find(({ question_id }) => question_id === question.id)
  if (!panel || !judged) return null
  return (
    <section aria-labelledby="quality">
      <h2 id="quality" className={HEADING}>
        Answer quality
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        A separate run, not one of those above: {panel.agent_llm} answered, {panel.judge_llm}{' '}
        judged. Report {panel.report}, {dayOf(panel.date)}.
      </p>
      {judged.scores.length > 0 ? (
        <ul className="mt-2 grid gap-1 text-sm">
          {judged.scores.map(({ metric, score, passed }) => (
            <li key={metric} className="flex flex-wrap items-center gap-2">
              <span className="inline-flex size-4 items-center justify-center">
                <VerdictMark verdict={passed ? 'passed' : 'failed'} />
              </span>
              <span>
                {metric}: <span className="tabular-nums">{score?.toFixed(2) ?? 'no score'}</span>,{' '}
                {passed ? 'passed' : 'failed'}
              </span>
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-2 text-sm">The judge gave no scores for this answer.</p>
      )}
      {judged.note && <p className="mt-2 text-sm">{judged.note}</p>}
    </section>
  )
}
