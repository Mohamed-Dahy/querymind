import { useState } from 'react'

import { Composer } from '@/canvas/Composer'
import { EmptyCanvas } from '@/canvas/EmptyCanvas'

/** The canvas: example questions, the composer, and (from U1.3) one answer cell per question. */
export function CanvasPage() {
  // Until U1.3 sends questions to the API, the page only confirms what it received.
  const [lastQuestion, setLastQuestion] = useState<string | null>(null)

  return (
    <div className="mx-auto flex min-h-full max-w-5xl flex-col px-4 pt-10">
      <h1 className="text-2xl">Ask about your data</h1>
      <p className="mt-2 text-fg-muted">
        Questions about the Northwind shop and the Chinook music store, in plain language. Every
        answer shows the SQL that produced it.
      </p>

      <div className="mt-8 flex-1">
        <EmptyCanvas onAsk={setLastQuestion} />
      </div>

      {/* Stays at the bottom of the screen while the cells above it scroll. */}
      <div className="sticky bottom-0 mt-8 bg-linear-to-t from-bg from-70% pt-4 pb-4">
        <p aria-live="polite" className="mb-2 text-sm text-fg-muted empty:hidden">
          {lastQuestion && `Received: “${lastQuestion}”. Answers arrive in the next step (U1.3).`}
        </p>
        <Composer onSubmit={setLastQuestion} />
      </div>
    </div>
  )
}
