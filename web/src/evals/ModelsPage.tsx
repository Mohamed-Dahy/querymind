import { lazy, Suspense } from 'react'

import type { EvalsReply, ModelRow, ModelStudy } from '@/api/evals'
import { Skeleton } from '@/components/ui/skeleton'

import { EvalsFrame } from './EvalsFrame'
import { dollars } from './format'
import { gapNote, scatterPoints, shortModel, slowest, unpricedModels } from './modelRows'

// Its own download: only this view needs a chart library.
const ModelScatter = lazy(() => import('./ModelScatter'))

const TEXT = 'px-3 py-2 text-left align-top'
const NUMBER = 'px-3 py-2 text-right align-top tabular-nums whitespace-nowrap'
// Headings may wrap: the numbers under them are what must stay on one line.
const NUMBER_HEAD = 'px-3 py-2 text-right align-bottom'

/** The model comparison: the same agent, with only the LLM changed. */
export default function ModelsPage() {
  return (
    <EvalsFrame
      title="Model comparison"
      intro="The same full agent on the same questions, one question at a time. Only the LLM changes."
    >
      {(data) => <Studies data={data} />}
    </EvalsFrame>
  )
}

function Studies({ data }: { data: EvalsReply }) {
  const noiseOf = (name: string) =>
    data.ablations.find((study) => study.name === name)?.noise_questions
  return (
    <div className="space-y-12">
      <p className="text-sm text-fg-muted">
        Costs are list prices, checked {data.models.prices_checked}. Our own runs used free tiers.
      </p>
      {data.models.studies.map((study) => (
        <Study key={study.name} study={study} noise={noiseOf(study.name)} />
      ))}
      <section aria-labelledby="models-notes">
        <h2 id="models-notes" className="text-lg font-semibold">
          What these numbers leave out
        </h2>
        <ul className="mt-2 list-disc space-y-2 pl-5 text-sm">
          {data.models.notes.map((note) => (
            <li key={note}>{note}</li>
          ))}
        </ul>
      </section>
    </div>
  )
}

function Study({ study, noise }: { study: ModelStudy; noise: number | undefined }) {
  const headingId = `models-${study.name}`
  const points = scatterPoints(study)
  const unpriced = unpricedModels(study)
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="text-lg font-semibold">
        {study.title}
        {study.partial_note && `: ${study.partial_note}`}
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        Every row is scored on the same {study.questions} questions: those every model below scored
        (a question lost to a rate limit is left out for all).
      </p>

      {points.length > 1 && (
        <div className="mt-4 rounded-cell border bg-surface p-4">
          <p className="text-xs text-fg-muted">
            QueryMind match, up to 100%. Up and to the left is better: more correct answers, for
            less money.
          </p>
          <Suspense
            fallback={
              <Skeleton aria-hidden="true" className="h-80 w-full motion-reduce:animate-none" />
            }
          >
            <ModelScatter points={points} />
          </Suspense>
          {noise !== undefined && <p className="mt-2 text-sm">{gapNote(points, noise)}</p>}
          {unpriced.length > 0 && (
            <p className="mt-2 text-sm">
              Not drawn, because they have no cost per correct answer: {unpriced.join(', ')}.
            </p>
          )}
        </div>
      )}
      {study.only_one_model && (
        <p className="mt-4 rounded-cell border bg-surface p-4 text-sm">
          Only one model has run this study so far: nothing to compare yet.
        </p>
      )}
      {study.rows.length > 1 && <SpeedBars rows={study.rows} />}

      <ModelTable study={study} />
      {study.not_run.length > 0 && (
        <p className="mt-3 text-sm">
          Not run yet:{' '}
          {study.not_run.map(({ llm, missing }) => `${llm} (${missing.join(', ')})`).join('; ')}.
        </p>
      )}
      <p className="mt-2 break-words text-xs text-fg-muted">Reports: {study.reports.join(', ')}</p>
    </section>
  )
}

