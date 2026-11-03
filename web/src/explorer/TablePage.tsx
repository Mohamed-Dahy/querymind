import { ArrowLeft, ArrowRight, MessageSquarePlus, ShieldAlert } from 'lucide-react'
import { useEffect, type ReactNode } from 'react'
import { Link, useParams } from 'react-router'

import { useDraftNew } from '@/canvas/useConversation'
import { SqlCode } from '@/canvas/SqlView'
import { Skeleton } from '@/components/ui/skeleton'
import { NotFoundPage } from '@/pages/NotFoundPage'

import {
  businessLabel,
  formatRowCount,
  tableDetails,
  termName,
  type Relation,
  type TableDetails,
} from './data'
import { LoadError } from './LoadError'
import { useExplorerData } from './useExplorerData'

const tablePath = (name: string) => `/explore/tables/${encodeURIComponent(name)}`

/** One table: what it means, its columns and joins, its terms, and questions to ask about it. */
export default function TablePage() {
  const { table: name } = useParams()
  const { catalog, semantic, error, pending, retry } = useExplorerData()

  // A join is followed from far down the page, and the next table starts at its title.
  useEffect(() => {
    document.documentElement.scrollTop = 0
  }, [name])

  if (pending) {
    return (
      <div aria-hidden="true" className="mx-auto max-w-5xl space-y-3 px-4 py-10">
        <Skeleton className="h-8 w-48 motion-reduce:animate-none" />
        <Skeleton className="h-64 w-full motion-reduce:animate-none" />
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
  return <TableView details={details} />
}

function TableView({ details }: { details: TableDetails }) {
  const { table, meaning, businessDescription } = details
  const piiCount = meaning?.columns.filter(({ pii }) => pii).length ?? 0
  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <Link
        to={`/explore?table=${encodeURIComponent(table.name)}`}
        className="inline-flex min-h-11 items-center gap-1.5 rounded-lg text-sm text-info underline underline-offset-4"
      >
        <ArrowLeft aria-hidden="true" className="size-4" />
        All tables
      </Link>
      <h1 className="mt-2 text-2xl break-all">{table.name}</h1>
      <p className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-1 text-fg-muted">
        <span>
          {businessLabel(table.business)}
          {businessDescription && `: ${businessDescription}`}
        </span>
        <span>{formatRowCount(table.row_count)}, approximately</span>
        {piiCount > 0 && (
          <span className="inline-flex items-center gap-1 text-warn">
            <ShieldAlert aria-hidden="true" className="size-4" />
            {piiCount} PII {piiCount === 1 ? 'column' : 'columns'}
          </span>
        )}
      </p>

      <div className="mt-8 space-y-8">
        <About details={details} />
        <Columns details={details} />
        <Joins relations={details.relations} />
        <Terms details={details} />
        <Questions questions={meaning?.suggested_questions ?? []} />
      </div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section aria-label={title}>
      <h2 className="text-lg font-semibold">{title}</h2>
      <div className="mt-2">{children}</div>
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
  const written = meaning?.description
  const drafted = meaning?.dictionary_description
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
          <thead className="bg-muted text-left">
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
                  <td className="px-3 py-2 text-fg-muted">{type}</td>
                  <td className="space-y-1 px-3 py-2">
                    {note?.description && <p>{note.description}</p>}
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
  return (
    <li className="flex flex-wrap items-center gap-x-2 text-sm">
      <code className="font-mono break-all">{from}</code>
      <ArrowRight aria-label="points to" className="size-3.5 shrink-0 text-fg-muted" />
      <code className="font-mono break-all">{to}</code>
      <Link
        to={tablePath(other)}
        className="inline-flex min-h-8 items-center rounded-lg text-info underline underline-offset-4 pointer-coarse:min-h-11"
      >
        Open {other}
      </Link>
      {preferred && <span className="text-xs text-fg-muted">preferred join</span>}
      {note && <span className="text-xs text-fg-muted">({note})</span>}
    </li>
  )
}

function Terms({ details: { metrics } }: { details: TableDetails }) {
  return (
    <Section title="Business terms">
      {metrics.length === 0 ? (
        <p className="text-fg-muted">No business term is defined on this table.</p>
      ) : (
        <ul className="space-y-4">
          {metrics.map((metric) => (
            <li key={metric.name}>
              <p className="font-medium">
                {termName(metric.name)}{' '}
                <span className="text-xs font-normal text-fg-muted">
                  {metric.kind === 'measure' ? 'a measure' : 'a condition'}
                </span>
              </p>
              <p className="mt-0.5 text-sm text-fg-muted">{metric.description}</p>
              <div className="mt-1.5">
                <SqlCode sql={metric.sql} />
              </div>
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
          <ul className="space-y-1.5">
            {questions.map((question) => (
              <li key={question}>
                <button
                  type="button"
                  onClick={() => draftNew(question)}
                  className="flex min-h-11 w-full items-center gap-2 rounded-lg border border-input px-3 py-2 text-left text-sm hover:bg-muted"
                >
                  <MessageSquarePlus aria-hidden="true" className="size-4 shrink-0 text-fg-muted" />
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
