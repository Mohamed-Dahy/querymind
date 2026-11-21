import { ArrowRight, FileText } from 'lucide-react'
import { Link } from 'react-router'

import { Skeleton } from '@/components/ui/skeleton'
import { headlineTiles, type Tile } from '@/evals/format'
import { useEvals } from '@/evals/useEvals'
import { CountUp, Stagger, StaggerItem } from '@/motion'

/**
 * The landing page's numbers (U8.4): the evals lab's own headline tiles, from the public GET /evals.
 * Never typed in by hand: each one says which report it comes from and when it ran. If the numbers
 * can't be loaded, the section says so and points to the evals lab instead of showing anything made up.
 */
export function Proof() {
  const { data, isPending, isError } = useEvals()

  if (isPending) {
    return (
      <div aria-hidden="true" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
        {[0, 1, 2, 3].map((n) => (
          <Skeleton key={n} className="h-40 rounded-cell motion-reduce:animate-none" />
        ))}
      </div>
    )
  }
  if (isError) {
    return (
      <p className="rounded-cell border bg-surface p-4 text-sm text-fg-muted">
        The numbers can&apos;t be loaded right now.{' '}
        <Link to="/evals" className="text-info underline underline-offset-4">
          Open the evals lab
        </Link>{' '}
        to see every run.
      </p>
    )
  }

  return (
    <Stagger as="ul" className="grid gap-4 sm:grid-cols-2 lg:grid-cols-4">
      {headlineTiles(data).map((tile) => (
        <StaggerItem
          as="li"
          key={tile.title}
          className="flex flex-col rounded-cell border bg-surface p-5"
        >
          <p className="label-mono">{tile.title}</p>
          <p className="mt-3 text-4xl font-semibold tracking-tight">
            <TileValue value={tile.value} />
          </p>
          <p className="mt-2 text-sm text-fg-muted">{tile.caption}</p>
          <p className="mt-auto flex items-start gap-1.5 pt-4 font-mono text-xs text-fg-muted">
            <FileText aria-hidden="true" className="mt-px size-3.5 shrink-0" />
            <span>
              <span className="sr-only">From </span>
              {sourceLine(tile)}
            </span>
          </p>
        </StaggerItem>
      ))}
      <li className="sm:col-span-2 lg:col-span-4">
        <Link
          to="/evals"
          className="inline-flex min-h-11 items-center gap-1.5 rounded-lg text-sm font-medium text-info underline-offset-4 hover:underline"
        >
          Every run, model and question in the evals lab
          <ArrowRight aria-hidden="true" className="size-4" />
        </Link>
      </li>
    </Stagger>
  )
}

/** "57/100" counts up its first number; other values (a price, a percentage) are shown as they are. */
function TileValue({ value }: { value: string }) {
  const score = /^(\d+)\/(\d+)$/.exec(value)
  if (!score) return <span className="tabular-nums">{value}</span>
  return (
    <>
      <CountUp value={Number(score[1])} />
      <span className="text-fg-muted">/{score[2]}</span>
    </>
  )
}

/** Which report(s), and the day: "golden-d.md · 2026-11-08". */
function sourceLine(tile: Tile): string {
  const reports =
    tile.sources.length > 2 ? `${tile.sources.length} reports` : tile.sources.join(', ')
  return [reports, tile.date].filter(Boolean).join(' · ')
}
