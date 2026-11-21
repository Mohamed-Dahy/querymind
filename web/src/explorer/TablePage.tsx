import { useQuery } from '@tanstack/react-query'
import { ArrowLeft, ArrowRight, MessageSquarePlus, ShieldAlert } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { CONVERSATIONS_KEY } from '@/api/conversations'
import { getTableQuestions, type DatabaseId } from '@/api/explorer'
import { useDraftNew } from '@/canvas/useConversation'
import { SqlCode } from '@/canvas/SqlView'
import { Skeleton } from '@/components/ui/skeleton'
import { NotFoundPage } from '@/pages/NotFoundPage'

import {
  businessLabel,
  formatRowCount,
  tableDetails,
  termAnchor,
  termName,
  type Relation,
  type TableDetails,
} from './data'
import { LoadError } from './LoadError'
import { useExplorerData } from './useExplorerData'
import { useExplorerDatabase, useExplorerHref } from './useExplorerDatabase'

/** One table: what it means, its columns and joins, its terms, and questions to ask about it. */
export default function TablePage() {
  const { table: name } = useParams()
  const database = useExplorerDatabase()
  const { catalog, semantic, error, pending, retry } = useExplorerData(database)

  // A join is followed from far down the page, and the next table starts at its title.
  useEffect(() => {
    document.documentElement.scrollTop = 0
  }, [name])

  if (pending) {
    return (
      <div aria-hidden="true" className="mx-auto max-w-5xl space-y-3 px-4 py-10">
        <Skeleton className="h-8 w-48 motion-reduce:animate-none" />
        <Skeleton className="h-64 w-full rounded-cell motion-reduce:animate-none" />
      </div>
    )
  }
  if (catalog === undefined || semantic === undefined) {
    return (
      <div className="mx-auto max-w-5xl px-4 py-10">
        <h1 className="text-2xl">{name}</h1>
        <div className="mt-6">
          <LoadError error={error} onRetry={retry} />
        </div>
      </div>
    )
  }
  const details = tableDetails(name ?? '', catalog, semantic)
  if (details === undefined) return <NotFoundPage />
  return <TableView details={details} database={database} />
}

function TableView({ details, database }: { details: TableDetails; database: DatabaseId }) {
  const href = useExplorerHref()
  const { table, meaning, businessDescription } = details
  const piiCount = meaning?.columns.filter(({ pii }) => pii).length ?? 0
  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <Link
        to={href(`/explore?table=${encodeURIComponent(table.name)}`)}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-lg text-sm text-info underline underline-offset-4"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        All tables
      </Link>
      {/* Signal Lab (U8.5): a mono label, then the name in mono, as it is written in SQL. */}
      <p aria-hidden="true" className="label-mono mt-4">
        TABLE
      </p>
      <h1 className="mt-1 font-mono text-3xl font-semibold tracking-tight break-all">
        {table.name}
      </h1>
      <p className="mt-2 max-w-3xl text-fg-muted">
        {businessLabel(table.business)}
        {businessDescription && `: ${businessDescription}`}
      </p>
      <p className="mt-3 flex flex-wrap items-center gap-2 text-sm">
        <span className="rounded-full border bg-surface px-2.5 py-0.5 font-mono text-fg-muted tabular-nums">
          {formatRowCount(table.row_count)}, approximately
        </span>
        {piiCount > 0 && (
          <span className="inline-flex items-center gap-1 rounded-full border border-warn/50 bg-surface px-2.5 py-0.5 text-warn">
            <ShieldAlert aria-hidden="true" className="size-4" />
            {piiCount} PII {piiCount === 1 ? 'column' : 'columns'}
          </span>
        )}
      </p>

      {/* counter-reset: the sections number themselves (01, 02…) in their headings, below. */}
      <div className="mt-8 space-y-4 [counter-reset:part]">
        <About details={details} />
        <Columns details={details} />
        <Joins relations={details.relations} />
        <Terms details={details} />
        <Questions questions={meaning?.suggested_questions ?? []} />
        <RecentQuestions table={table.name} database={database} />
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section
      aria-label={title}
      className="rounded-cell border bg-surface p-5 [counter-increment:part]"
    >
      {/* The number is CSS with empty alt text (`/ ''`): seen, not read out, not in the heading's name. */}
      <h2 className="text-base font-semibold tracking-tight before:mr-2.5 before:font-mono before:text-xs before:font-normal before:text-fg-muted before:content-[counter(part,decimal-leading-zero)_/_'']">
        {title}
      </h2>
      <div className="mt-3">{children}</div>
    </section>
  )
}

