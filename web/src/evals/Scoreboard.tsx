import { ExternalLink } from 'lucide-react'
import { Link } from 'react-router'

import type { EvalsReply, Milestone, QualityPanel, SpeedUp } from '@/api/evals'

import { headlineTiles, newestRun, type Tile } from './format'

const SOURCE = 'break-words text-xs text-fg-muted'

/** The evals lab's first view: the headline numbers, and where each came from. */
export function Scoreboard({ data }: { data: EvalsReply }) {
  return (
    <div className="space-y-10">
      <p className="text-sm text-fg-muted">
        Saved eval runs, the newest from {newestRun(data)}. These numbers don&rsquo;t update by
        themselves.
      </p>

      <section aria-labelledby="headlines">
        <h2 id="headlines" className="sr-only">
          Headline numbers
        </h2>
        <ul className="grid gap-4 md:grid-cols-2">
          {headlineTiles(data).map((tile) => (
            <HeadlineTile key={tile.title} tile={tile} />
          ))}
        </ul>
      </section>

      <Timeline milestones={data.milestones} />
      {data.quality && <Quality quality={data.quality} />}
      <SpeedUps speedUps={data.speed_ups} />
    </div>
  )
}

function HeadlineTile({ tile }: { tile: Tile }) {
  const { title, value, caption, details, sources, date, model, link } = tile
  return (
    <li aria-label={title} className="flex flex-col rounded-cell border bg-surface p-4">
      <h3 className="text-sm font-semibold">{title}</h3>
      <p className="mt-1 text-3xl font-semibold tabular-nums">{value}</p>
      <p className="mt-1 text-sm">{caption}</p>
      {details.map((detail) => (
        <p key={detail} className="mt-2 text-sm text-fg-muted">
          {detail}
        </p>
      ))}
      {link && (
        <a
          href={link.href}
          target="_blank"
          rel="noreferrer"
          className="mt-2 inline-flex min-h-11 w-fit items-center gap-1.5 rounded-lg text-sm text-info underline underline-offset-4"
        >
          {link.label}
          <ExternalLink aria-hidden="true" className="size-3.5" />
          <span className="sr-only">(opens in a new tab)</span>
        </a>
      )}
      <dl className={`${SOURCE} mt-auto grid grid-cols-[auto_1fr] gap-x-2 pt-3`}>
        <dt>Model</dt>
        <dd>{model}</dd>
        <dt>Date</dt>
        <dd>{date}</dd>
        <dt>{sources.length > 1 ? 'Reports' : 'Report'}</dt>
        <dd className="min-w-0">{sources.join(', ')}</dd>
      </dl>
    </li>
  )
}

/**
 * Separate points on purpose. The question sets and models changed from phase to phase, so a line
 * joining them would draw a trend that was never measured.
 */
function Timeline({ milestones }: { milestones: Milestone[] }) {
  return (
    <section aria-labelledby="timeline">
      <h2 id="timeline" className="text-lg font-semibold">
        Phase by phase
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        These aren&rsquo;t a trend: the questions got harder and the model changed. To compare parts
        of the agent on equal terms, see{' '}
        <Link to="/evals/ablations" className="text-info underline underline-offset-4">
          what each part adds
        </Link>
        .
      </p>
      <ol className="mt-3 grid gap-3 md:grid-cols-2">
        {milestones.map((point) => (
          <li
            key={`${point.phase}-${point.title}`}
            className="flex flex-col rounded-cell border bg-surface p-3"
          >
            <p className="text-xs text-fg-muted">Phase {point.phase}</p>
            <h3 className="font-semibold">{point.title}</h3>
            <p className="mt-1">
              <span className="text-xl font-semibold tabular-nums">
                {point.passed}/{point.total}
              </span>{' '}
              <span className="text-sm">on {point.question_set}</span>
            </p>
            {point.not_scored > 0 && (
              <p className="text-sm text-fg-muted">{point.not_scored} more not scored</p>
            )}
            {point.note && <p className="mt-1 text-sm text-fg-muted">{point.note}</p>}
            <p className={`${SOURCE} mt-auto pt-2`}>
              {point.llm} · {point.date} · {point.report}
            </p>
          </li>
        ))}
      </ol>
    </section>
  )
}

