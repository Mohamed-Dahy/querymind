import { useQueryClient } from '@tanstack/react-query'
import { useCallback, useEffect, useRef, useState } from 'react'
import { toast } from 'sonner'

import { CONVERSATIONS_KEY, deleteConversation } from '@/api/conversations'
import { ApiError } from '@/api/client'
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
      try {
        await deleteConversation(id)
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
            show(id)
          },
        },
      })
    },
    [commit, show],
  )

  // Closing the tab must not quietly cancel a delete the user believes is done.
  useEffect(() => {
    const pending = timers.current
    function flush() {
      for (const [id, timer] of pending) {
        clearTimeout(timer)
        pending.delete(id)
        void deleteConversation(id, { keepalive: true }).catch(() => {})
      }
    }
    window.addEventListener('pagehide', flush)
    return () => window.removeEventListener('pagehide', flush)
  }, [])

  return { hidden, remove }
}
