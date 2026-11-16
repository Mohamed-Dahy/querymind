import { ShieldBan } from 'lucide-react'

import type { RefusalResponse } from '@/api/client'

import { UsageLine } from './UsageLine'

/**
 * QueryMind won't do this. Calm on purpose: a refusal is the product working, not an error. It
 * doesn't say "no SQL ran": the LLM may refuse after a failed attempt. This sentence is always
 * true, because the agent's database role can only read.
 */
export function RefusalCell({ reply }: { reply: RefusalResponse }) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <ShieldBan aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-fg-muted" />
      <div>
        <p>{reply.message}</p>
        <p className="text-fg-muted">Nothing in the database was changed.</p>
        <div className="mt-2">
          <UsageLine usage={reply.usage} />
        </div>
      </div>
    </div>
  )
}
