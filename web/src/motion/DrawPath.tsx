import { m, type SVGMotionProps } from 'motion/react'

import { duration, ease } from './tokens'
import { useMotionSafe } from './useMotionSafe'

type DrawPathProps = Omit<
  SVGMotionProps<SVGPathElement>,
  'initial' | 'animate' | 'exit' | 'transition' | 'variants'
> & {
  /** Seconds to wait before drawing. */
  delay?: number
  /** Draw when it scrolls into view (a diagram further down a page), not when it mounts. */
  inView?: boolean
}

/**
 * An SVG path that draws itself, like a pen tracing it: the repair arc, a follow-up's connector,
 * the certainty dial. `pathLength` 0 → 1 is motion's shortcut for the stroke-dasharray trick.
 * Under reduced motion (and in unit tests) the path is simply there.
 */
export function DrawPath({ delay = 0, inView = false, ...props }: DrawPathProps) {
  const { animate } = useMotionSafe()
  if (!animate) return <m.path {...props} />

  const drawn = {
    pathLength: 1,
    opacity: 1,
    transition: {
      // A line reads as "being drawn" only if it takes a little longer than a fade.
      pathLength: { duration: duration.slow * 1.5, ease: ease.out, delay },
      opacity: { duration: duration.fast, delay },
    },
  }
  return inView ? (
    <m.path
      initial={{ pathLength: 0, opacity: 0 }}
      whileInView={drawn}
      viewport={{ once: true }}
      {...props}
    />
  ) : (
    <m.path initial={{ pathLength: 0, opacity: 0 }} animate={drawn} {...props} />
  )
}
