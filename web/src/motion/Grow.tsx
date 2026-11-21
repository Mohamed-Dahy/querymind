import { m, type HTMLMotionProps } from 'motion/react'

import { duration, ease } from './tokens'
import { useMotionSafe } from './useMotionSafe'

type GrowProps = Omit<
  HTMLMotionProps<'div'>,
  'initial' | 'animate' | 'exit' | 'transition' | 'variants' | 'whileInView'
> & {
  /** Seconds to wait first: the part already reached grows, then what this arm added. */
  delay?: number
}

/**
 * A bar that grows from its left end to its real length, once, when it scrolls into view (the evals
 * lab's bars). Only `scaleX` moves: the bar's width is its number and is set before anything moves,
 * so the layout never shifts. Under reduced motion (and in unit tests) it is a plain bar.
 */
export function Grow({ delay = 0, style, ...props }: GrowProps) {
  const { animate } = useMotionSafe()
  if (!animate) return <m.div style={style} {...props} />

  return (
    <m.div
      style={{ ...style, originX: 0 }}
      initial={{ scaleX: 0 }}
      whileInView={{
        scaleX: 1,
        // A bar is bigger than a button: the slow duration, so the eye can follow it.
        transition: { duration: duration.slow * 2, ease: ease.out, delay },
      }}
      viewport={{ once: true }}
      {...props}
    />
  )
}
