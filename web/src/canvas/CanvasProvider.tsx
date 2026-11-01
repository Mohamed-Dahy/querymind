import { useMutation } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { ApiError, type AskRequest } from '@/api/client'
import { askStream, type AskStreamOptions } from '@/api/stream'
import { KEEP_FOCUS } from '@/layout/useFocusOnRouteChange'

import { canvasReducer, conversationAt, EMPTY_STORE, type CellError } from './state'
import { CanvasContext, type CanvasContextValue } from './useConversation'

// Cell ids only need to be unique on this page load (crypto.randomUUID needs HTTPS off localhost).
let lastCellId = 0

/** Any failure as a cell error: the API's own code and message, or a generic one. */
function cellError(error: unknown): CellError {
  if (error instanceof ApiError) {
    const { code, message, status, retryAfterSeconds } = error
    return {
      code,
      message,
      ...(status !== undefined && { status }),
      ...(retryAfterSeconds !== undefined && {
        retryAfterSeconds,
        retryAt: Date.now() + retryAfterSeconds * 1000,
      }),
    }
  }
  return { code: 'unexpected', message: 'Something went wrong in the browser. Please try again.' }
}

/**
 * Holds every conversation of this page load, and does the side effects the reducer can't:
 * it asks the API (POST /api/ask/stream), dispatches each step and the end, and puts a new
 * conversation's id in the URL.
 */
export function CanvasProvider({ children }: { children: ReactNode }) {
  const [store, dispatch] = useReducer(canvasReducer, EMPTY_STORE)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  // Asking is never retried automatically (queryClient.ts): every try spends LLM quota.
  const { mutateAsync } = useMutation({
    mutationFn: ({ body, options }: { body: AskRequest; options: AskStreamOptions }) =>
      askStream(body, options),
  })
  // Every running question, to cancel it. `named`: its conversation has an id already.
  const running = useRef(new Map<string, { controller: AbortController; named: boolean }>())

  // The latest store and path, for code that runs after an await (when the render's values are old).
  const latest = useRef({ store, pathname })
  useEffect(() => {
    latest.current = { store, pathname }
  }, [store, pathname])

  // Asks the API for a cell that is already running, and dispatches what happened.
  const send = useCallback(
    async (key: string, cellId: string, question: string) => {
      // The id comes from the conversation, not the URL: a follow-up typed at `/` right after the
      // first answer must continue that conversation.
      const conversationId = conversationAt(latest.current.store, key).id
      // Its id, not `key`: `new` may mean another conversation by the time the reply arrives.
      const target = conversationId ?? key
      const controller = new AbortController()
      running.current.set(cellId, { controller, named: conversationId !== null })
      try {
        const reply = await mutateAsync({
          body: { question, conversation_id: conversationId },
          options: {
            signal: controller.signal,
            onEvent: (event) => dispatch({ key: target, type: 'traced', cellId, event }),
          },
        })
        dispatch({ key: target, type: 'replied', cellId, reply })
        // The first reply named the conversation: put that name in the URL, so it can be
        // bookmarked and shared. Replace (Back skips the empty `/`), keep focus in the composer,
        // and only if the user is still on `/` (not if they went to Explore meanwhile).
        if (conversationId === null && latest.current.pathname === '/') {
          void navigate(`/c/${reply.conversation_id}`, { replace: true, state: KEEP_FOCUS })
        }
      } catch (error) {
        // Cancelled by "New conversation": its cell is gone, so there is nothing to report.
        if (controller.signal.aborted) return
        dispatch({ key: target, type: 'failed', cellId, error: cellError(error) })
      } finally {
        running.current.delete(cellId)
      }
    },
    [mutateAsync, navigate],
  )

  const value = useMemo<CanvasContextValue>(
    () => ({
      store,
      ask: (key, question, parentId) => {
        const cellId = `cell-${++lastCellId}`
        dispatch(
          parentId === undefined
            ? { key, type: 'asked', cellId, question }
            : { key, type: 'asked', cellId, question, parentId },
        )
        void send(key, cellId, question)
      },
      retry: (key, cellId) => {
        const cell = conversationAt(latest.current.store, key).cells.find(
          ({ id, status }) => id === cellId && status === 'failed',
        )
        if (cell === undefined) return
        dispatch({ key, type: 'retried', cellId })
        void send(key, cellId, cell.question)
      },
      newConversation: () => {
        // A question with no conversation has nowhere to land after this, so it is cancelled. One
        // in a named conversation goes on: its answer waits at that URL (Back finds it), and
        // throwing it away wouldn't save the quota it has already spent.
        for (const { controller, named } of running.current.values()) {
          if (!named) controller.abort()
        }
        dispatch({ type: 'reset' })
        void navigate('/')
      },
    }),
    [store, send, navigate],
  )
  return <CanvasContext value={value}>{children}</CanvasContext>
}
