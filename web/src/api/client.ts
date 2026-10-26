import createClient from 'openapi-fetch'

import type { components, paths } from './schema'

// Every type here comes from the backend: schema.d.ts is generated from FastAPI's OpenAPI schema
// (scripts/export_openapi.py → npm run gen:api). Never write an API type by hand.
type Schemas = components['schemas']
export type AskRequest = Schemas['AskRequest']
export type AskResponse = Schemas['AskResponse']
export type ClarificationResponse = Schemas['ClarificationResponse']
export type RefusalResponse = Schemas['RefusalResponse']
export type AskReply = paths['/ask']['post']['responses'][200]['content']['application/json']
export type HealthResponse = Schemas['HealthResponse']
type ErrorBody = Schemas['ErrorResponse']

// Same as src/querymind/client.py: a question makes two LLM calls plus a query, and rate-limit
// retries or SQL repairs can take a while.
export const ASK_TIMEOUT_MS = 120_000
const HEALTH_TIMEOUT_MS = 10_000

const ASK_KINDS: readonly string[] = ['answer', 'clarification', 'refusal']

/** The API call failed. `code` is stable (e.g. "unsafe_sql"); `message` is for people. */
export class ApiError extends Error {
  readonly code: string
  readonly status: number | undefined

  constructor(code: string, message: string, status?: number) {
    super(message)
    this.name = 'ApiError'
    this.code = code
    this.status = status
  }
}

const api = createClient<paths>({
  // Absolute, because `new Request()` needs a full URL outside the browser (tests). Same origin:
  // in dev Vite's proxy forwards /api/* to uvicorn (vite.config.ts).
  baseUrl: `${window.location.origin}/api`,
  // Look up fetch on every call (not once at start-up), so test stubs and MSW (U0.5) are used.
  fetch: (request) => globalThis.fetch(request),
})

/** GET /health: is the API up, and which version. */
export function getHealth(signal?: AbortSignal): Promise<HealthResponse> {
  return call(HEALTH_TIMEOUT_MS, signal, (s) => api.GET('/health', { signal: s }))
}

/** POST /ask: an answer, a question back, or a refusal (see `kind`). Never retried: each try costs LLM quota. */
export async function ask(body: AskRequest, signal?: AbortSignal): Promise<AskReply> {
  const reply = await call(ASK_TIMEOUT_MS, signal, (s) => api.POST('/ask', { body, signal: s }))
  // The UI switches on `kind`; a reply without a known one can't be shown.
  if (!ASK_KINDS.includes(reply.kind)) {
    throw new ApiError('bad_response', 'The API sent an unexpected response.')
  }
  return reply
}

type Result<T> = { data?: T; error?: unknown; response: Response }

/**
 * Run one request with a timeout, and turn every failure into an ApiError (like client.py):
 * the API's own {error, message}, a timeout, an unreachable API, or a body we can't read.
 * A cancel by the caller (their `signal`) is passed on as the AbortError it is: not a failure.
 */
async function call<T>(
  timeoutMs: number,
  signal: AbortSignal | undefined,
  send: (signal: AbortSignal) => Promise<Result<T>>,
): Promise<T> {
  // Our own timer (not AbortSignal.timeout), so it is cleared as soon as the reply arrives and
  // tests can fast-forward it with fake timers.
  const timeout = new AbortController()
  const timer = setTimeout(
    () => timeout.abort(new DOMException('The API took too long to answer.', 'TimeoutError')),
    timeoutMs,
  )
  const combined = signal ? AbortSignal.any([signal, timeout.signal]) : timeout.signal

  let result: Result<T>
  try {
    result = await send(combined)
  } catch (error) {
    if (timeout.signal.aborted) {
      throw new ApiError('api_timeout', 'The API took too long to answer.')
    }
    if (signal?.aborted) throw error
    if (error instanceof SyntaxError) {
      throw new ApiError('bad_response', 'The API sent an unexpected response.')
    }
    throw new ApiError('api_unreachable', "Can't reach the QueryMind API. Is it running?")
  } finally {
    clearTimeout(timer)
  }

  const { data, error, response } = result
  if (!response.ok) throw errorFrom(response.status, error)
  if (data === undefined) {
    throw new ApiError('bad_response', 'The API sent an unexpected response.')
  }
  return data
}

function isErrorBody(body: unknown): body is ErrorBody {
  return (
    typeof body === 'object' &&
    body !== null &&
    typeof (body as ErrorBody).error === 'string' &&
    typeof (body as ErrorBody).message === 'string'
  )
}

/** Our API always sends {error, message}. Anything else came from something in between. */
function errorFrom(status: number, body: unknown): ApiError {
  if (isErrorBody(body)) return new ApiError(body.error, body.message, status)
  // A gateway error without our body: the proxy (dev) or load balancer (prod) couldn't reach the API.
  if (status === 502 || status === 503 || status === 504) {
    return new ApiError('api_unreachable', "Can't reach the QueryMind API. Is it running?", status)
  }
  return new ApiError('bad_response', `The API answered with HTTP ${status}.`, status)
}
