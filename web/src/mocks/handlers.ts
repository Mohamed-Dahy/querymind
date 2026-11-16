import { delay, http, HttpResponse } from 'msw'

import {
  addSavedQuestion,
  deleteStored,
  getStored,
  listSavedQuestions,
  listStored,
  questionsUsing,
  recordTurn,
  removeSavedQuestion,
  rerunStored,
  updateStored,
} from './conversations'

import type { LoginRequest, SignupRequest } from '@/api/auth'
import type { AskReply, AskRequest } from '@/api/client'
import type { TokenResponse } from '@/api/session'
import type { ConversationUpdate } from '@/api/conversations'
import type { MemberInfo, Role } from '@/api/members'
import type { DictionaryEdit } from '@/api/onboarding'
import type { SaveConnectionRequest, SaveLLMKeyRequest } from '@/api/settings'
import type { StreamEvent, TraceEvent } from '@/api/stream'
import {
  answer,
  answerStream,
  answerWithRows,
  catalog,
  clarification,
  clarificationStream,
  errors,
  evals,
  followup,
  followupStream,
  health,
  monthly,
  monthlyStream,
  providers,
  refusal,
  refusalStream,
  repair,
  repairStream,
  semantic,
  single,
  singleStream,
  type MockError,
  type MockErrorCode,
} from './fixtures'
import {
  editMockDictionary,
  forgetMockOnboarding,
  isMockOnboardingRunning,
  mockDictionary,
  onboardedOverview,
  pollMockOnboarding,
  startMockOnboarding,
} from './onboarding'
import {
  addMockMember,
  loadMockMembers,
  MEMBER_MESSAGES,
  removeMockMember,
  setMockRole,
} from './members'
import {
  currentMockUser,
  mockLoggedOut,
  mockWorkspaces,
  setMockLoggedOut,
  switchMockWorkspace,
} from './session'
import {
  CONNECTION_TRIGGERS,
  deleteMockConnection,
  findMockConnection,
  deleteMockKey,
  INVALID_KEY,
  isMockConnection,
  loadMockConnections,
  loadMockKey,
  NOT_ONBOARDED,
  savedDatabaseCatalog,
  refusedMessage,
  saveMockConnection,
  saveMockKey,
  testMockConnection,
  TOO_MANY_TRIES_KEY,
} from './settings'

// The paths the browser calls. MSW answers before Vite's proxy, so the /api prefix is still there.
export const HEALTH_URL = '/api/health'
export const ASK_URL = '/api/ask'
export const STREAM_URL = '/api/ask/stream'
const CONVERSATION_URL = '/api/conversations/:id'
export const SAVED_QUESTIONS_URL = '/api/saved-questions'
export const LLM_KEY_URL = '/api/llm/key'
export const CONNECTIONS_URL = '/api/connections'
export const MEMBERS_URL = '/api/admin/members'

export const AUTH_URL = {
  refresh: '/api/auth/refresh',
  login: '/api/auth/login',
  signup: '/api/auth/signup',
  logout: '/api/auth/logout',
  me: '/api/auth/me',
}
/** Log in with this password to see "Wrong email or password." */
export const WRONG_PASSWORD = 'wrong password'
/** Sign up with this email to see "That email already has an account." */
export const TAKEN_EMAIL = 'taken@example.com'

function tokens(email: string): TokenResponse {
  return {
    access_token: 'mock-access-token',
    token_type: 'bearer',
    expires_in: 900,
    user: { ...currentMockUser(), email: email.toLowerCase() },
  }
}

// In the browser's mock mode the steps come one by one, so they can be seen. Tests don't wait.
const EVENT_DELAY_MS = import.meta.env.MODE === 'mock' ? 60 : 0

/** The API's error body with its real status, e.g. apiError('db_unavailable') → 503. */
export function apiError(code: MockErrorCode) {
  const { status, message, retry_after_seconds: wait }: MockError = errors[code]
  if (wait === undefined) return HttpResponse.json({ error: code, message }, { status })
  return HttpResponse.json(
    { error: code, message, retry_after_seconds: wait },
    { status, headers: { 'Retry-After': String(wait) } },
  )
}

