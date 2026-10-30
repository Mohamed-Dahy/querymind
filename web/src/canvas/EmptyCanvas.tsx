import { MessageSquareText } from 'lucide-react'

import { EXAMPLE_GROUPS } from './examples'

type EmptyCanvasProps = {
  /** Asks the clicked question, as if it had been typed. */
  onAsk: (question: string) => void
  /** While a question runs, the examples wait too. */
  busy?: boolean
}

/** What the canvas shows before the first question: example questions, grouped by database. */
export function EmptyCanvas({ onAsk, busy = false }: EmptyCanvasProps) {
  return (
    <div className="grid gap-4 sm:grid-cols-3">
      {EXAMPLE_GROUPS.map(({ title, description, questions }) => {
        const headingId = `examples-${title.toLowerCase().replaceAll(' ', '-')}`
        return (
          <section
            key={title}
            aria-labelledby={headingId}
            className="rounded-cell border bg-surface p-4"
          >
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
                    className="flex min-h-11 w-full items-start gap-2 rounded-lg px-2 py-2 text-left text-sm transition-colors hover:bg-muted focus-visible:ring-3 focus-visible:ring-ring focus-visible:outline-none aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
                  >
                    <MessageSquareText
                      aria-hidden="true"
                      className="mt-0.5 size-4 shrink-0 text-signal"
                    />
                    {question}
                  </button>
                </li>
              ))}
            </ul>
          </section>
        )
      })}
    </div>
  )
}
