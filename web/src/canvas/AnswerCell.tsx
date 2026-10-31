import { Download, Info, TriangleAlert } from 'lucide-react'

import type { AskResponse } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { csvFileName, downloadCsv, toCsv } from '@/lib/csv'

import { CertaintyMeter } from './CertaintyMeter'
import { SqlView } from './SqlView'

const NUMBER = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 })

/** An answer: the text, how sure it is, any warnings, then the rows and the SQL as evidence. */
export function AnswerCell({ reply }: { reply: AskResponse }) {
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

      <Tabs defaultValue="table">
        <TabsList aria-label="Evidence">
          <TabsTrigger value="table">Table</TabsTrigger>
          <TabsTrigger value="sql">SQL</TabsTrigger>
        </TabsList>
        <TabsContent value="table" className="space-y-3 pt-2">
          <RowsNote rowCount={reply.row_count} truncated={reply.truncated} />
          {reply.rows.length > 0 && <SimpleTable reply={reply} />}
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

/** A plain, accessible table of the rows. U1.4 replaces it (sorting, types, 1,000 rows). */
function SimpleTable({ reply }: { reply: AskResponse }) {
  return (
    // A scroll area must be reachable by keyboard (tabIndex 0) to scroll it with the arrow keys.
    <div
      role="region"
      aria-label="Result rows"
      tabIndex={0}
      className="max-h-96 overflow-auto rounded-lg border outline-none focus-visible:ring-3 focus-visible:ring-ring"
    >
      <table className="w-full text-sm">
        <caption className="sr-only">Result rows for: {reply.question}</caption>
        <thead className="sticky top-0 bg-muted">
          <tr>
            {reply.columns.map((column) => (
              <th key={column} scope="col" className="px-3 py-2 text-left font-medium">
                {column}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {reply.rows.map((row, rowIndex) => (
            <tr key={rowIndex} className="border-t">
              {row.map((value, columnIndex) => (
                <td
                  key={columnIndex}
                  className={
                    typeof value === 'number'
                      ? 'px-3 py-1.5 text-right tabular-nums'
                      : 'px-3 py-1.5'
                  }
                >
                  {formatValue(value)}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

function formatValue(value: unknown) {
  if (value === null) return <span className="text-fg-muted italic">null</span>
  if (typeof value === 'number') return NUMBER.format(value)
  if (typeof value === 'string') return value
  return JSON.stringify(value)
}