// Mock mode only: typing "error: db_unavailable" shows that error, to design the error states.
const ERROR_PREFIX = /^error:\s*(\w+)$/i
const CHANGES_DATA = /\b(delete|drop|update|insert|truncate|alter)\b/i
const AMBIGUOUS = /\b(popular|best)\b/i
// Mock mode only: "rows: 1000" answers with that many made-up rows, to try the table (max 1,000).
const MANY_ROWS = /\brows:\s*(\d+)\b/i
// The two chart cases (U1.5). Only "how many customers…": "How many orders did each country place?"
// must still get the bar-chart answer.
const PER_MONTH = /\bper month\b|\bmonthly\b/i
const ONE_NUMBER = /^how many customers\b/i
// U2's repair loop. "in each": "How many orders did each country place?" stays a plain answer.
const NEEDS_REPAIR = /\bin each country\b/i
// Mock mode only: "stream: broken" makes the stream fail at once, so the UI falls back to POST /ask.
const BROKEN_STREAM = /\bstream:\s*broken\b/i
// Mock mode only: "stream: stuck" sends a few steps and then waits forever, to try the Stop button.
const STUCK_STREAM = /\bstream:\s*stuck\b/i

/**
 * A recorded reply and its events that fit the question, so mock mode feels like the real API: a
 * follow-up continues, "delete…" is refused, "best…" asks back, anything else gets the recorded
 * answer. Like the real API, a question without a conversation starts a new one with a fresh id
 * (the recorded id would make every new conversation the same one on the canvas).
 */
function recordedFor(request: AskRequest): { reply: AskReply; events: TraceEvent[] } {
  const { question } = request
  const many = MANY_ROWS.exec(question)?.[1]
  if (many) {
    const conversation_id = request.conversation_id ?? crypto.randomUUID()
    const reply = { ...answerWithRows(Math.min(Number(many), 1000)), question, conversation_id }
    return { reply, events: answerStream }
  }
  if (request.conversation_id) {
    const reply = { ...followup, question, conversation_id: request.conversation_id }
    return { reply, events: followupStream }
  }
  const conversation_id = crypto.randomUUID()
  const fitted = <Reply extends AskReply>(recorded: Reply, events: TraceEvent[]) => ({
    reply: { ...recorded, question, conversation_id },
    events,
  })
  if (CHANGES_DATA.test(question)) return fitted(refusal, refusalStream)
  if (AMBIGUOUS.test(question)) return fitted(clarification, clarificationStream)
  if (PER_MONTH.test(question)) return fitted(monthly, monthlyStream)
  if (NEEDS_REPAIR.test(question)) return fitted(repair, repairStream)
  if (ONE_NUMBER.test(question)) return fitted(single, singleStream)
  return fitted(answer, answerStream)
}

export function replyFor(request: AskRequest): AskReply {
  return recordedFor(request).reply
}

/**
 * These events as POST /ask/stream sends them: one `data:` line each, then a blank line. `stuck`: it
 * stays open afterwards, like a step that is taking a long time.
 */
export function streamOf(events: readonly StreamEvent[], { stuck = false } = {}) {
  const encoder = new TextEncoder()
  const body = new ReadableStream<Uint8Array>({
    async start(controller) {
      for (const event of events) {
        if (EVENT_DELAY_MS > 0) await delay(EVENT_DELAY_MS)
        controller.enqueue(encoder.encode(`data: ${JSON.stringify(event)}\n\n`))
      }
      if (!stuck) controller.close()
    },
  })
  return new HttpResponse(body, { headers: { 'Content-Type': 'text/event-stream; charset=utf-8' } })
}

/** The whole recorded stream for a question: its events, then the reply. */
export function replyStream(request: AskRequest) {
  const { reply, events } = recordedFor(request)
  recordTurn(reply, events, request.connection_id ?? null) // the real API saves before the final event, too
  return streamOf([...events, { type: 'final', reply }])
}

/** A run that fails partway: the first steps, then the API's error, with status 200 already sent. */
export function errorStream(code: MockErrorCode) {
  const { message, retry_after_seconds: wait }: MockError = errors[code]
  return streamOf([
    ...answerStream.slice(0, 4),
    {
      type: 'error',
      error: { error: code, message, ...(wait !== undefined && { retry_after_seconds: wait }) },
    },
  ])
}

/**
 * Mock mode and tests only: `localStorage['querymind-mock-delay'] = '3000'` holds every reply that
 * many milliseconds, to see (and test) the loading states. Unset, the fake API answers at once.
 */
export const MOCK_DELAY_KEY = 'querymind-mock-delay'

function mockDelayMs(): number {
  try {
    return Number(localStorage.getItem(MOCK_DELAY_KEY)) || 0
  } catch {
    return 0
  }
}

