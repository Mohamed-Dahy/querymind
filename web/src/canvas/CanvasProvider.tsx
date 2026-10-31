import { useMutation } from '@tanstack/react-query'
import { useCallback, useEffect, useMemo, useReducer, useRef, type ReactNode } from 'react'
import { useLocation, useNavigate } from 'react-router'

import { ApiError, ask, type AskRequest } from '@/api/client'
import { KEEP_FOCUS } from '@/layout/useFocusOnRouteChange'

import { canvasReducer, conversationAt, EMPTY_STORE, type CellError } from './state'
import { CanvasContext, type CanvasContextValue } from './useConversation'

// Cell ids only need to be unique on this page load (crypto.randomUUID needs HTTPS off localhost).
let lastCellId = 0

/** Any failure as a cell error: the API's own code and message, or a generic one. */
function cellError(error: unknown): CellError {
  if (error instanceof ApiError) {
    return error.status === undefined
      ? { code: error.code, message: error.message }
      : { code: error.code, message: error.message, status: error.status }
  }
  return { code: 'unexpected', message: 'Something went wrong in the browser. Please try again.' }
}

/**
 * Holds every conversation of this page load, and does the side effects the reducer can't:
 * it calls POST /api/ask, dispatches what happened, and puts a new conversation's id in the URL.
 */
export function CanvasProvider({ children }: { children: ReactNode }) {
  const [store, dispatch] = useReducer(canvasReducer, EMPTY_STORE)
  const navigate = useNavigate()
  const { pathname } = useLocation()
  // Asking is never retried automatically (queryClient.ts): every try spends LLM quota.
  const { mutateAsync } = useMutation({ mutationFn: (body: AskRequest) => ask(body) })

  // The latest store and path, for code that runs after an await (when the render's values are old).
  const latest = useRef({ store, pathname })
  useEffect(() => {
    latest.current = { store, pathname }
  }, [store, pathname])

  const askIn = useCallback(
    async (key: string, question: string, parentId?: string) => {
      // The id comes from the conversation, not the URL: a follow-up typed at `/` right after the
      // first answer must continue that conversation.
      const conversationId = conversationAt(latest.current.store, key).id
      const cellId = `cell-${++lastCellId}`
      dispatch(
        parentId === undefined
          ? { key, type: 'asked', cellId, question }
          : { key, type: 'asked', cellId, question, parentId },
      )
      try {
        const reply = await mutateAsync({ question, conversation_id: conversationId })
        dispatch({ key, type: 'replied', cellId, reply })
        // The first reply named the conversation: put that name in the URL, so it can be
        // bookmarked and shared. Replace (Back skips the empty `/`), keep focus in the composer,
        // and only if the user is still on `/` (not if they went to Explore meanwhile).
        if (conversationId === null && latest.current.pathname === '/') {
          void navigate(`/c/${reply.conversation_id}`, { replace: true, state: KEEP_FOCUS })
        }
      } catch (error) {
        dispatch({ key, type: 'failed', cellId, error: cellError(error) })
      }
    },
    [mutateAsync, navigate],
  )

  const value = useMemo<CanvasContextValue>(
    () => ({
      store,
      ask: (key, question, parentId) => void askIn(key, question, parentId),
    }),
    [store, askIn],
  )
  return <CanvasContext value={value}>{children}</CanvasContext>
}
