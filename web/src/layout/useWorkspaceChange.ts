import { useQueryClient } from '@tanstack/react-query'
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router'
import { toast } from 'sonner'

import { checkMe, onWorkspaceChange } from '@/api/session'

// A role check when the tab comes back, at most this often: switching windows back and forth
// shouldn't send a request each time.
const CHECK_EVERY_MS = 15_000

/**
 * What the app does when the session moves to another workspace (U7.1d): the account menu's
 * switch, or a refresh that landed elsewhere (removed from the workspace, or switched in another
 * tab: the refresh cookie is shared). Everything cached belonged to the old workspace, so:
 * - the query cache is cleared at once, before the new workspace is drawn (the listener runs
 *   inside `setSession`, before React re-renders);
 * - the returned generation changes, for the canvas's `key`: its conversations start empty;
 * - the app goes to the canvas, and a toast says where you are now.
 *
 * Also checks who you are (/auth/me) when the tab comes back into focus: an admin may have changed
 * your role meanwhile, and the role badge, tabs and buttons follow it.
 */
export function useWorkspaceChange(): number {
  const queryClient = useQueryClient()
  const navigate = useNavigate()
  const [generation, setGeneration] = useState(0)

  useEffect(
    () =>
      onWorkspaceChange(({ from, to, chosen }) => {
        queryClient.clear()
        setGeneration((was) => was + 1)
        void navigate('/')
        if (chosen) toast.success(`Switched to ${to.workspace.name}.`)
        else {
          toast.warning(
            `You're now in ${to.workspace.name}: ${from.workspace.name} isn't open to this login any more, or another tab switched.`,
          )
        }
      }),
    [queryClient, navigate],
  )

  useEffect(() => {
    let last = 0
    function onBack() {
      if (document.visibilityState !== 'visible' || Date.now() - last < CHECK_EVERY_MS) return
      last = Date.now()
      void checkMe()
    }
    window.addEventListener('focus', onBack)
    document.addEventListener('visibilitychange', onBack)
    return () => {
      window.removeEventListener('focus', onBack)
      document.removeEventListener('visibilitychange', onBack)
    }
  }, [])

  return generation
}