export const handlers = [
  // Returns nothing, so after its wait MSW goes on to the handler that really answers.
  http.all('/api/*', async () => {
    const ms = mockDelayMs()
    if (ms > 0) await delay(ms)
  }),
  http.get(HEALTH_URL, () => HttpResponse.json(health)),
  // Logins (U7.1). Any email and password work, except the few below that show each error.
  http.post(AUTH_URL.refresh, () =>
    mockLoggedOut()
      ? apiError('not_authenticated')
      : HttpResponse.json(tokens(currentMockUser().email)),
  ),
  http.post(AUTH_URL.login, async ({ request }) => {
    const body = (await request.json()) as LoginRequest
    if (body.password === WRONG_PASSWORD) return apiError('invalid_login')
    setMockLoggedOut(false)
    return HttpResponse.json(tokens(body.email))
  }),
  http.post(AUTH_URL.signup, async ({ request }) => {
    const body = (await request.json()) as SignupRequest
    if (body.email === TAKEN_EMAIL) return apiError('email_taken')
    setMockLoggedOut(false)
    return HttpResponse.json(tokens(body.email), { status: 201 })
  }),
  http.post(AUTH_URL.logout, () => {
    setMockLoggedOut(true)
    return new HttpResponse(null, { status: 204 })
  }),
  http.get(AUTH_URL.me, () => HttpResponse.json(currentMockUser())),
  // Workspaces (U7.1d): yours and Acme Sales (a viewer there); a switch moves the fake login.
  http.get('/api/auth/workspaces', () => HttpResponse.json({ workspaces: mockWorkspaces() })),
  http.post('/api/auth/switch', async ({ request }) => {
    const { workspace_id: id } = (await request.json()) as { workspace_id: string }
    if (!switchMockWorkspace(id)) return memberRefusal('member_not_found')
    return HttpResponse.json(tokens(currentMockUser().email))
  }),
  // Members (U7.1d): admins only, as the real API (403 for the other roles).
  http.get(MEMBERS_URL, () =>
    currentMockUser().role === 'admin'
      ? HttpResponse.json({ members: loadMockMembers() })
      : apiError('forbidden'),
  ),
  http.post(MEMBERS_URL, async ({ request }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const { email, role } = (await request.json()) as { email: string; role: Role }
    return memberResult(addMockMember(email, role), 201)
  }),
  http.patch(`${MEMBERS_URL}/:id`, async ({ params, request }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const { role } = (await request.json()) as { role: Role }
    return memberResult(setMockRole(String(params['id']), role))
  }),
  http.delete(`${MEMBERS_URL}/:id`, ({ params }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const result = removeMockMember(String(params['id']))
    return typeof result === 'string'
      ? memberRefusal(result)
      : new HttpResponse(null, { status: 204 })
  }),
  http.post(STREAM_URL, async ({ request }) => {
    const body = (await request.json()) as AskRequest
    // The real stream sends these as its only event, with status 200 (seen live, U7.1b).
    const refused = ownDatabaseRefusal(body)
    if (refused) {
      const { message } = errors[refused]
      return streamOf([{ type: 'error', error: { error: refused, message } }])
    }
    if (BROKEN_STREAM.test(body.question)) return HttpResponse.error()
    if (STUCK_STREAM.test(body.question)) return streamOf(answerStream.slice(0, 5), { stuck: true })
    const code = ERROR_PREFIX.exec(body.question)?.[1]?.toLowerCase()
    if (code && code in errors) return errorStream(code as MockErrorCode)
    return replyStream(body)
  }),
  http.post(ASK_URL, async ({ request }) => {
    const body = (await request.json()) as AskRequest
    const refused = ownDatabaseRefusal(body)
    if (refused) return apiError(refused)
    const code = ERROR_PREFIX.exec(body.question)?.[1]?.toLowerCase()
    if (code && code in errors) return apiError(code as MockErrorCode)
    const reply = replyFor(body)
    recordTurn(reply, [], body.connection_id ?? null)
    return HttpResponse.json(reply)
  }),
  http.get('/api/conversations', ({ request }) => {
    const { searchParams } = new URL(request.url)
    return HttpResponse.json(listStored(searchParams.get('q'), Number(searchParams.get('limit'))))
  }),
  http.get(CONVERSATION_URL, ({ params }) => {
    const found = getStored(String(params['id']))
    return found ? HttpResponse.json(found) : apiError('conversation_not_found')
  }),
  http.patch(CONVERSATION_URL, async ({ params, request }) => {
    const change = (await request.json()) as ConversationUpdate
    const found = updateStored(String(params['id']), change)
    return found ? HttpResponse.json(found) : apiError('conversation_not_found')
  }),
  http.delete(CONVERSATION_URL, ({ params }) =>
    deleteStored(String(params['id']))
      ? new HttpResponse(null, { status: 204 })
      : apiError('conversation_not_found'),
  ),
  http.post(`${CONVERSATION_URL}/turns/:n/rerun`, ({ params }) => {
    const result = rerunStored(String(params['id']), Number(params['n']))
    return result ? HttpResponse.json(result) : apiError('turn_not_found')
  }),
  // A saved database's (U7.1b): `?connection_id=`; one the workspace doesn't have is a 404.
  http.get('/api/catalog', ({ request }) => {
    const database = new URL(request.url).searchParams.get('connection_id')
    if (database === null) return HttpResponse.json(catalog)
    const connection = findMockConnection(database)
    if (!connection) return apiError('connection_not_found')
    // Onboarded: its tables belong to the business its dictionary makes (U7.1c).
    const dictionary = mockDictionary(database)
    return HttpResponse.json(
      savedDatabaseCatalog(dictionary && { name: connection.name, tables: dictionary.tables }),
    )
  }),
  http.get('/api/semantic', ({ request }) => {
    const database = new URL(request.url).searchParams.get('connection_id')
    if (database === null) return HttpResponse.json(semantic)
    const connection = findMockConnection(database)
    if (!connection) return apiError('connection_not_found')
    const dictionary = mockDictionary(database)
    return HttpResponse.json(dictionary ? onboardedOverview(connection, dictionary) : NOT_ONBOARDED)
  }),
  http.get('/api/evals', () => HttpResponse.json(evals)),
  http.get('/api/catalog/tables/:table/questions', ({ params, request }) => {
    const database = new URL(request.url).searchParams.get('connection_id')
    if (database === null) return HttpResponse.json(questionsUsing(String(params['table'])))
    return isMockConnection(database)
      ? HttpResponse.json({ questions: [] })
      : apiError('connection_not_found')
  }),
  http.get(SAVED_QUESTIONS_URL, () => HttpResponse.json({ questions: listSavedQuestions() })),
  http.post(SAVED_QUESTIONS_URL, async ({ request }) => {
    const { question } = (await request.json()) as { question: string }
    return HttpResponse.json(addSavedQuestion(question.trim()))
  }),
  http.delete(`${SAVED_QUESTIONS_URL}/:id`, ({ params }) => {
    removeSavedQuestion(String(params['id']))
    return new HttpResponse(null, { status: 204 })
  }),
  // Settings: the workspace's LLM key (U7.1b). Admins change it; every member may read it.
  http.get('/api/llm/providers', () => HttpResponse.json(providers)),
  http.get(LLM_KEY_URL, () => HttpResponse.json(loadMockKey())),
  http.put(LLM_KEY_URL, async ({ request }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const body = (await request.json()) as SaveLLMKeyRequest
    if (body.api_key === TOO_MANY_TRIES_KEY) {
      return HttpResponse.json(
        {
          error: 'rate_limited',
          message: 'Too many keys tried in a short time. Please wait before trying another.',
          retry_after_seconds: 30,
        },
        { status: 429, headers: { 'Retry-After': '30' } },
      )
    }
    if (body.api_key === INVALID_KEY) {
      return HttpResponse.json(
        { error: 'llm_key_invalid', message: refusedMessage(body.provider) },
        { status: 422 },
      )
    }
    return HttpResponse.json(saveMockKey(body))
  }),
  http.delete(LLM_KEY_URL, () => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    deleteMockKey()
    return new HttpResponse(null, { status: 204 })
  }),
  // Settings: the workspace's own databases (U7.1b part 2). Every member may list them.
  http.get(CONNECTIONS_URL, () => HttpResponse.json(loadMockConnections())),
  http.post(CONNECTIONS_URL, async ({ request }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const body = (await request.json()) as SaveConnectionRequest
    const refused = connectionRefusal(body)
    if (refused) return refused
    const saved = saveMockConnection(body)
    // With a key, saving starts onboarding at once (api/onboarding.py start_if_possible).
    if (saved !== null && loadMockKey() !== null) startMockOnboarding(saved)
    if (saved === null) {
      return HttpResponse.json(
        {
          error: 'connection_name_taken',
          message: `This workspace already has a database called '${body.name}'.`,
        },
        { status: 409 },
      )
    }
    return HttpResponse.json(saved, { status: 201 })
  }),
  http.post(`${CONNECTIONS_URL}/:id/test`, ({ params }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const checked = testMockConnection(String(params['id']))
    return checked ? HttpResponse.json(checked) : apiError('connection_not_found')
  }),
  http.delete(`${CONNECTIONS_URL}/:id`, ({ params }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    forgetMockOnboarding(String(params['id']))
    return deleteMockConnection(String(params['id']))
      ? new HttpResponse(null, { status: 204 })
      : apiError('connection_not_found')
  }),
  // Onboarding and the dictionary review (U7.1c): mocks/onboarding.ts.
  http.post(`${CONNECTIONS_URL}/:id/onboard`, ({ params }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const connection = findMockConnection(String(params['id']))
    if (!connection) return apiError('connection_not_found')
    if (loadMockKey() === null) {
      return HttpResponse.json(
        {
          error: 'llm_key_required',
          message:
            'Onboarding drafts the data dictionary with your own LLM key: an admin can save one in Settings.',
        },
        { status: 409 },
      )
    }
    const started = startMockOnboarding(connection)
    return started ? HttpResponse.json(started, { status: 202 }) : apiError('onboarding_running')
  }),
  http.get(`${CONNECTIONS_URL}/:id/onboarding`, ({ params }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const connection = findMockConnection(String(params['id']))
    const status = connection ? pollMockOnboarding(connection) : null
    if (status === null) {
      return HttpResponse.json(
        { error: 'dictionary_not_found', message: "This database hasn't been onboarded yet." },
        { status: 404 },
      )
    }
    return HttpResponse.json(status)
  }),
  http.get(`${CONNECTIONS_URL}/:id/dictionary`, ({ params }) => {
    const dictionary = mockDictionary(String(params['id']))
    return dictionary ? HttpResponse.json(dictionary) : apiError('dictionary_not_found')
  }),
  http.put(`${CONNECTIONS_URL}/:id/dictionary`, async ({ params, request }) => {
    if (currentMockUser().role !== 'admin') return apiError('forbidden')
    const id = String(params['id'])
    if (mockDictionary(id) === null) return apiError('dictionary_not_found')
    if (isMockOnboardingRunning(id)) return apiError('onboarding_running')
    const { tables } = (await request.json()) as DictionaryEdit
    const result = editMockDictionary(id, tables)
    return 'refused' in result
      ? HttpResponse.json({ error: 'dictionary_invalid', message: result.refused }, { status: 422 })
      : HttpResponse.json(result)
  }),
]

