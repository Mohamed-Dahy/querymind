import { ShieldAlert, ShieldCheck, ShieldX, type LucideIcon } from 'lucide-react'
import { m } from 'motion/react'

import type { AskResponse } from '@/api/client'
import { cn } from '@/lib/utils'
import { spring, useMotionSafe } from '@/motion'

type Level = AskResponse['confidence']

// Text, icon and the dial's sweep all say the level; the colour only repeats it.
const LEVELS: Record<
  Level,
  { label: string; filled: number; stroke: string; text: string; Icon: LucideIcon }
> = {
  high: {
    label: 'High',
    filled: 3,
    stroke: 'stroke-signal',
    text: 'text-signal',
    Icon: ShieldCheck,
  },
  medium: {
    label: 'Medium',
    filled: 2,
    stroke: 'stroke-warn',
    text: 'text-warn',
    Icon: ShieldAlert,
  },
  low: { label: 'Low', filled: 1, stroke: 'stroke-danger', text: 'text-danger', Icon: ShieldX },
}

// The dial: a half circle from left to right (radius 16, centre 20,20). Its length is π × 16.
const ARC = 'M 4 20 A 16 16 0 0 1 36 20'
// Three ticks at a third, two thirds and the end of the sweep: the levels' marks.
const TICKS = [1 / 3, 2 / 3, 1].map((share) => {
  const angle = Math.PI * (1 - share)
  return {
    share,
    x1: 20 + 12.5 * Math.cos(angle),
    y1: 20 - 12.5 * Math.sin(angle),
    x2: 20 + 19.5 * Math.cos(angle),
    y2: 20 - 19.5 * Math.sin(angle),
  }
})

type CertaintyMeterProps = { level: Level; reasons: readonly string[] }

/**
 * How sure QueryMind is (high / medium / low), and why when it isn't high. Signal Lab (U8.3): a
 * small dial whose needle-arc sweeps to a third, two thirds or all of the way, like a gauge on an
 * instrument. Still a `role="meter"` with the level in words, so nothing depends on seeing it.
 */
export function CertaintyMeter({ level, reasons }: CertaintyMeterProps) {
  const { label, filled, stroke, text, Icon } = LEVELS[level]
  const { animate } = useMotionSafe()
  const share = filled / 3

  return (
    <div className="text-sm">
      <div className="flex items-center gap-2.5">
        {/* role="meter": a value within a known range, read as "Confidence, High". */}
        <div
          role="meter"
          aria-label="Confidence"
          aria-valuemin={1}
          aria-valuemax={3}
          aria-valuenow={filled}
          aria-valuetext={label}
          className="shrink-0"
        >
          <svg viewBox="0 0 40 22" aria-hidden="true" className="h-[22px] w-10 overflow-visible">
            {/* The track: an outline colour (3:1), so the empty part of the dial is visible too. */}
            <path
              d={ARC}
              fill="none"
              strokeWidth={3.5}
              strokeLinecap="round"
              className="stroke-input"
            />
            {TICKS.map((tick) => (
              <line
                key={tick.share}
                x1={tick.x1}
                y1={tick.y1}
                x2={tick.x2}
                y2={tick.y2}
                strokeWidth={1}
                className={tick.share <= share ? 'stroke-surface' : 'stroke-border'}
              />
            ))}
            <m.path
              d={ARC}
              fill="none"
              strokeWidth={3.5}
              strokeLinecap="round"
              className={stroke}
              // Sweeps from empty to its level when the answer arrives; already there under reduced
              // motion and for answers shown again.
              initial={animate ? { pathLength: 0 } : false}
              animate={{ pathLength: share }}
              transition={animate ? { ...spring.gentle, delay: 0.12 } : { duration: 0 }}
            />
          </svg>
        </div>
        <Icon aria-hidden="true" className={cn('size-4', text)} />
        <span className="font-medium">{label} confidence</span>
      </div>
      {reasons.length > 0 && (
        <ul
          aria-label="Why the confidence is lower"
          className="mt-1.5 ml-[4.25rem] list-disc text-fg-muted"
        >
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
