import type { AuditAction } from '@/api/audit'
import { ERROR_TITLES, type ErrorCode } from '@/api/errors'

/** Every action the audit log records (store/audit.py `Action`), in plain words. */
export const ACTION_LABELS = {
  ask: 'Question',
  rerun: 'Re-run',
  signup: 'Sign-up',
  login: 'Login',
  login_failed: 'Failed login',
  logout: 'Logout',
  llm_key_saved: 'Key saved',
  llm_key_deleted: 'Key removed',
  llm_key_refused: 'Key refused',
  connection_saved: 'Database saved',
  connection_tested: 'Database tested',
  connection_refused: 'Database refused',
  connection_deleted: 'Database removed',
  onboarding_started: 'Onboarding started',
  dictionary_edited: 'Descriptions edited',
  table_access_changed: 'Tables changed',
  row_scope_changed: 'Rows changed',
  member_added: 'Member added',
  role_changed: 'Role changed',
  member_removed: 'Member removed',
  workspace_switched: 'Workspace switched',
} as const satisfies Record<AuditAction, string>

export const ACTIONS = Object.keys(ACTION_LABELS) as AuditAction[]

/**
 * Three kinds of outcome, each with its own icon and words (colour never alone): it worked, it was
 * declined on purpose (asked back, refused, stopped), or it failed with an error code.
 */
export type OutcomeKind = 'done' | 'declined' | 'failed'

const OUTCOMES: Record<string, { kind: OutcomeKind; label: string }> = {
  answer: { kind: 'done', label: 'Answered' },
  ok: { kind: 'done', label: 'Done' },
  clarification: { kind: 'declined', label: 'Asked back' },
  refusal: { kind: 'declined', label: 'Declined' },
  cancelled: { kind: 'declined', label: 'Stopped' },
}

/** An outcome in words: the ones above, or an error code's title ("Couldn't log you in."). */
export function outcomeOf(outcome: string): { kind: OutcomeKind; label: string } {
  const known = OUTCOMES[outcome]
  if (known) return known
  // An error code we don't know yet still shows, as the code itself.
  const label = outcome in ERROR_TITLES ? ERROR_TITLES[outcome as ErrorCode] : outcome
  return { kind: 'failed', label }
}

// Codes only the browser makes (client.ts, the canvas): never in the audit log.
const BROWSER_ONLY: readonly string[] = [
  'api_unreachable',
  'api_timeout',
  'stream_interrupted',
  'stopped',
  'bad_response',
]

/** The outcome filter's choices, grouped by kind. Values are what the API stores. */
export const OUTCOME_GROUPS: { kind: OutcomeKind; label: string; values: string[] }[] = [
  { kind: 'done', label: 'Worked', values: ['answer', 'ok'] },
  { kind: 'declined', label: 'Declined', values: ['clarification', 'refusal', 'cancelled'] },
  {
    kind: 'failed',
    label: 'Failed',
    values: Object.keys(ERROR_TITLES).filter((code) => !BROWSER_ONLY.includes(code)),
  },
]
