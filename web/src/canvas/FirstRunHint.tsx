import { useQuery } from '@tanstack/react-query'
import { Lightbulb } from 'lucide-react'

import { conversationListQuery, HISTORY_PAGE } from '@/api/conversations'
import { Kbd } from '@/components/Kbd'
import { MODIFIER } from '@/lib/keys'

/**
 * One line for a first visit, above the example questions: what to try and what an answer shows.
 * Shown while there's no saved conversation, or when history is off (then nobody can tell).
 * Nothing while the history list loads, so a returning visitor never sees it flash.
 */
export function FirstRunHint() {
  // The history rail's own first request, so this costs no extra call.
  const history = useQuery(conversationListQuery('', HISTORY_PAGE))
  const firstRun = history.isError || history.data?.conversations.length === 0
  if (!firstRun) return null

  return (
    <p className="mb-4 flex items-start gap-2 rounded-lg border border-info/40 bg-info/10 px-3 py-2 text-sm">
      <Lightbulb aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-info" />
      <span>
        New here? Pick a question below, or type your own. Each answer shows its steps, the SQL it
        ran and how sure it is. <Kbd>{MODIFIER}</Kbd> <Kbd>K</Kbd> finds any page, table or business
        term.
      </span>
    </p>
  )
}
