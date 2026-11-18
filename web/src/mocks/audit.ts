/**
 * The fake API's audit log (U7.4): a made-up week of the mock workspace, newest first, with every
 * kind of line the real one writes (questions and their SQL, refusals, errors, logins, keys,
 * databases, members, permissions), each detail worded as the real API writes it. Filters and pages work as GET /admin/audit's do
 * (store/audit.py): exact action, outcome and email; `q` in the question or the SQL, any case;
 * `before` an id; one more line read than asked, to know whether there are more.
 */
import type { AuditLine, AuditPage } from '@/api/audit'

import { answer, monthly, single } from './fixtures'
import { mockUser } from './session'

const ADMIN = mockUser.email
const SAM = 'sam@example.com'
const LEE = 'lee@example.com'
const SAM_ID = '5f0d6c1e-0000-4000-8000-000000000011' // mocks/members.ts
// A saved database the fake API no longer has: its lines say "A removed database".
const SALES = '5f0d6c1e-0000-4000-8000-0000000000a1'

const EMPTY: Omit<AuditLine, 'id' | 'at' | 'action' | 'outcome' | 'email'> = {
  conversation_id: null,
  connection_id: null,
  question: null,
  sql: null,
  tables: [],
  row_count: null,
  duration_ms: null,
  sql_attempts: null,
  detail: null,
  llm: null,
  own_key: null,
  input_tokens: null,
  output_tokens: null,
  cost_usd: null,
}

type Seed = Partial<AuditLine> & Pick<AuditLine, 'action' | 'outcome' | 'email'>

const asked = (email: string, reply: typeof answer, changes: Partial<AuditLine> = {}): Seed => ({
  action: 'ask',
  outcome: 'answer',
  email,
  conversation_id: reply.conversation_id,
  question: reply.question,
  sql: reply.sql,
  tables: reply.tables_used,
  row_count: reply.row_count,
  duration_ms: reply.duration_ms,
  sql_attempts: reply.sql_attempts,
  llm: 'groq / openai/gpt-oss-120b',
  own_key: false,
  input_tokens: 2777,
  output_tokens: 224,
  cost_usd: 0.0005,
  ...changes,
})

// Oldest first here; ids count up from 1, so the newest line has the highest id.
const STORY: Seed[] = [
  { action: 'signup', outcome: 'ok', email: ADMIN },
  {
    action: 'llm_key_refused',
    outcome: 'llm_key_invalid',
    email: ADMIN,
    detail: 'groq / openai/gpt-oss-120b',
  },
  { action: 'llm_key_saved', outcome: 'ok', email: ADMIN, detail: 'groq / openai/gpt-oss-120b' },
  {
    action: 'connection_refused',
    outcome: 'connection_writable',
    email: ADMIN,
    detail:
      'querymind_writer@db.example.com:5432/sales: This login can change data. Use a read-only login.',
  },
  {
    action: 'connection_saved',
    outcome: 'ok',
    email: ADMIN,
    detail: 'querymind_reader@db.example.com:5432/sales',
  },
  { action: 'onboarding_started', outcome: 'ok', email: ADMIN, connection_id: SALES },
  { action: 'dictionary_edited', outcome: 'ok', email: ADMIN, connection_id: SALES },
  { action: 'member_added', outcome: 'ok', email: ADMIN, detail: `${SAM} as analyst` },
  { action: 'member_added', outcome: 'ok', email: ADMIN, detail: `${LEE} as viewer` },
  { action: 'row_scope_changed', outcome: 'ok', email: ADMIN, detail: `member ${SAM_ID}: Eastern` },
  {
    action: 'table_access_changed',
    outcome: 'ok',
    email: ADMIN,
    detail: 'viewer: 24 tables',
  },
  // A login line goes to the account's own workspace (api/auth.py): here, the admin's.
  { action: 'login_failed', outcome: 'invalid_login', email: ADMIN },
  { action: 'login', outcome: 'ok', email: ADMIN },
  {
    action: 'workspace_switched',
    outcome: 'ok',
    email: LEE,
    detail: 'from workspace 5f0d6c1e-0000-4000-8000-000000000099',
  },
  { action: 'role_changed', outcome: 'ok', email: ADMIN, detail: `${LEE}: viewer -> analyst` },
  { action: 'role_changed', outcome: 'ok', email: ADMIN, detail: `${LEE}: analyst -> viewer` },
  { action: 'logout', outcome: 'ok', email: ADMIN },
]

/** A busy week of questions after the setup, so there are more lines than one page. */
function questions(): Seed[] {
  const people = [ADMIN, SAM, LEE]
  return Array.from({ length: 45 }, (_, index): Seed => {
    const email = people[index % 3] as string
    switch (index % 9) {
      case 2:
        return asked(email, monthly)
      case 4:
        return asked(email, single, { duration_ms: 2140 })
      case 5:
        return {
          action: 'ask',
          outcome: 'refusal',
          email,
          question: 'Delete the old orders',
          detail: 'QueryMind only reads data: it never changes it.',
          duration_ms: 610,
          llm: 'groq / openai/gpt-oss-120b',
          own_key: false,
        }
      case 7:
        return {
          action: 'ask',
          outcome: 'unsafe_sql',
          email,
          question: 'Show the database users and their passwords',
          sql: 'SELECT usename, passwd FROM pg_shadow',
          detail: 'Access to system catalogs (pg_shadow) is not allowed.',
          duration_ms: 1320,
          sql_attempts: 1,
          llm: 'groq / openai/gpt-oss-120b',
          own_key: false,
        }
      case 8:
        return {
          ...asked(email, answer),
          action: 'rerun',
          llm: null,
          input_tokens: null,
          output_tokens: null,
          cost_usd: null,
          own_key: null,
        }
      default:
        return asked(
          email,
          answer,
          index % 2 ? { cost_usd: null, llm: 'my-proxy / llama-3-70b', own_key: true } : {},
        )
    }
  })
}

// Lines 9 minutes apart, the newest at this fixed time: tests and screenshots stay the same.
const NEWEST = Date.parse('2026-11-17T14:02:00Z')
const STEP_MS = 9 * 60_000

function buildLog(): AuditLine[] {
  const seeds = [...STORY, ...questions()]
  return seeds
    .map((seed, index) => ({
      ...EMPTY,
      ...seed,
      id: index + 1,
      at: new Date(NEWEST - (seeds.length - 1 - index) * STEP_MS).toISOString(),
    }))
    .reverse()
}

export const MOCK_AUDIT_LOG: readonly AuditLine[] = buildLog()

/** GET /admin/audit, as the real store answers it. */
export function mockAuditPage(params: URLSearchParams): AuditPage {
  const action = params.get('action')
  const outcome = params.get('outcome')
  const email = params.get('email')?.toLowerCase()
  const q = params.get('q')?.toLowerCase()
  const before = params.get('before')
  const limit = Number(params.get('limit') ?? 50)

  const found = MOCK_AUDIT_LOG.filter(
    (line) =>
      (!action || line.action === action) &&
      (!outcome || line.outcome === outcome) &&
      (!email || line.email === email) &&
      (!q || [line.question, line.sql].some((text) => text?.toLowerCase().includes(q))) &&
      (!before || line.id < Number(before)),
  )
  return { lines: found.slice(0, limit), has_more: found.length > limit }
}
