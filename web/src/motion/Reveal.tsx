import { m, type HTMLMotionProps } from 'motion/react'

import { duration, ease, rise, spring } from './tokens'
import { useMotionSafe } from './useMotionSafe'

// The tags a revealed block can be. Each is motion's light `m.*` version of the HTML element.
const TAGS = {
  div: m.div,
  section: m.section,
  article: m.article,
  header: m.header,
  h2: m.h2,
  h3: m.h3,
  li: m.li,
  p: m.p,
  span: m.span,
} as const

export type RevealTag = keyof typeof TAGS

type RevealProps = Omit<
  HTMLMotionProps<'div'>,
  'initial' | 'animate' | 'exit' | 'transition' | 'variants' | 'whileInView'
> & {
  /** The element to render (default `div`). */
  as?: RevealTag
  /** Seconds to wait first, to put a few reveals in order (answer → dial → evidence). */
  delay?: number
  /** Reveal when it scrolls into view (the landing page), not when it mounts. */
  inView?: boolean
}

/**
 * Fades and rises a block into place, once, when it appears: "this just arrived".
 * Under reduced motion (and in unit tests) it is a plain element, already in place.
 */
export function Reveal({ as = 'div', delay = 0, inView = false, ...props }: RevealProps) {
  const { animate } = useMotionSafe()
  // One element type for TypeScript: every tag above takes the same props.
  const Element = TAGS[as] as typeof m.div

  if (!animate) return <Element {...props} />

  const shown = {
    opacity: 1,
    y: 0,
    transition: {
      ...spring.gentle,
      delay,
      // Opacity on a timer, the movement on a spring: the fade never overshoots.
      opacity: { duration: duration.slow, ease: ease.out, delay },
    },
  }
  return inView ? (
    <Element
      initial={{ opacity: 0, y: rise }}
      whileInView={shown}
      viewport={{ once: true, margin: '0px 0px -10% 0px' }}
      {...props}
    />
  ) : (
    <Element initial={{ opacity: 0, y: rise }} animate={shown} {...props} />
  )
}
