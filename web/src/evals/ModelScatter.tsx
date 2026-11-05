import {
  CartesianGrid,
  LabelList,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
  ZAxis,
} from 'recharts'

import { useTokenColours } from '@/lib/useTokenColours'

import { dollars } from './format'
import {
  accuracyFloor,
  accuracyTicks,
  describeScatter,
  logTicks,
  type ScatterPoint,
} from './modelRows'

const costTick = (value: number) => `$${value}`
const percentTick = (value: number) => `${value}%`

/**
 * Accuracy against cost, one labelled point per model. To screen readers it is one image with a
 * text summary (the table beside it has every number). Loaded with its own view, so Recharts stays
 * out of the first download.
 */
export default function ModelScatter({ points }: { points: ScatterPoint[] }) {
  const colours = useTokenColours()
  const costs = points.map(({ cost }) => cost)
  const ticks = logTicks(Math.min(...costs), Math.max(...costs))
  const floor = accuracyFloor(points)
  const axis = { stroke: colours.grid, tick: { fill: colours.muted, fontSize: 12 } }
  return (
    <figure role="img" aria-label={describeScatter(points)} className="w-full">
      <ResponsiveContainer width="100%" height={320}>
        <ScatterChart
          margin={{ top: 28, right: 16, bottom: 24, left: 0 }}
          accessibilityLayer={false}
        >
          <CartesianGrid stroke={colours.grid} strokeOpacity={0.4} />
          <XAxis
            type="number"
            dataKey="cost"
            scale="log"
            domain={[ticks[0] ?? 'auto', ticks.at(-1) ?? 'auto']}
            ticks={ticks}
            tickFormatter={costTick}
            {...axis}
            label={{
              value: '$ per correct answer (log scale)',
              position: 'insideBottom',
              offset: -16,
              fill: colours.muted,
              fontSize: 12,
            }}
          />
          <YAxis
            type="number"
            dataKey="accuracy"
            domain={[floor, 100]}
            ticks={accuracyTicks(floor)}
            tickFormatter={percentTick}
            width={48}
            {...axis}
          />
          {/* One size for every point: the size of a dot carries no number here. */}
          <ZAxis range={[80, 80]} />
          <Tooltip
            cursor={{ stroke: colours.grid, strokeDasharray: '3 3' }}
            content={({ active, payload }) => {
              const point = payload?.[0]?.payload as ScatterPoint | undefined
              if (!active || !point) return null
              return (
                <div className="rounded-lg border bg-surface px-3 py-2 text-xs tabular-nums">
                  <p className="font-semibold">{point.name}</p>
                  <p>
                    {point.passed}/{point.questions} correct ({point.accuracy.toFixed(1)}%)
                  </p>
                  <p>{dollars(point.cost)} per correct answer</p>
                </div>
              )
            }}
          />
          <Scatter data={points} fill={colours.series[0]} isAnimationActive={false}>
            <LabelList dataKey="name" position="top" fill={colours.text} fontSize={12} />
          </Scatter>
        </ScatterChart>
      </ResponsiveContainer>
    </figure>
  )
}
