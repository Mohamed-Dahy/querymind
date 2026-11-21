import type { Transition } from 'motion/react'

/*
 * One rhythm for every animation in the app (docs/ui-design-system.md §6). Pages never pick their own
 * numbers: they use these through the primitives in this folder. CSS transitions use the same curve
 * (`--ease-out-expo` in index.css).
 */

/** Seconds (motion's unit). Small things fast, big things slower, nothing over a third of a second. */
export const duration = {
  fast: 0.15,
  base: 0.22,
  slow: 0.32,
} as const

/** Ease-out for what enters (fast start, soft landing); ease-in for what leaves. */
export const ease = {
  out: [0.16, 1, 0.3, 1],
  in: [0.7, 0, 0.84, 0],
} as const

/** Springs: physics instead of a fixed time, so an interrupted animation stays smooth. */
export const spring = {
  /** Presses, sliding indicators, pop-ups: quick, no wobble. */
  snappy: { type: 'spring', stiffness: 500, damping: 40, mass: 1 },
  /** Cards entering, the certainty dial: softer. */
  gentle: { type: 'spring', stiffness: 220, damping: 30, mass: 1 },
} as const satisfies Record<string, Transition>

/** Seconds between items of a list that enter one after another. */
export const stagger = 0.04

/** How far (px) a revealed thing rises while it fades in. */
export const rise = 10

/** Leaving takes about two thirds of entering, so the screen feels quick to respond. */
export function exitDuration(enter: number): number {
  return Math.round(enter * 0.65 * 1000) / 1000
}

/** The transition for "jump to the end": reduced motion, and the unit tests. */
export const instant = { duration: 0 } as const satisfies Transition