const MEMBER_STATUS = {
  account_not_found: 404,
  member_not_found: 404,
  already_member: 409,
  last_admin: 409,
  rate_limited: 429,
} as const

/** A members refusal with the real API's status and words (mocks/members.ts). */
function memberRefusal(code: keyof typeof MEMBER_STATUS) {
  const status = MEMBER_STATUS[code]
  if (code === 'rate_limited') {
    return HttpResponse.json(
      { error: code, message: MEMBER_MESSAGES[code], retry_after_seconds: 30 },
      { status, headers: { 'Retry-After': '30' } },
    )
  }
  return HttpResponse.json({ error: code, message: MEMBER_MESSAGES[code] }, { status })
}

function memberResult(result: MemberInfo | keyof typeof MEMBER_STATUS, status = 200) {
  return typeof result === 'string' ? memberRefusal(result) : HttpResponse.json(result, { status })
}

/** The refusal a real check would give for what was typed (mocks/settings.ts), or none. */
function connectionRefusal(body: SaveConnectionRequest) {
  const t = CONNECTION_TRIGGERS
  if (body.host === t.tooManyTriesHost) {
    return HttpResponse.json(
      {
        error: 'rate_limited',
        message: 'Too many database checks in a short time. Please wait before trying again.',
        retry_after_seconds: 30,
      },
      { status: 429, headers: { 'Retry-After': '30' } },
    )
  }
  if (body.host === t.unsafeHost) return apiError('connection_unsafe')
  if (body.host === t.unreachableHost) return apiError('connection_unreachable')
  if (body.host === t.selfSignedHost) return apiError('connection_tls_failed')
  if (body.password === t.wrongPassword) return apiError('connection_login_failed')
  if (body.user === t.writerUser) return apiError('connection_writable')
  return null
}

/**
 * Why a first question about a saved database is refused, as the real API would: one the
 * workspace doesn't have (`connection_not_found`), or no LLM key of its own to ask it with
 * (`llm_key_required`). POST /ask answers with that HTTP error; the stream sends it as an event.
 */
function ownDatabaseRefusal(body: AskRequest): MockErrorCode | null {
  const database = body.connection_id
  if (database === null || database === undefined) return null
  if (!isMockConnection(database)) return 'connection_not_found'
  if (loadMockKey() === null) return 'llm_key_required'
  return null
}
