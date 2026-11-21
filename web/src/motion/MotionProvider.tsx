import { LazyMotion, MotionConfig } from 'motion/react'
import type { ReactNode } from 'react'

import { duration, ease } from './tokens'

// The features chunk (features.ts) is fetched when the app starts, next to the first page's code.
const loadFeatures = () => import('./features').then((module) => module.default)

/**
 * Wraps the app once (main.tsx), so every animation shares the same set-up:
 * - `LazyMotion` keeps motion's engine out of the first-load JS. `strict` makes a plain
 *   `motion.div` an error: only the light `m.div` is allowed, so nobody pulls the full bundle in by
 *   accident.
 * - `MotionConfig` gives every animation our default timing, and `reducedMotion="user"` turns off
 *   movement (transforms, layout) for visitors who ask for less motion. The primitives go further and
 *   skip opacity too (useMotionSafe).
 */
export function MotionProvider({ children }: { children: ReactNode }) {
  return (
    <LazyMotion features={loadFeatures} strict>
      <MotionConfig reducedMotion="user" transition={{ duration: duration.base, ease: ease.out }}>
        {children}
      </MotionConfig>
    </LazyMotion>
  )
}