const DRAFT_NOTE = 'Marked "AI draft": written by an LLM, and not checked by a person yet.'

function DictionaryNote({ text, reviewed }: { text: string; reviewed: boolean }) {
  return (
    <p className="text-sm text-fg-muted">
      <span className="font-medium">{reviewed ? 'Data dictionary:' : 'AI draft:'}</span> {text}
    </p>
  )
}

function About({ details: { meaning } }: { details: TableDetails }) {
  const drafted = meaning?.dictionary_description
  // A saved database's meanings are made from its dictionary (U7.1c): the same words would show
  // twice, once as written and once as the draft. Then only the draft (or reviewed entry) shows.
  const written = meaning?.description === drafted ? undefined : meaning?.description
  const synonyms = meaning?.synonyms ?? []
  return (
    <Section title="What it is">
      <div className="space-y-2">
        {written && <p>{written}</p>}
        {drafted && (
          <DictionaryNote text={drafted} reviewed={meaning?.dictionary_reviewed ?? false} />
        )}
        {!written && !drafted && (
          <p className="text-fg-muted">Nobody has described this table yet.</p>
        )}
        {synonyms.length > 0 && (
          <p className="text-sm">
            <span className="text-fg-muted">Other words for it:</span> {synonyms.join(', ')}
          </p>
        )}
      </div>
    </Section>
  )
}

