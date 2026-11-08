import { useMutation } from '@tanstack/react-query'
import { toast } from 'sonner'
import { Database, Download, Info, ListChecks, RotateCw, TriangleAlert } from 'lucide-react'
import { lazy, Suspense, useMemo, useRef, useState } from 'react'

import { ApiError, type AskResponse } from '@/api/client'
import { rerunTurn, type RerunResult } from '@/api/conversations'
import { errorTitle } from '@/api/errors'
import { OverlayLoading } from '@/components/OverlayLoading'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { csvFileName, downloadCsv, toCsv } from '@/lib/csv'

import { CertaintyMeter } from './CertaintyMeter'
import { pickChart } from './pickChart'
import { ResultTable } from './ResultTable'
import { SqlView } from './SqlView'
import type { SavedInfo } from './state'

// Recharts is big: it downloads only when a chart is first shown, not with the page.
const ResultChart = lazy(() =>
  import('./ResultChart').then(({ ResultChart }) => ({ default: ResultChart })),
)

// Most answers never open it, so it downloads on the first click.
const ShowYourWork = lazy(() =>
  import('./ShowYourWork').then(({ ShowYourWork }) => ({ default: ShowYourWork })),
)

/**
 * Which database the answer came from, worked out from the tables its SQL read (not by the LLM).
 * One database can hold several businesses, e.g. Northwind's `employees` and Chinook's `employee`.
 */
function SourceLine({ sources }: { sources: AskResponse['sources'] }) {
  if (sources.length === 0) return null
  return (
    <p className="flex items-start gap-1.5 text-sm text-fg-muted">
      <Database aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
      <span>
        From{' '}
        {sources.map((source, index) => (
          <span key={source.database}>
            {index > 0 && ' and '}
            <span className="font-medium text-fg">{source.database}</span>
          </span>
        ))}
        : {sources.map((source) => source.description).join(' ')}
      </span>
    </p>
  )
}

/** An answer: the text, how sure it is, any warnings, then the rows and the SQL as evidence. */
export function AnswerCell({
  reply,
  saved,
  onRerun,
}: {
  reply: AskResponse
  /** Set when this answer came from the saved history. */
  saved?: SavedInfo | undefined
  onRerun?: (result: RerunResult) => void
}) {
  const chart = useMemo(() => pickChart(reply.columns, reply.rows), [reply.columns, reply.rows])
  // Rows aren't saved, so a reopened answer has to fetch them again to show them.
  const rowsNotStored = saved !== undefined && saved.freshAt === undefined && reply.row_count > 0
  const [workOpen, setWorkOpen] = useState(false)
  // Stays mounted after the first open, so closing it can animate and give the focus back.
  const [workLoaded, setWorkLoaded] = useState(false)
  const workButton = useRef<HTMLButtonElement>(null)

  return (
    <div className="space-y-3">
      <p className="text-base">{reply.answer}</p>
      <SourceLine sources={reply.sources} />

      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <CertaintyMeter level={reply.confidence} reasons={reply.confidence_reasons} />
        {!reply.answer_from_llm && <TemplateBadge />}
        <Button
          ref={workButton}
          type="button"
          variant="outline"
          aria-haspopup="dialog"
          className="ml-auto"
          onClick={() => {
            setWorkLoaded(true)
            setWorkOpen(true)
          }}
        >
          <ListChecks aria-hidden="true" />
          Show your work
        </Button>
      </div>
      {workLoaded && (
        <Suspense fallback={<OverlayLoading />}>
          <ShowYourWork
            reply={reply}
            open={workOpen}
            onOpenChange={setWorkOpen}
            opener={workButton}
          />
        </Suspense>
      )}

      {reply.warnings.length > 0 && (
        <ul aria-label="Warnings" className="space-y-1.5">
          {reply.warnings.map((warning, index) => (
            <li
              key={`${warning.code}-${index}`}
              className="flex items-start gap-2 rounded-lg border border-warn/40 bg-warn/10 px-3 py-2 text-sm"
            >
              <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
              {warning.message}
            </li>
          ))}
        </ul>
      )}

      {saved?.freshAt !== undefined && <FreshRowsNote saved={saved} freshAt={saved.freshAt} />}

      {/* The chart first when one fits; otherwise the table opens (one row, no numbers…). The key:
          after a re-run the Chart tab may appear, and it should open on it. */}
      <Tabs
        key={saved?.freshAt ?? 'first'}
        defaultValue={chart.kind === 'none' ? 'table' : 'chart'}
      >
        <TabsList aria-label="Evidence">
          {chart.kind !== 'none' && <TabsTrigger value="chart">Chart</TabsTrigger>}
          <TabsTrigger value="table">Table</TabsTrigger>
          <TabsTrigger value="sql">SQL</TabsTrigger>
        </TabsList>
        {chart.kind !== 'none' && (
          <TabsContent value="chart" className="pt-2">
            <Suspense fallback={<Skeleton className="h-64 w-full motion-reduce:animate-none" />}>
              <ResultChart spec={chart} />
            </Suspense>
          </TabsContent>
        )}
        <TabsContent value="table" className="space-y-3 pt-2">
          {rowsNotStored ? (
            <RerunPanel reply={reply} n={saved?.n ?? 0} onRerun={onRerun} />
          ) : (
            <RowsNote rowCount={reply.row_count} truncated={reply.truncated} />
          )}
          {reply.rows.length > 0 && (
            <ResultTable columns={reply.columns} rows={reply.rows} question={reply.question} />
          )}
          {!rowsNotStored && (
            <Button
              type="button"
              variant="outline"
              disabled={reply.rows.length === 0}
              onClick={() => {
                const name = csvFileName(reply.question)
                downloadCsv(name, toCsv(reply.columns, reply.rows))
                // A download gives no sign in the page itself; this one is polite and goes by itself.
                toast(`Downloaded ${name}`, {
                  description: `${reply.rows.length.toLocaleString('en')} ${reply.rows.length === 1 ? 'row' : 'rows'}`,
                })
              }}
            >
              <Download aria-hidden="true" />
              Download CSV
            </Button>
          )}
        </TabsContent>
        {/* Radix mounts a tab's content only when it opens, so Shiki loads on the first SQL click. */}
        <TabsContent value="sql" className="pt-2">
          <SqlView sql={reply.sql} explanation={reply.explanation} />
        </TabsContent>
      </Tabs>
    </div>
  )
}

