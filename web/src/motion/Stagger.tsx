import { m, type HTMLMotionProps, type Variants } from 'motion/react'

import { duration, ease, rise, spring, stagger } from './tokens'
import { useMotionSafe } from './useMotionSafe'

const TAGS = {
  div: m.div,
  ul: m.ul,
  ol: m.ol,
  li: m.li,
  section: m.section,
  article: m.article,
} as const

type StaggerTag = keyof typeof TAGS

type StaggerProps = Omit<
  HTMLMotionProps<'div'>,
  'initial' | 'animate' | 'exit' | 'transition' | 'variants'
> & {
  as?: StaggerTag
  /** Seconds before the first item starts. */
  delay?: number
}

// Variants are named states ("hidden", "shown") that a parent passes down to its children: the
// parent only says "go to shown", and each StaggerItem knows what "shown" means for itself.
function containerVariants(delay: number): Variants {
  return { hidden: {}, shown: { transition: { staggerChildren: stagger, delayChildren: delay } } }
}

const itemVariants: Variants = {
  hidden: { opacity: 0, y: rise },
  shown: {
    opacity: 1,
    y: 0,
    transition: { ...spring.gentle, opacity: { duration: duration.slow, ease: ease.out } },
  },
}

/**
 * A list whose items (StaggerItem) arrive one after another, 40 ms apart: the example questions,
 * the palette's results. Plain elements under reduced motion and in unit tests.
 */
export function Stagger({ as = 'div', delay = 0, ...props }: StaggerProps) {
  const { animate } = useMotionSafe()
  const Element = TAGS[as] as typeof m.div
  if (!animate) return <Element {...props} />
  return <Element variants={containerVariants(delay)} initial="hidden" animate="shown" {...props} />
}

/** One item of a Stagger. It takes its timing from the parent, so it has no props of its own. */
export function StaggerItem({ as = 'div', ...props }: Omit<StaggerProps, 'delay'>) {
  const { animate } = useMotionSafe()
  const Element = TAGS[as] as typeof m.div
  if (!animate) return <Element {...props} />
  return <Element variants={itemVariants} {...props} />
}
