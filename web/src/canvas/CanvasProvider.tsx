import { useMutation, useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { ApiError, type AskRequest } from '@/api/client'
import { CONVERSATIONS_KEY, type ConversationDetail, type RerunResult } from '@/api/conversations'
import { askStream, type AskStreamOptions } from '@/api/stream'
import { KEEP_FOCUS } from '@/layout/useFocusOnRouteChange'

import { canvasReducer, conversationAt, EMPTY_STORE, type CellError } from './state'
import { CanvasContext, type CanvasContextValue } from './useConversation'

// Cell ids only need to be unique on this page load (crypto.randomUUID needs HTTPS off localhost).
let lastCellId = 0

const STOPPED: CellError = {
  code: 'stopped',
  message: 'A step that was already running may still finish on the server.',
}

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
  const queryClient = useQueryClient()
  const { pathname } = useLocation()
  // Asking is never retried automatically (queryClient.ts): every try spends LLM quota.
  const { mutateAsync } = useMutation({
    mutationFn: ({ body, options }: { body: AskRequest; options: AskStreamOptions }) =>
      askStream(body, options),
  })
  // Every running question, to cancel it. `named`: its conversation has an id already.
  // `stopped`: the user pressed Stop, so the cell stays and says so.
  const running = useRef(
    new Map<string, { controller: AbortController; named: boolean; stopped: boolean }>(),
  )

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
      const run = { controller, named: conversationId !== null, stopped: false }
      running.current.set(cellId, run)
      try {
        const reply = await mutateAsync({
          body: { question, conversation_id: conversationId },
          options: {
            signal: controller.signal,
            onEvent: (event) => dispatch({ key: target, type: 'traced', cellId, event }),
          },
        })
        dispatch({ key: target, type: 'replied', cellId, reply })
        // The API saved this turn before replying, so the history list can show it now.
        void queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY })
        // The first reply named the conversation: put that name in the URL, so it can be
        // bookmarked and shared. Replace (Back skips the empty `/`), keep focus in the composer,
        // and only if the user is still on `/` (not if they went to Explore meanwhile).
        if (conversationId === null && latest.current.pathname === '/') {
          void navigate(`/c/${reply.conversation_id}`, { replace: true, state: KEEP_FOCUS })
        }
      } catch (error) {
        // Cancelled by "New conversation": its cell is gone, so there is nothing to report.
        if (controller.signal.aborted && !run.stopped) return
        dispatch({
          key: target,
          type: 'failed',
          cellId,
          error: run.stopped ? STOPPED : cellError(error),
        })
      } finally {
        running.current.delete(cellId)
      }
    },
    [mutateAsync, navigate, queryClient],
  )

  // Only dispatch: stable, so effects can depend on them.
  const load = useCallback((conversation: ConversationDetail) => {
    dispatch({ type: 'loaded', conversation })
  }, [])
  const applyRerun = useCallback((key: string, cellId: string, result: RerunResult) => {
    dispatch({ key, type: 'rerun', cellId, result })
  }, [])
  const dropConversation = useCallback((conversationId: string) => {
    dispatch({ type: 'dropped', conversationId })
  }, [])

  const value = useMemo<CanvasContextValue>(
    () => ({
      store,
      load,
      applyRerun,
      dropConversation,
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
      stop: (cellId) => {
        const run = running.current.get(cellId)
        if (run === undefined) return
        run.stopped = true
        run.controller.abort()
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
    [store, send, navigate, load, applyRerun, dropConversation],
  )
  return <CanvasContext value={value}>{children}</CanvasContext>
}
