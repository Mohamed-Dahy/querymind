import { useEffect, useLayoutEffect, useRef } from 'react'

import { cn } from '@/lib/utils'

import { useMotionSafe } from './useMotionSafe'

type CountUpProps = {
  /** The real number to land on (from a report, never made up). */
  value: number
  /** How to write the number: "57", "57%", "$0.0004". */
  format?: (n: number) => string
  /** Where counting starts (default 0). */
  from?: number
  className?: string
}

/** Seconds: long enough to read as counting, short enough not to make anyone wait. */
const COUNT_SECONDS = 0.9

/** Ease-out-expo: races at first, then settles, like a needle coming to rest. */
function easeOutExpo(t: number): number {
  return t >= 1 ? 1 : 1 - 2 ** (-10 * t)
}

const plain = (n: number) => String(Math.round(n))

/**
 * A number that counts up to its value the first time it scrolls into view (the evals tiles, the
 * landing page's proof). Under reduced motion, in unit tests, and in browsers without
 * IntersectionObserver it simply shows the value.
 *
 * The count writes straight into the element (textContent), not through React state: 60 updates a
 * second would otherwise re-render the component 60 times a second.
 */
export function CountUp({ value, format = plain, from = 0, className }: CountUpProps) {
  const { animate } = useMotionSafe()
  const ref = useRef<HTMLSpanElement>(null)
  // The latest format, read inside the animation without restarting it when a parent passes a new
  // (but equal) function on each render. Updated after render, before the animation's next frame.
  const formatRef = useRef(format)
  useLayoutEffect(() => {
    formatRef.current = format
  })
  // What is on screen now, so a changed value counts on from there, not from the start.
  const shownRef = useRef(animate ? from : value)

  useEffect(() => {
    const node = ref.current
    if (!node) return
    const show = (n: number) => {
      shownRef.current = n
      node.textContent = formatRef.current(n)
    }
    if (!animate || typeof IntersectionObserver !== 'function') {
      show(value)
      return
    }

    let frame = 0
    const run = () => {
      const start = performance.now()
      const begin = shownRef.current
      const step = (now: number) => {
        const t = Math.min((now - start) / (COUNT_SECONDS * 1000), 1)
        show(begin + (value - begin) * easeOutExpo(t))
        if (t < 1) frame = requestAnimationFrame(step)
      }
      frame = requestAnimationFrame(step)
    }

    // Start only once it is on screen: a tile further down the page counts when it is reached.
    const observer = new IntersectionObserver((entries) => {
      if (entries.some((entry) => entry.isIntersecting)) {
        observer.disconnect()
        run()
      }
    })
    observer.observe(node)
    return () => {
      observer.disconnect()
      cancelAnimationFrame(frame)
    }
  }, [value, animate])

  return (
    // Rendered text: where counting starts (the effect takes over from there), or, with animations
    // off, the value itself. While counting it only changes with `from`, so React leaves the count
    // alone between frames.
    <span ref={ref} className={cn('tabular-nums', className)}>
      {format(animate ? from : value)}
    </span>
  )
}