const NUMBER = 'px-3 py-2 text-right tabular-nums'
const TEXT = 'px-3 py-2 text-left'

function Quality({ quality }: { quality: QualityPanel }) {
  const { agent_llm, judge_llm, answers, judged_answers, metrics, questions, retrieval } = quality
  const warned = questions.filter(({ note }) => note)
  const notes = [...new Set(warned.map(({ note }) => note))]
  return (
    <section aria-labelledby="quality">
      <h2 id="quality" className="text-lg font-semibold">
        Answer quality
      </h2>
      <p className="mt-1 text-sm text-fg-muted">
        Its own run, not one of the runs above: {agent_llm} answered, {judge_llm} judged.{' '}
        {judged_answers} of {answers} answers had a written summary to judge. Scores go from 0 to 1,
        and a pass is at least 0.5.
      </p>

      <div className="mt-3 overflow-x-auto rounded-cell border bg-surface">
        <table className="w-full text-sm">
          <caption className="sr-only">DeepEval's answer scores</caption>
          <thead className="text-fg-muted">
            <tr>
              <th scope="col" className={TEXT}>
                DeepEval metric
              </th>
              <th scope="col" className={NUMBER}>
                Mean
              </th>
              <th scope="col" className={NUMBER}>
                Passed
              </th>
            </tr>
          </thead>
          <tbody>
            {metrics.map(({ metric, asks, mean, passed, judged }) => (
              <tr key={metric} className="border-t">
                <th scope="row" className={`${TEXT} font-normal`}>
                  <span className="font-mono">{metric}</span>
                  <span className="block text-fg-muted">{asks}</span>
                </th>
                <td className={NUMBER}>{mean === null ? 'not scored' : mean.toFixed(2)}</td>
                <td className={NUMBER}>
                  {passed}/{judged}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {warned.length > 0 && (
        <p className="mt-2 text-sm text-fg-muted">
          {warned.map(({ question_id }) => question_id).join(', ')}: {notes.join(' ')}
        </p>
      )}

      {retrieval && (
        <>
          <h3 className="mt-4 font-semibold">Schema retrieval (RAGAS)</h3>
          <dl className="mt-1 grid gap-x-3 gap-y-1 text-sm sm:grid-cols-[auto_1fr]">
            <dt className="text-fg-muted">Recall</dt>
            <dd>
              <span className="tabular-nums">{retrieval.id_recall.toFixed(3)}</span>:{' '}
              {retrieval.complete} of {retrieval.questions} questions got every table they need
            </dd>
            <dt className="text-fg-muted">Precision</dt>
            <dd>
              <span className="tabular-nums">{retrieval.id_precision.toFixed(3)}</span>: low by
              design, because a few extra tables only cost tokens
            </dd>
            {retrieval.llm_precision !== null && (
              <>
                <dt className="text-fg-muted">Judge&rsquo;s precision</dt>
                <dd>
                  <span className="tabular-nums">{retrieval.llm_precision.toFixed(3)}</span> on a
                  sample of {retrieval.sample}: this one measures whether useful tables rank first
                </dd>
              </>
            )}
          </dl>
        </>
      )}
      <p className={`${SOURCE} mt-3`}>
        {quality.date.slice(0, 10)} · {quality.report}
      </p>
    </section>
  )
}

function SpeedUps({ speedUps }: { speedUps: SpeedUp[] }) {
  return (
    <section aria-labelledby="speed">
      <h2 id="speed" className="text-lg font-semibold">
        Parallel runs with Ray
      </h2>
      <ul className="mt-3 grid gap-3 md:grid-cols-2">
        {speedUps.map(({ title, before, after, factor, note, report }) => (
          <li key={title} className="flex flex-col rounded-cell border bg-surface p-3">
            <h3 className="text-sm font-semibold">{title}</h3>
            <p className="mt-1">
              <span className="text-xl font-semibold tabular-nums">{factor}</span>{' '}
              <span className="text-sm tabular-nums">
                faster: {before} to {after}
              </span>
            </p>
            <p className="mt-1 text-sm text-fg-muted">{note}</p>
            <p className={`${SOURCE} mt-auto pt-2`}>{report}</p>
          </li>
        ))}
      </ul>
    </section>
  )
}