/** Plain HTML, like the arms' bars: every time is real text next to its bar. */
function SpeedBars({ rows }: { rows: ModelRow[] }) {
  const top = slowest(rows)
  return (
    <div className="mt-4 rounded-cell border bg-surface p-4">
      <h3 className="text-sm font-semibold">Seconds per question</h3>
      <p className="mt-1 text-xs text-fg-muted">
        p50: half the questions took less than this. p95: 19 in 20 took less.
      </p>
      <ul className="mt-3 space-y-4">
        {rows.map((row) => (
          <li key={row.llm} aria-label={`Speed of ${shortModel(row.llm)}`} className="text-sm">
            <p className="font-semibold">{shortModel(row.llm)}</p>
            <SpeedBar name="p50" seconds={row.p50_seconds} top={top} className="bg-signal" />
            <SpeedBar name="p95" seconds={row.p95_seconds} top={top} className="bg-input" />
          </li>
        ))}
      </ul>
    </div>
  )
}

type SpeedBarProps = {
  name: string
  seconds: number | null | undefined
  top: number
  className: string
}

function SpeedBar({ name, seconds, top, className }: SpeedBarProps) {
  if (seconds === null || seconds === undefined) return null
  return (
    <div className="mt-1 grid grid-cols-[6rem_1fr] items-center gap-2 tabular-nums">
      <span>
        {name} {seconds.toFixed(1)} s
      </span>
      <div aria-hidden="true" className="h-2 overflow-hidden rounded bg-muted">
        <div
          className={`h-full ${className}`}
          style={{ width: `${top ? (100 * seconds) / top : 0}%` }}
        />
      </div>
    </div>
  )
}

const tokens = (value: number | null | undefined) => value?.toLocaleString('en-US') ?? ''
const secondsText = (value: number | null | undefined) => value?.toFixed(1) ?? ''

function ModelTable({ study }: { study: ModelStudy }) {
  const isBird = study.name === 'bird'
  return (
    <div
      tabIndex={0}
      role="region"
      aria-label={`${study.title}: every number`}
      className="mt-4 overflow-x-auto rounded-cell border bg-surface"
    >
      <table className="w-full text-sm">
        <caption className="sr-only">What each model scored, how fast, and what it cost</caption>
        <thead className="text-fg-muted">
          <tr>
            <th scope="col" className={TEXT}>
              Model
            </th>
            {isBird && (
              <th scope="col" className={NUMBER_HEAD}>
                BIRD EX
              </th>
            )}
            <th scope="col" className={NUMBER_HEAD}>
              QueryMind match
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              p50 s
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              p95 s
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Tokens / question (in + out)
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              $ / question
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              $ / 1,000 questions
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              $ / correct answer
            </th>
          </tr>
        </thead>
        <tbody>
          {study.rows.map((row) => (
            <tr key={row.llm} className="border-t">
              <th scope="row" className={`${TEXT} min-w-48 font-normal`}>
                <span className="font-semibold">{row.llm}</span>
                {row.price_note && (
                  <span className="mt-1 block text-xs text-fg-muted">{row.price_note}</span>
                )}
              </th>
              {isBird && (
                <td className={NUMBER}>
                  {row.ex}/{row.questions}
                </td>
              )}
              <td className={NUMBER}>
                {row.passed}/{row.questions}
              </td>
              <td className={NUMBER}>{secondsText(row.p50_seconds)}</td>
              <td className={NUMBER}>{secondsText(row.p95_seconds)}</td>
              <td className={NUMBER}>
                {tokens(row.input_tokens)} + {tokens(row.output_tokens)}
              </td>
              <td className={NUMBER}>{dollars(row.cost_per_question)}</td>
              <td className={NUMBER}>
                {dollars(
                  row.cost_per_question === null || row.cost_per_question === undefined
                    ? null
                    : row.cost_per_question * 1000,
                )}
              </td>
              <td className={NUMBER}>{dollars(row.cost_per_correct)}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
