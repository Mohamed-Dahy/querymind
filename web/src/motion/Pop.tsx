import { m, type HTMLMotionProps } from 'motion/react'

import { spring } from './tokens'
import { useMotionSafe } from './useMotionSafe'

type PopProps = Omit<
  HTMLMotionProps<'div'>,
  'initial' | 'animate' | 'exit' | 'transition' | 'variants'
> & {
  /** The corner it grows from, next to the button that opened it (CSS transform-origin). */
  origin?: string
}

/**
 * A small panel that pops out of the button that opened it (the account menu): it grows from that
 * corner, so the eye sees where it came from. Enter only: it closes at once, which keeps the menu
 * quick and means nothing lingers in the page after it is gone. Plain under reduced motion and in
 * unit tests.
 */
export function Pop({ origin = 'top right', style, ...props }: PopProps) {
  const { animate } = useMotionSafe()
  if (!animate) return <m.div style={style} {...props} />
  return (
    <m.div
      initial={{ opacity: 0, scale: 0.96, y: -4 }}
      animate={{ opacity: 1, scale: 1, y: 0 }}
      transition={spring.snappy}
      style={{ transformOrigin: origin, ...style }}
      {...props}
    />
  )
}
