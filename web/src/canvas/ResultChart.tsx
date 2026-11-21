import { useMemo } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  LabelList,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import { formatValue } from '@/lib/columns'
import { useTokenColours } from '@/lib/useTokenColours'

import { describeChart, type ChartSpec } from './pickChart'

type DrawnSpec = Exclude<ChartSpec, { kind: 'none' }>

// Bars this few get their value written next to them (more would overlap).
const MAX_LABELLED_BARS = 12
const BAR_HEIGHT = 28
const DASHES = [undefined, '6 3', '2 3']

const formatNumber = (value: unknown) => formatValue(value, 'number')

/**
 * The chart pickChart chose. To screen readers it is one image with a text summary (the table and
 * that summary carry the data); to everyone else, Recharts with our colours, tooltips and labels.
 * Loaded lazily with the Chart tab, so Recharts isn't in the first download.
 */
export function ResultChart({ spec }: { spec: DrawnSpec }) {
  const colours = useTokenColours()
  // Recharts reads rows as objects: { label, v0, v1… } (v = the series, by position).
  const data = useMemo(
    () =>
      spec.points.map((point) => ({
        label: point.label,
        ...Object.fromEntries(point.values.map((value, index) => [`v${index}`, value])),
      })),
    [spec],
  )
  // Signal Lab (U8.3): hairline axes and grid, mono tick labels (the figure's font), an instrument-
  // style tooltip. The data keeps the strongest colour on the page.
  const axis = { stroke: colours.hairline, tick: { fill: colours.muted, fontSize: 11 } }
  const tooltip = (
    <Tooltip
      formatter={formatNumber}
      contentStyle={{
        background: colours.surface,
        borderColor: colours.grid,
        borderRadius: 10,
        color: colours.text,
        fontSize: 12,
      }}
      labelStyle={{ color: colours.muted }}
      cursor={{ fill: colours.grid, fillOpacity: 0.15 }}
    />
  )

  return (
    // font-mono: SVG text inherits the font, so every axis label and value reads like a gauge.
    <figure role="img" aria-label={describeChart(spec)} className="w-full font-mono">
      {spec.kind === 'bar' ? (
        <ResponsiveContainer width="100%" height={spec.points.length * BAR_HEIGHT + 56}>
          {/* layout="vertical": the bars lie down, so long category names stay readable. */}
          <BarChart
            data={data}
            layout="vertical"
            margin={{ top: 4, right: 64, bottom: 20, left: 8 }}
            accessibilityLayer={false}
          >
            <CartesianGrid horizontal={false} stroke={colours.hairline} strokeDasharray="2 4" />
            <XAxis
              type="number"
              {...axis}
              tickFormatter={formatNumber}
              label={{
                value: spec.seriesNames[0],
                position: 'insideBottom',
                offset: -12,
                fill: colours.muted,
              }}
            />
            <YAxis type="category" dataKey="label" width={128} {...axis} interval={0} />
            {tooltip}
            <Bar
              dataKey="v0"
              name={spec.seriesNames[0]}
              fill={colours.series[0]}
              radius={[0, 6, 6, 0]}
              maxBarSize={22}
            >
              {spec.points.length <= MAX_LABELLED_BARS && (
                <LabelList
                  dataKey="v0"
                  position="right"
                  formatter={formatNumber}
                  fill={colours.text}
                  fontSize={11}
                />
              )}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      ) : (
        <ResponsiveContainer width="100%" height={300}>
          <LineChart
            data={data}
            margin={{ top: 8, right: 24, bottom: 20, left: 8 }}
            accessibilityLayer={false}
          >
            <CartesianGrid vertical={false} stroke={colours.hairline} strokeDasharray="2 4" />
            <XAxis
              dataKey="label"
              // Room at both ends, so the first and last labels aren't cut by the edge.
              padding={{ left: 16, right: 16 }}
              {...axis}
              label={{
                value: spec.xName,
                position: 'insideBottom',
                offset: -12,
                fill: colours.muted,
              }}
            />
            <YAxis {...axis} tickFormatter={formatNumber} width={72} />
            {tooltip}
            {spec.seriesNames.length > 1 && <Legend wrapperStyle={{ color: colours.text }} />}
            {spec.seriesNames.map((name, index) => (
              <Line
                key={name}
                dataKey={`v${index}`}
                name={name}
                stroke={colours.series[index]}
                strokeWidth={2}
                // Not colour alone: each line also has its own dash pattern (the legend shows it).
                strokeDasharray={DASHES[index]}
                // Dots too: the points stay visible where a line is short or broken by a gap.
                dot={{ r: 3, fill: colours.surface, stroke: colours.series[index], strokeWidth: 2 }}
                connectNulls={false}
              />
            ))}
          </LineChart>
        </ResponsiveContainer>
      )}
    </figure>
  )
}
