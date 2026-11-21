import { AnimatePresence, m } from 'motion/react'
import type { ReactNode } from 'react'

import { duration, ease, exitDuration } from './tokens'
import { useMotionSafe } from './useMotionSafe'

type SwapProps = {
  /** What is shown now. A new id = new content: the old one fades out, the new one fades in. */
  id: string
  className?: string
  children: ReactNode
}

/**
 * A crossfade for content that replaces other content in the same place (a tab panel, a status
 * line). `mode="wait"`: the old content leaves first (quickly), so the two never overlap.
 * `initial={false}`: the first content is just there; only changes animate.
 */
export function Swap({ id, className, children }: SwapProps) {
  const { animate } = useMotionSafe()
  if (!animate) return <div className={className}>{children}</div>

  return (
    <AnimatePresence mode="wait" initial={false}>
      <m.div
        key={id}
        className={className}
        initial={{ opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0, transition: { duration: duration.base, ease: ease.out } }}
        exit={{ opacity: 0, transition: { duration: exitDuration(duration.base), ease: ease.in } }}
      >
        {children}
      </m.div>
    </AnimatePresence>
  )
}