/** The LLM's wording was replaced by a sentence built from the rows (agent/answer.py). */
function TemplateBadge() {
  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <button
          type="button"
          className="inline-flex min-h-8 items-center gap-1.5 rounded-lg border border-input px-2 text-sm text-fg-muted pointer-coarse:min-h-11"
        >
          <Info aria-hidden="true" className="size-4" />
          Answer from a template
        </button>
      </TooltipTrigger>
      <TooltipContent className="max-w-xs">
        The AI&apos;s wording didn&apos;t pass our checks, so this sentence was built directly from
        the rows. The numbers are exact; the wording is plain.
      </TooltipContent>
    </Tooltip>
  )
}

/** The rows now on screen are newer than the words above them, and may not match. */
function FreshRowsNote({ saved, freshAt }: { saved: SavedInfo; freshAt: string }) {
  const when = (iso: string) =>
    new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' }).format(
      new Date(iso),
    )
  return (
    <p className="flex items-start gap-2 text-sm">
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info" />
      Fresh rows from {when(freshAt)}. The answer above is from {when(saved.createdAt)}, so they may
      not match.
    </p>
  )
}

/** A saved answer's table before its re-run: why it is empty, and the way to fill it. */
function RerunPanel({
  reply,
  n,
  onRerun,
}: {
  reply: AskResponse
  n: number
  onRerun: ((result: RerunResult) => void) | undefined
}) {
  const rerun = useMutation({
    mutationFn: () => rerunTurn(reply.conversation_id, n),
    onSuccess: (result) => onRerun?.(result),
  })
  const { error } = rerun
  return (
    <div className="space-y-2 text-sm">
      <p>
        The {reply.row_count.toLocaleString()} {reply.row_count === 1 ? 'row' : 'rows'} of this
        answer aren&apos;t stored. Run its SQL again to see today&apos;s, with no AI involved.
      </p>
      {/* aria-disabled, not disabled: a disabled button would drop keyboard focus. */}
      <Button
        type="button"
        variant="outline"
        aria-disabled={rerun.isPending}
        className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
        onClick={() => {
          if (!rerun.isPending) rerun.mutate()
        }}
      >
        <RotateCw aria-hidden="true" />
        {rerun.isPending ? 'Running…' : 'Re-run for rows'}
      </Button>
      {error && (
        <p role="alert" className="flex items-start gap-1.5 text-danger">
          <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
          {errorTitle(error instanceof ApiError ? error.code : 'unexpected')}
        </p>
      )}
    </div>
  )
}

function RowsNote({ rowCount, truncated }: { rowCount: number; truncated: boolean }) {
  const rows = `${rowCount.toLocaleString()} row${rowCount === 1 ? '' : 's'}`
  if (!truncated) return <p className="text-sm text-fg-muted">{rows}</p>
  return (
    <p className="flex items-start gap-2 text-sm">
      <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info" />
      Showing the first {rows}: more rows matched than the row limit allows.
    </p>
  )
}
