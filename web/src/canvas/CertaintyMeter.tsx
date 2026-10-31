import { ShieldAlert, ShieldCheck, ShieldX, type LucideIcon } from 'lucide-react'

import type { AskResponse } from '@/api/client'
import { cn } from '@/lib/utils'

type Level = AskResponse['confidence']

// Text, icon and filled segments all say the level; the colour only repeats it.
const LEVELS: Record<Level, { label: string; filled: number; fill: string; Icon: LucideIcon }> = {
  high: { label: 'High', filled: 3, fill: 'bg-signal', Icon: ShieldCheck },
  medium: { label: 'Medium', filled: 2, fill: 'bg-warn', Icon: ShieldAlert },
  low: { label: 'Low', filled: 1, fill: 'bg-danger', Icon: ShieldX },
}
const SEGMENTS = [1, 2, 3]

type CertaintyMeterProps = { level: Level; reasons: readonly string[] }

/** How sure QueryMind is (high / medium / low), and why when it isn't high. */
export function CertaintyMeter({ level, reasons }: CertaintyMeterProps) {
  const { label, filled, fill, Icon } = LEVELS[level]
  const textColour = { high: 'text-signal', medium: 'text-warn', low: 'text-danger' }[level]

  return (
    <div className="text-sm">
      <div className="flex items-center gap-2">
        <Icon aria-hidden="true" className={cn('size-4', textColour)} />
        <span className="font-medium">{label} confidence</span>
        {/* role="meter": a value within a known range, read as "Confidence, High". */}
        <div
          role="meter"
          aria-label="Confidence"
          aria-valuemin={1}
          aria-valuemax={3}
          aria-valuenow={filled}
          aria-valuetext={label}
          className="flex gap-0.5"
        >
          {SEGMENTS.map((segment) => (
            <span
              key={segment}
              className={cn(
                'h-2 w-4 rounded-full',
                // Empty segments get an outline (3:1, like an input border), not just a faint fill.
                segment <= filled ? fill : 'border border-input',
              )}
            />
          ))}
        </div>
      </div>
      {reasons.length > 0 && (
        <ul aria-label="Why the confidence is lower" className="mt-1 ml-6 list-disc text-fg-muted">
          {reasons.map((reason) => (
            <li key={reason}>{reason}</li>
          ))}
        </ul>
      )}
    </div>
  )
}
