import { CountUp } from '@/motion'

/**
 * A headline number from the evals (U8.6). A score like "57/100" counts up its first number the first
 * time it is seen; other values (a price, a percentage, a speed-up) are shown as they are. Shared by
 * the evals lab's scoreboard and the landing page, so the two always show the same thing.
 *
 * The whole score stays one piece of text ("57/100"), so it reads, copies and is found as one.
 */
export function TileValue({ value }: { value: string }) {
  const score = /^(\d+)\/(\d+)$/.exec(value)
  if (!score) return <span className="tabular-nums">{value}</span>
  const total = score[2]
  return (
    <CountUp
      value={Number(score[1])}
      format={(n) => `${Math.round(n)}/${total}`}
      className="tabular-nums"
    />
  )
}
