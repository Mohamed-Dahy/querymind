import { Download, Info, TriangleAlert } from 'lucide-react'
import { lazy, Suspense, useMemo } from 'react'

import type { AskResponse } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { csvFileName, downloadCsv, toCsv } from '@/lib/csv'

import { CertaintyMeter } from './CertaintyMeter'
import { pickChart } from './pickChart'
import { ResultTable } from './ResultTable'
import { SqlView } from './SqlView'

// Recharts is big: it downloads only when a chart is first shown, not with the page.
const ResultChart = lazy(() =>
  import('./ResultChart').then(({ ResultChart }) => ({ default: ResultChart })),
)

/** An answer: the text, how sure it is, any warnings, then the rows and the SQL as evidence. */
export function AnswerCell({ reply }: { reply: AskResponse }) {
  const chart = useMemo(() => pickChart(reply.columns, reply.rows), [reply.columns, reply.rows])

  return (
    <div className="space-y-3">
      <p className="text-base">{reply.answer}</p>

      <div className="flex flex-wrap items-start gap-x-4 gap-y-2">
        <CertaintyMeter level={reply.confidence} reasons={reply.confidence_reasons} />
        {!reply.answer_from_llm && <TemplateBadge />}
      </div>

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

      {/* The chart first when one fits; otherwise the table opens (one row, no numbers…). */}
      <Tabs defaultValue={chart.kind === 'none' ? 'table' : 'chart'}>
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
          <RowsNote rowCount={reply.row_count} truncated={reply.truncated} />
          {reply.rows.length > 0 && (
            <ResultTable columns={reply.columns} rows={reply.rows} question={reply.question} />
          )}
          <Button
            type="button"
            variant="outline"
            disabled={reply.rows.length === 0}
            onClick={() =>
              downloadCsv(csvFileName(reply.question), toCsv(reply.columns, reply.rows))
            }
          >
            <Download aria-hidden="true" />
            Download CSV
          </Button>
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
