import { Cpu } from 'lucide-react'

import { usageParts, type LLMUsage } from './usage'

/**
 * Which model answered, on whose key, and what it cost (5.8b): one muted line under the reply.
 * The cost is the list price for those tokens (a free tier may have charged nothing). Nothing for
 * a reply without usage: saved before 5.8b, or a re-run (no LLM call).
 */
export function UsageLine({ usage }: { usage: LLMUsage | null | undefined }) {
  if (!usage) return null
  return (
    <p className="flex items-start gap-1.5 text-xs text-fg-muted">
      <Cpu aria-hidden="true" className="mt-px size-3.5 shrink-0" />
      <span>
        <span className="sr-only">Answered by </span>
        {usageParts(usage).join(' · ')}
      </span>
    </p>
  )
}
