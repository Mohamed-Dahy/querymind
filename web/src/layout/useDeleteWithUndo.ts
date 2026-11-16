import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { CONVERSATIONS_KEY, deleteConversation } from '@/api/conversations'
import { ApiError } from '@/api/client'
import { getSession } from '@/api/session'
import { errorTitle } from '@/api/errors'
import { useHistoryActions } from '@/canvas/useConversation'

export const UNDO_MS = 5000

export type DeleteWithUndo = {
  /** Conversations waiting out their Undo: already hidden from the list. */
  hidden: ReadonlySet<string>
  remove: (id: string, title: string) => void
}

/**
 * Delete with an Undo, and no restore route or soft-delete columns: the row hides at once, and
 * the real DELETE goes out when the Undo time ends, or when the page is closing.
 */
export function useDeleteWithUndo(): DeleteWithUndo {
  const queryClient = useQueryClient()
  const { dropConversation } = useHistoryActions()
  const [hidden, setHidden] = useState<ReadonlySet<string>>(new Set())
  const timers = useRef(new Map<string, ReturnType<typeof setTimeout>>())
  // The access token each delete was made under: its workspace's (U7.1d). Sent with it, even if the
  // session has moved to another workspace before the Undo time is up.
  const tokens = useRef(new Map<string, string | undefined>())

  const show = useCallback((id: string) => {
    setHidden((current) => {
      const next = new Set(current)
      next.delete(id)
      return next
    })
  }, [])

  const commit = useCallback(
    async (id: string) => {
      timers.current.delete(id)
      const token = tokens.current.get(id)
      tokens.current.delete(id)
      try {
        await deleteConversation(id, { token })
      } catch (error) {
        // Already gone is what was wanted; anything else brings the row back.
        if (!(error instanceof ApiError && error.code === 'conversation_not_found')) {
          show(id)
          toast.error(errorTitle(error instanceof ApiError ? error.code : 'unexpected'))
          return
        }
      }
      dropConversation(id)
      await queryClient.invalidateQueries({ queryKey: CONVERSATIONS_KEY })
      show(id)
    },
    [dropConversation, queryClient, show],
  )

  const remove = useCallback(
    (id: string, title: string) => {
      setHidden((current) => new Set(current).add(id))
      tokens.current.set(id, getSession()?.accessToken)
      timers.current.set(
        id,
        setTimeout(() => void commit(id), UNDO_MS),
      )
      toast(`Deleted “${title}”`, {
        duration: UNDO_MS,
        action: {
          label: 'Undo',
          onClick: () => {
            clearTimeout(timers.current.get(id))
            timers.current.delete(id)
            tokens.current.delete(id)
            show(id)
          },
        },
      })
    },
    [commit, show],
  )

  // Closing the tab must not quietly cancel a delete the user believes is done. Nor must a workspace
  // switch (U7.1d), which unmounts this: the deletes go now, each with its own workspace's token.
  useEffect(() => {
    const pending = timers.current
    const pendingTokens = tokens.current
    function flush() {
      for (const [id, timer] of pending) {
        clearTimeout(timer)
        pending.delete(id)
        const token = pendingTokens.get(id)
        pendingTokens.delete(id)
        void deleteConversation(id, { keepalive: true, token }).catch(() => {})
      }
    }
    window.addEventListener('pagehide', flush)
    return () => {
      window.removeEventListener('pagehide', flush)
      flush()
    }
  }, [])

  return { hidden, remove }
}
