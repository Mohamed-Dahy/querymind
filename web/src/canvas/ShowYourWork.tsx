import { TriangleAlert } from 'lucide-react'
import type { ReactNode, RefObject } from 'react'
import { Link } from 'react-router'

import type { AskResponse } from '@/api/client'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { diffWords } from '@/lib/diff'
import { useMediaQuery } from '@/lib/useMediaQuery'

import { formatDuration } from './trace'
import { tablesPath, triesOf, type FailedKind, type WorkTry } from './work'

const WHAT_WENT_WRONG: Record<FailedKind, string> = {
  unsafe: 'The validator refused it.',
  db_error: 'The database rejected it.',
  timeout: 'It took too long.',
  suspicious: 'It ran, but the result looked wrong.',
}

/** Which tables the LLM saw, in a sentence. */
function schemaSentence(selection: NonNullable<AskResponse['schema_selection']>): string {
  const { mode, tables, total_tables: total, search } = selection
  if (mode === 'retrieved') {
    const how = search ? ` (${search.replaceAll('_', ' + ')} search)` : ''
    return `Retrieved ${tables.length} of ${total} tables${how}.`
  }
  if (mode === 'fallback') {
    return `The whole schema, ${total} tables: the search couldn't narrow it down.`
  }
  return `The whole schema, ${total} tables.`
}

type ShowYourWorkProps = {
  reply: AskResponse
  open: boolean
  onOpenChange: (open: boolean) => void
  /** Gets the focus back on closing. Radix only knows its own trigger, and this one isn't. */
  opener: RefObject<HTMLElement | null>
}

/** A side sheet with how an answer was made. It only reads the reply, so it works after any path. */
export function ShowYourWork({ reply, open, onOpenChange, opener }: ShowYourWorkProps) {
  const tries = triesOf(reply)
  const { schema_selection: selection, search_question: searchedAs, tables_used: used } = reply
  const explorerPath = tablesPath(reply)
  // A side panel needs width; on a phone it would cover everything, so it rises from the bottom
  // instead, leaving the answer's top in view (like a phone's own share sheets).
  const wide = useMediaQuery('(min-width: 640px)')

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent
        side={wide ? 'right' : 'bottom'}
        className="w-full overflow-y-auto data-[side=bottom]:max-h-[85dvh] data-[side=bottom]:rounded-t-cell data-[side=right]:w-full data-[side=right]:sm:max-w-xl"
        onCloseAutoFocus={(event) => {
          event.preventDefault()
          opener.current?.focus()
        }}
      >
        <SheetHeader className="pb-0">
          <SheetTitle>How this answer was made</SheetTitle>
          <SheetDescription>{reply.question}</SheetDescription>
        </SheetHeader>

        <div className="space-y-5 px-4 pb-6">
          {(reply.duration_ms != null || reply.sql_attempts != null) && (
            <p className="text-fg-muted tabular-nums">
              {reply.duration_ms != null && `Took ${formatDuration(reply.duration_ms)}`}
              {reply.duration_ms != null && reply.sql_attempts != null && ' · '}
              {reply.sql_attempts != null &&
                `${reply.sql_attempts} SQL ${reply.sql_attempts === 1 ? 'query' : 'queries'} run`}
            </p>
          )}

          {selection && (
            <Section title="Tables shown to the AI">
              <p>{schemaSentence(selection)}</p>
              <ul aria-label="Tables shown" className="mt-2 flex flex-wrap gap-1.5">
                {selection.tables.map((table) => (
                  <li key={table} className="rounded border px-1.5 py-0.5 font-mono text-xs">
                    {table}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          {explorerPath !== null && (
            <Section title="Tables the SQL read">
              {used.length > 0 ? (
                <ul aria-label="Tables read" className="flex flex-wrap gap-1.5">
                  {used.map((table) => (
                    <li key={table} className="rounded border px-1.5 py-0.5 font-mono text-xs">
                      {table}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-fg-muted">
                  Not recorded for this answer: it was saved before QueryMind kept this.
                </p>
              )}
              <Link
                to={explorerPath}
                className="mt-2 inline-flex min-h-8 items-center rounded-lg text-info underline underline-offset-4 pointer-coarse:min-h-11"
              >
                See these tables
              </Link>
            </Section>
          )}

          {searchedAs && (
            <Section title="Searched as">
              <p>The follow-up, as a question of its own: “{searchedAs}”</p>
            </Section>
          )}

          {reply.example_questions.length > 0 && (
            <Section title="Similar solved questions it was shown">
              <ul className="ml-4 list-disc space-y-1">
                {reply.example_questions.map((question) => (
                  <li key={question}>{question}</li>
                ))}
              </ul>
            </Section>
          )}

          <Section title={tries.length === 1 ? 'The SQL' : `The SQL, ${tries.length} tries`}>
            <ol className="space-y-3">
              {tries.map((one, index) => (
                <TryItem
                  key={`${index}-${one.sql}`}
                  attempt={index + 1}
                  work={one}
                  previous={tries[index - 1]?.sql}
                />
              ))}
            </ol>
          </Section>

          {reply.warnings.length > 0 && (
            <Section title="Warnings">
              <ul className="space-y-1">
                {reply.warnings.map((warning) => (
                  <li key={warning.code} className="flex items-start gap-2">
                    <TriangleAlert
                      aria-hidden="true"
                      className="mt-0.5 size-4 shrink-0 text-warn"
                    />
                    {warning.message}
                  </li>
                ))}
              </ul>
            </Section>
          )}

          <Section title={`Confidence: ${reply.confidence}`}>
            {reply.confidence_reasons.length > 0 ? (
              <ul className="ml-4 list-disc space-y-1">
                {reply.confidence_reasons.map((reason) => (
                  <li key={reason}>{reason}</li>
                ))}
              </ul>
            ) : (
              <p>No sign of a problem.</p>
            )}
          </Section>
        </div>
      </SheetContent>
    </Sheet>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h3 className="mb-1.5 font-medium">{title}</h3>
      {children}
    </section>
  )
}

const CODE = 'overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs whitespace-pre-wrap'

function TryItem({
  attempt,
  work,
  previous,
}: {
  attempt: number
  work: WorkTry
  previous: string | undefined
}) {
  return (
    <li>
      <p className="mb-1 font-medium">
        Try {attempt}
        {work.used && <span className="font-normal text-fg-muted"> · used for the answer</span>}
      </p>
      {previous === undefined ? (
        <pre className={CODE}>
          <code>{work.sql}</code>
        </pre>
      ) : (
        <SqlDiff before={previous} after={work.sql} />
      )}
      {work.failed && (
        <p className="mt-1 text-fg-muted">
          {WHAT_WENT_WRONG[work.failed.kind]} {work.failed.message}
        </p>
      )}
    </li>
  )
}

/** The try, with what changed from the one before: struck through or underlined, not colour alone. */
function SqlDiff({ before, after }: { before: string; after: string }) {
  return (
    <pre className={CODE}>
      <code>
        {diffWords(before, after).map((part, index) => {
          const key = `${index}-${part.kind}`
          if (part.kind === 'same') return <span key={key}>{part.text}</span>
          // Many screen readers don't announce <del> and <ins>, so these say it in words.
          return part.kind === 'removed' ? (
            <del key={key} className="rounded-sm bg-danger/15 line-through">
              <span className="sr-only">removed: </span>
              {part.text}
            </del>
          ) : (
            <ins key={key} className="rounded-sm bg-signal/15 font-semibold underline">
              <span className="sr-only">added: </span>
              {part.text}
            </ins>
          )
        })}
      </code>
    </pre>
  )
}
