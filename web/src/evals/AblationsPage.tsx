import { Link, useSearchParams } from 'react-router'

import type { AblationArm, AblationStudy, EvalsReply } from '@/api/evals'
import { cn } from '@/lib/utils'

import { EvalsFrame } from './EvalsFrame'
import { percentText, scoreText } from './format'
import { Waterfall } from './Waterfall'
import {
  describeWaterfall,
  differentQuestions,
  headlineName,
  tokenChange,
  waterfallRows,
} from './waterfallRows'

const SCOPE_NAMES: Record<string, string> = {
  all: 'All 58',
  golden: 'Northwind 30',
  business: 'Business terms 13',
  chinook: 'Chinook 15',
}

const SOURCE = 'break-words text-xs text-fg-muted'

/** The ablations: switch parts of the agent off, and see what each one is worth. */
export default function AblationsPage() {
  return (
    <EvalsFrame
      title="What each part adds"
      intro="Each arm adds one part to the arm before it, with the same model throughout, so the difference between two rows is what that part is worth."
    >
      {(data) => <Studies data={data} />}
    </EvalsFrame>
  )
}

function Studies({ data }: { data: EvalsReply }) {
  return (
    <div className="space-y-12">
      {data.ablations.map((study) => (
        <Study key={study.name} study={study} />
      ))}
    </div>
  )
}

function Study({ study }: { study: AblationStudy }) {
  const [params] = useSearchParams()
  const chosen = study.views.find(({ scope }) => scope === params.get('scope')) ?? study.views[0]
  if (!chosen) return null
  const rows = waterfallRows(chosen.arms, study)
  const notRun = chosen.arms.filter(({ match }) => match === null)
  const saving = tokenChange(rows)
  const headingId = `study-${study.name}`
  const reports = [...new Set(chosen.arms.flatMap((arm) => arm.reports))]
  return (
    <section aria-labelledby={headingId}>
      <h2 id={headingId} className="text-lg font-semibold">
        {study.title}
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        Judged by {headlineName(study)}. {study.llm} for every arm, one question at a time. Two runs
        of one setup differ by about {study.noise_questions} questions.
      </p>

      {study.views.length > 1 && (
        <nav aria-label={`${study.title}: which questions`} className="mt-3 flex flex-wrap gap-2">
          {study.views.map(({ scope }) => (
            <Link
              key={scope}
              to={scope === 'all' ? '/evals/ablations' : `/evals/ablations?scope=${scope}`}
              aria-current={scope === chosen.scope ? 'true' : undefined}
              className={cn(
                'inline-flex min-h-11 items-center rounded-lg border px-3 text-sm',
                scope === chosen.scope ? 'border-input bg-muted font-semibold' : 'hover:bg-muted',
              )}
            >
              {SCOPE_NAMES[scope] ?? scope}
            </Link>
          ))}
        </nav>
      )}

      {rows.length > 1 ? (
        <div className="mt-4 rounded-cell border bg-surface p-4">
          <p className="sr-only">{describeWaterfall(rows)}</p>
          <Waterfall rows={rows} />
          {differentQuestions(rows) && (
            <p className="mt-4 text-sm">
              Not every arm was scored on the same questions, because some were lost to a rate
              limit. Compare these percentages with care.
            </p>
          )}
          {saving && <p className="mt-2 text-sm">{saving}</p>}
        </div>
      ) : (
        <p className="mt-4 rounded-cell border bg-surface p-4 text-sm">
          {rows[0]
            ? `Only arm ${rows[0].arm.key} has run, so there is nothing to compare yet: ${scoreText(rows[0].score)} by ${headlineName(study)}.`
            : 'No arm has run yet.'}
          {notRun.length > 0 &&
            ` Not run yet: ${notRun.map(({ key }) => `arm ${key}`).join(', ')}.`}
        </p>
      )}

      <ArmTable study={study} arms={chosen.arms} />
      <p className={`${SOURCE} mt-2`}>Reports: {reports.join(', ')}</p>
    </section>
  )
}

const TEXT = 'px-3 py-2 text-left align-top'
const NUMBER = 'px-3 py-2 text-right align-top tabular-nums whitespace-nowrap'
// Headings may wrap: the numbers under them are what must stay on one line.
const NUMBER_HEAD = 'px-3 py-2 text-right align-bottom'

const both = (score: AblationArm['match']) =>
  score ? `${scoreText(score)} (${percentText(score)})` : 'not run yet'

function ArmTable({ study, arms }: { study: AblationStudy; arms: AblationArm[] }) {
  return (
    <div
      tabIndex={0}
      role="region"
      aria-label={`${study.title}: every number`}
      className="mt-4 overflow-x-auto rounded-cell border bg-surface"
    >
      <table className="w-full text-sm">
        <caption className="sr-only">What each arm scored, and what it cost</caption>
        <thead className="text-fg-muted">
          <tr>
            <th scope="col" className={TEXT}>
              Arm
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              QueryMind match
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              BIRD EX
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Change
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Not scored
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Repairs / question
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Tokens / question (in + out)
            </th>
            <th scope="col" className={NUMBER_HEAD}>
              Median seconds
            </th>
          </tr>
        </thead>
        <tbody>
          {arms.map((arm) => {
            const ran = arm.match !== null
            return (
              <tr key={arm.key} className="border-t">
                <th scope="row" className={`${TEXT} min-w-48 font-normal`}>
                  <span className="font-semibold">{arm.key}</span>{' '}
                  <span className="text-fg-muted">{arm.adds}</span>
                </th>
                <td className={NUMBER}>{both(arm.match)}</td>
                <td className={NUMBER}>{ran ? both(arm.ex) : ''}</td>
                <td className={NUMBER}>
                  {arm.delta_points === null
                    ? ''
                    : `${arm.delta_points >= 0 ? '+' : ''}${arm.delta_points.toFixed(1)} pts`}
                </td>
                <td className={NUMBER}>{ran ? arm.not_scored : ''}</td>
                <td className={NUMBER}>{ran ? arm.repairs_per_question?.toFixed(2) : ''}</td>
                <td className={NUMBER}>
                  {ran
                    ? `${arm.input_tokens?.toLocaleString('en-US')} + ${arm.output_tokens?.toLocaleString('en-US')}`
                    : ''}
                </td>
                <td className={NUMBER}>{ran ? arm.median_seconds?.toFixed(1) : ''}</td>
              </tr>
            )
          })}
        </tbody>
      </table>
    </div>
  )
}
