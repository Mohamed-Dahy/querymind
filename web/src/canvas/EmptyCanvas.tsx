import { ArrowRight } from 'lucide-react'

import { Stagger, StaggerItem } from '@/motion'

import { EXAMPLE_GROUPS } from './examples'

type EmptyCanvasProps = {
  /** Asks the clicked question, as if it had been typed. */
  onAsk: (question: string) => void
  /** While a question runs, the examples wait too. */
  busy?: boolean
}

/**
 * What the canvas shows before the first question: example questions, grouped by database. Signal
 * Lab (U8.3): "probe" cards that arrive one after another and lift under the pointer; each question
 * is a probe you can send.
 */
export function EmptyCanvas({ onAsk, busy = false }: EmptyCanvasProps) {
  return (
    <Stagger className="grid gap-4 sm:grid-cols-3">
      {EXAMPLE_GROUPS.map(({ title, description, questions }, index) => {
        const headingId = `examples-${title.toLowerCase().replaceAll(' ', '-')}`
        return (
          <StaggerItem
            as="section"
            key={title}
            aria-labelledby={headingId}
            className="lift rounded-cell border bg-surface p-5"
          >
            <p aria-hidden="true" className="label-mono mb-2">
              {`PROBE ${String(index + 1).padStart(2, '0')}`}
            </p>
            <h2 id={headingId} className="text-base font-semibold">
              {title}
            </h2>
            <p className="mt-1 text-sm text-fg-muted">{description}</p>
            <ul className="mt-3 space-y-1">
              {questions.map((question) => (
                <li key={question}>
                  <button
                    type="button"
                    onClick={() => {
                      if (!busy) onAsk(question)
                    }}
                    aria-disabled={busy}
                    className="group/probe -mx-2 flex min-h-11 w-[calc(100%+1rem)] items-start gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                  >
                    <ArrowRight
                      aria-hidden="true"
                      className="mt-0.5 size-4 shrink-0 text-signal transition-transform duration-150 group-hover/probe:translate-x-0.5"
                    />
                    {question}
                  </button>
                </li>
              ))}
            </ul>
          </StaggerItem>
        )
      })}
    </Stagger>
  )
}