function Columns({ details: { table, meaning } }: { details: TableDetails }) {
  const notes = new Map(meaning?.columns.map((column) => [column.name, column]))
  const reviewed = meaning?.dictionary_reviewed ?? false
  const hasDrafts = !reviewed && meaning?.columns.some((column) => column.dictionary_note)
  const keyTargets = new Map(
    table.foreign_keys.flatMap(({ columns, ref_table: target }) =>
      columns.map((column) => [column, target] as const),
    ),
  )
  return (
    <Section title={`Columns (${table.columns.length})`}>
      {hasDrafts && <p className="mb-2 text-sm text-fg-muted">{DRAFT_NOTE}</p>}
      <div
        role="region"
        aria-label="Columns of this table"
        tabIndex={0}
        className="overflow-x-auto rounded-lg border outline-none focus-visible:ring-3 focus-visible:ring-ring"
      >
        <table className="w-full min-w-xl text-sm">
          <thead className="bg-muted text-left font-mono text-xs text-fg-muted">
            <tr>
              <th scope="col" className="px-3 py-2 font-medium">
                Column
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Type
              </th>
              <th scope="col" className="px-3 py-2 font-medium">
                Meaning
              </th>
            </tr>
          </thead>
          <tbody>
            {table.columns.map(({ name, data_type: type }) => {
              const note = notes.get(name)
              const written = note?.description === note?.dictionary_note ? '' : note?.description
              const target = keyTargets.get(name)
              return (
                <tr key={name} className="border-t align-top">
                  <th scope="row" className="px-3 py-2 text-left font-normal">
                    <span className="font-mono break-all">{name}</span>
                    <span className="mt-0.5 flex flex-wrap gap-x-2 text-xs text-fg-muted">
                      {table.primary_key.includes(name) && <span>primary key</span>}
                      {target !== undefined && <span>points to {target}</span>}
                      {note?.pii && (
                        <span className="inline-flex items-center gap-0.5 text-warn">
                          <ShieldAlert aria-hidden="true" className="size-3" />
                          PII
                        </span>
                      )}
                    </span>
                  </th>
                  <td className="px-3 py-2 font-mono text-xs text-fg-muted">{type}</td>
                  <td className="space-y-1 px-3 py-2">
                    {written && <p>{written}</p>}
                    {note?.dictionary_note && (
                      <DictionaryNote text={note.dictionary_note} reviewed={reviewed} />
                    )}
                    {note?.synonyms.length ? (
                      <p className="text-xs text-fg-muted">Also: {note.synonyms.join(', ')}</p>
                    ) : null}
                    {!note?.description && !note?.dictionary_note && (
                      <p className="text-fg-muted">No notes yet.</p>
                    )}
                  </td>
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </Section>
  )
}

function Joins({ relations }: { relations: readonly Relation[] }) {
  return (
    <Section title="Joins">
      {relations.length === 0 ? (
        <p className="text-fg-muted">
          This table has no foreign keys, and no other table points to it.
        </p>
      ) : (
        <ul className="space-y-1.5">
          {relations.map((relation) => (
            <RelationRow key={relation.id} relation={relation} />
          ))}
        </ul>
      )}
    </Section>
  )
}

function RelationRow({ relation }: { relation: Relation }) {
  const { from, to, other, preferred, note } = relation
  const href = useExplorerHref()
  return (
    <li className="flex flex-wrap items-center gap-x-2 gap-y-1 text-sm">
      <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs break-all">{from}</code>
      <ArrowRight aria-label="points to" className="size-3.5 shrink-0 text-signal" />
      <code className="rounded-md bg-muted px-1.5 py-0.5 font-mono text-xs break-all">{to}</code>
      <Link
        to={href(`/explore/tables/${encodeURIComponent(other)}`)}
        className="inline-flex min-h-8 items-center rounded-lg text-info underline underline-offset-4 pointer-coarse:min-h-11"
      >
        Open {other}
      </Link>
      {preferred && (
        <span className="rounded-full border px-2 py-0.5 font-mono text-[11px] text-fg-muted">
          preferred join
        </span>
      )}
      {note && <span className="text-xs text-fg-muted">({note})</span>}
    </li>
  )
}

function Terms({ details: { metrics } }: { details: TableDetails }) {
  const href = useExplorerHref()
  return (
    <Section title="Business terms">
      {metrics.length === 0 ? (
        <p className="text-fg-muted">No business term is defined on this table.</p>
      ) : (
        <ul className="space-y-3">
          {metrics.map((metric) => (
            <li key={metric.name} className="rounded-lg border bg-bg p-4">
              <p className="font-medium">
                {termName(metric.name)}{' '}
                <span className="ml-1 rounded-full border px-2 py-0.5 align-middle font-mono text-[11px] font-normal text-fg-muted">
                  {metric.kind === 'measure' ? 'a measure' : 'a condition'}
                </span>
              </p>
              <p className="mt-0.5 text-sm text-fg-muted">{metric.description}</p>
              <div className="mt-1.5">
                <SqlCode sql={metric.sql} />
              </div>
              <Link
                to={href(`/explore/glossary#${termAnchor(metric.name)}`)}
                className="mt-1 inline-flex min-h-8 items-center rounded-lg text-sm text-info underline underline-offset-4 pointer-coarse:min-h-11"
              >
                {termName(metric.name)} in the glossary
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}

function Questions({ questions }: { questions: readonly string[] }) {
  const draftNew = useDraftNew()
  return (
    <Section title="Questions to ask">
      {questions.length === 0 ? (
        <p className="text-fg-muted">No verified example question uses this table yet.</p>
      ) : (
        <>
          <p className="mb-2 text-sm text-fg-muted">
            Each one opens a new conversation with the question ready: nothing is sent until you
            send it.
          </p>
          <ul className="grid gap-2 sm:grid-cols-2">
            {questions.map((question) => (
              <li key={question}>
                {/* Like the canvas's example questions: a card that lifts a little under the pointer. */}
                <button
                  type="button"
                  onClick={() => draftNew(question)}
                  className="lift flex min-h-11 w-full items-start gap-2 rounded-lg border bg-bg px-3 py-2.5 text-left text-sm"
                >
                  <MessageSquarePlus
                    aria-hidden="true"
                    className="mt-0.5 size-4 shrink-0 text-signal"
                  />
                  {question}
                </button>
              </li>
            ))}
          </ul>
        </>
      )}
    </Section>
  )
}

/** Saved questions whose SQL read this table. Nothing at all with history off. */
function RecentQuestions({ table, database }: { table: string; database: DatabaseId }) {
  // Under the conversations' key, so a new answer refreshes it too. Per database: the demo's
  // `customers` is not a saved database's.
  const recent = useQuery({
    queryKey: [...CONVERSATIONS_KEY, 'using', table, database],
    queryFn: ({ signal }) => getTableQuestions(table, database, signal),
    retry: false,
  })
  if (!recent.isSuccess) return null
  const { questions } = recent.data
  return (
    <Section title="Recent questions that used it">
      {questions.length === 0 ? (
        <p className="text-fg-muted">No saved question has used this table yet.</p>
      ) : (
        <ul className="space-y-1.5">
          {questions.map(({ conversation_id: id, n, question }) => (
            <li key={`${id}-${n}`}>
              <Link
                to={`/c/${id}`}
                className="inline-flex min-h-8 items-center rounded-lg text-sm text-info underline underline-offset-4 pointer-coarse:min-h-11"
              >
                {question}
              </Link>
            </li>
          ))}
        </ul>
      )}
    </Section>
  )
}
