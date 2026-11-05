import { cn } from '@/lib/utils'

import { percentText, scoreText } from './format'
import { changeText, type WaterfallRow } from './waterfallRows'

/**
 * What each arm adds to the one before it. Plain HTML, not a chart library: every number is real
 * text beside its bar, so it reads with colour removed, on a phone, and in a screen reader.
 */
export function Waterfall({ rows }: { rows: WaterfallRow[] }) {
  return (
    <ol className="space-y-4">
      {rows.map((row) => (
        <Row key={row.arm.key} row={row} />
      ))}
    </ol>
  )
}

function Row({ row }: { row: WaterfallRow }) {
  const { arm, score, from, to, change, withinNoise } = row
  const low = Math.min(from, to)
  const size = Math.abs(to - from)
  const words = changeText(row)
  return (
    <li aria-label={`Arm ${arm.key}`} className="grid gap-x-4 gap-y-1 md:grid-cols-[16rem_1fr]">
      <p className="text-sm">
        <span className="font-semibold">Arm {arm.key}</span>{' '}
        <span className="text-fg-muted">{arm.adds}</span>
      </p>
      <div>
        <p className="flex flex-wrap items-baseline gap-x-2 text-sm">
          <span className="text-base font-semibold tabular-nums">{scoreText(score)}</span>
          <span className="tabular-nums">{percentText(score)}</span>
          {words && <span className="font-semibold tabular-nums">{words}</span>}
          {withinNoise && <span className="text-fg-muted">within noise</span>}
          {arm.not_scored > 0 && <span className="text-fg-muted">{arm.not_scored} not scored</span>}
        </p>
        <div aria-hidden="true" className="relative mt-1 h-3 overflow-hidden rounded bg-muted">
          {/* What the arms before it already reached. */}
          <div className="absolute inset-y-0 left-0 bg-input" style={{ width: `${low}%` }} />
          {/* What this arm changed; a thin mark when nothing did, so the row still has a point. */}
          <div
            className={cn(
              'absolute inset-y-0 min-w-0.5',
              change === 'loss' ? 'bg-danger' : 'bg-signal',
              withinNoise && 'opacity-50',
            )}
            style={{
              left: `${change === 'none' ? Math.max(to - 0.25, 0) : low}%`,
              width: `${size}%`,
            }}
          />
        </div>
      </div>
    </li>
  )
}
