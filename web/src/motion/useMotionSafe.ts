import { MotionGlobalConfig, useReducedMotion, type Transition } from 'motion/react'

import { instant } from './tokens'

/**
 * Whether to animate at all. Off when:
 * - the visitor's system asks for reduced motion (`prefers-reduced-motion: reduce`), or
 * - animations are switched off globally (the unit tests do this in src/test/setup.ts).
 *
 * Every primitive here checks it and, when it is off, renders the end state straight away, with no
 * animation at all. Motion's own `reducedMotion="user"` is not enough: it still fades opacity, and the
 * browser tests allow no animation longer than 1 ms under reduced motion (e2e/a11y.spec.ts).
 */
export function useMotionSafe(): { animate: boolean; transition: (t: Transition) => Transition } {
  const reduce = useReducedMotion() ?? false
  const animate = !reduce && !MotionGlobalConfig.skipAnimations
  return { animate, transition: (t) => (animate ? t : instant) }
}
