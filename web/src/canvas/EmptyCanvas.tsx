import { ArrowRight } from 'lucide-react'

import { Stagger, StaggerItem } from '@/motion'

import { EXAMPLE_GROUPS } from './examples'

type EmptyCanvasProps = {
  /** Asks the clicked question, as if it had been typed. */
  onAsk: (question: string) => void
  /** While a question runs, the examples wait too. */
  busy?: boolean
}

// Where each group's numbers start: the questions are numbered 01, 02… straight through every group,
// like the steps of one test plan.
const FIRST_NUMBER = EXAMPLE_GROUPS.map((_, index) =>
  EXAMPLE_GROUPS.slice(0, index).reduce((count, group) => count + group.questions.length, 1),
)

/**
 * What the home console shows under the question box: example questions, grouped by database. U9.3
 * (ADR 027): a numbered list on the Swiss grid, not cards. Each group is a row (its name on the left,
 * its questions on the right); each question is one line with its number in lime. The numbers are
 * decoration (`aria-hidden`), so a question's button is still named by the question alone.
 */
export function EmptyCanvas({ onAsk, busy = false }: EmptyCanvasProps) {
  return (
    <Stagger className="border-t">
      {EXAMPLE_GROUPS.map(({ title, description, questions }, index) => {
        const headingId = `examples-${title.toLowerCase().replaceAll(' ', '-')}`
        const first = FIRST_NUMBER[index] ?? 1
        return (
          <StaggerItem
            as="section"
            key={title}
            aria-labelledby={headingId}
            className="grid gap-x-8 gap-y-3 border-b py-6 md:grid-cols-12"
          >
            <div className="md:col-span-4">
              <p aria-hidden="true" className="label-mono mb-2">
                {`PROBE ${String.fromCharCode(65 + index)} · ${questions.length} QUESTIONS`}
              </p>
              <h2 id={headingId} className="text-lg font-semibold tracking-tight">
                {title}
              </h2>
              <p className="mt-1 text-sm text-fg-muted">{description}</p>
            </div>
            <ol className="md:col-span-8">
              {questions.map((question, position) => (
                <li key={question} className="border-b border-dashed last:border-b-0">
                  <button
                    type="button"
                    onClick={() => {
                      if (!busy) onAsk(question)
                    }}
                    aria-disabled={busy}
                    className="group/probe flex min-h-11 w-full items-baseline gap-4 rounded-[4px] px-2 py-2.5 text-left transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                  >
                    <span
                      aria-hidden="true"
                      className="w-6 shrink-0 font-mono text-xs font-semibold text-signal tabular-nums"
                    >
                      {String(first + position).padStart(2, '0')}
                    </span>
                    <span className="min-w-0 flex-1">{question}</span>
                    {/* Shown on hover and keyboard focus: "this line sends". Opacity only, no shift. */}
                    <ArrowRight
                      aria-hidden="true"
                      className="size-4 shrink-0 self-center text-signal opacity-0 transition-opacity duration-150 group-hover/probe:opacity-100 group-focus-visible/probe:opacity-100"
                    />
                  </button>
                </li>
              ))}
            </ol>
          </StaggerItem>
        )
      })}
    </Stagger>
  )
}
