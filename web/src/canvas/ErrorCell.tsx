import { CircleAlert, KeyRound, Pencil, RotateCw, SquarePen } from 'lucide-react'
import { Link } from 'react-router'

import { errorTitle } from '@/api/errors'
import { useSession } from '@/api/session'
import { Button } from '@/components/ui/button'
import { useSecondsLeft } from '@/lib/useSecondsLeft'

import type { CellError } from './state'
import { useNewConversation } from './useConversation'
import { formatWait } from './wait'

// The same question would fail the same way, and each try costs LLM quota: change it instead.
const RETRY_WONT_HELP: readonly string[] = ['unsafe_sql', 'invalid_request']

type ErrorCellProps = {
  error: CellError
  /** A question is running: Retry waits (one question at a time). */
  busy: boolean
  /** Runs the same question again, in this cell. */
  onRetry: () => void
  /** Puts the question back in the composer, to change it. */
  onRephrase: () => void
}

/** Why a question failed, and the way forward: Retry (after any wait), or Rephrase. */
export function ErrorCell({ error, busy, onRetry, onRephrase }: ErrorCellProps) {
  const secondsLeft = useSecondsLeft(error.retryAt)
  const waiting = secondsLeft > 0

  return (
    <div className="flex items-start gap-2 text-sm">
      <CircleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-danger" />
      <div className="min-w-0 flex-1">
        <p className="font-medium">{errorTitle(error.code)}</p>
        {error.code === 'no_tables_permitted' ? (
          <NoTablesNote />
        ) : (
          error.message && <p className="text-fg-muted">{error.message}</p>
        )}
        <div className="mt-2 flex flex-wrap items-center gap-x-3 gap-y-2">
          {RETRY_WONT_HELP.includes(error.code) ? (
            <Button type="button" variant="outline" onClick={onRephrase}>
              <Pencil aria-hidden="true" />
              Rephrase
            </Button>
          ) : (
            // aria-disabled, not disabled: a disabled button would drop keyboard focus.
            <Button
              type="button"
              variant="outline"
              aria-disabled={busy || waiting}
              className="aria-disabled:cursor-not-allowed aria-disabled:opacity-50"
              onClick={() => {
                if (!busy && !waiting) onRetry()
              }}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          )}
          {/* The way forward for the database errors (U7.1b): a key to save, or a new start. */}
          {error.code === 'llm_key_required' && (
            <Button asChild variant="outline">
              <Link to="/settings/key">
                <KeyRound aria-hidden="true" />
                Save your own key
              </Link>
            </Button>
          )}
          {error.code === 'conversation_other_database' && <NewConversationButton />}
          {/* Hidden from screen readers: a number changing every second would be noise. They hear
              the wait with the error (CanvasPage), and the end of it just below. */}
          {waiting && (
            <span aria-hidden="true" className="text-fg-muted tabular-nums">
              Try again in {formatWait(secondsLeft)}
            </span>
          )}
        </div>
        <p aria-live="polite" className="sr-only">
          {error.retryAt !== undefined && !waiting ? 'You can try again now.' : ''}
        </p>
      </div>
    </div>
  )
}

/** This conversation is about another database: a new one asks the database chosen now. */
function NewConversationButton() {
  const newConversation = useNewConversation()
  return (
    <Button type="button" variant="outline" onClick={newConversation}>
      <SquarePen aria-hidden="true" />
      New conversation
    </Button>
  )
}

/**
 * A role with no table of this database (U7.2, PLAN 5.2): who can change that. Admins are never
 * restricted, so this is always an analyst or a viewer: an admin of this workspace grants tables
 * per role. Instead of the API's "your workspace", its name.
 */
function NoTablesNote() {
  const workspace = useSession()?.user.workspace.name
  return (
    <p className="text-fg-muted">
      Your role can&rsquo;t use any table of this database. Ask an admin of{' '}
      {workspace ? <span className="font-medium text-fg">{workspace}</span> : 'your workspace'} for
      access: they choose which tables each role may use.
    </p>
  )
}
