import { ArrowRight } from 'lucide-react'
import { useEffect, useRef, useState, type RefObject } from 'react'
import { Link } from 'react-router'

import { Skeleton } from '@/components/ui/skeleton'
import { headlineTiles, type Tile } from '@/evals/format'
import { TileValue } from '@/evals/TileValue'
import { useEvals } from '@/evals/useEvals'

/** The datasheet's columns: parameter, measured value, what it counted, where it comes from. */
const ROW =
  'grid gap-x-6 gap-y-1 px-4 md:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)_minmax(0,1.6fr)_minmax(0,1.2fr)]'

/**
 * The landing page's numbers as a spec sheet (U8.4; a datasheet since U9.1): the evals lab's own
 * headline tiles, from the public GET /evals. Never typed in by hand: each row says which report it
 * comes from and when it ran. If the numbers can't be loaded, the section says so and points to the
 * evals lab instead of showing anything made up.
 */
export function Proof() {
  const box = useRef<HTMLDivElement>(null)
  const near = useNearScreen(box)
  return (
    <div ref={box}>
      <Datasheet near={near} />
    </div>
  )
}

/**
 * Whether the box is within about a screen of being seen (and stays true). GET /evals is 415 kB, and
 * this datasheet is far down the page: fetched with the page, it competed with the app's own code on
 * a slow phone and held back the first screen (Lighthouse mobile LCP 5.7 s vs 3.8 s without it,
 * 2026-11-24). Without IntersectionObserver (unit tests' jsdom) it is true at once, like CountUp.
 */
function useNearScreen(box: RefObject<HTMLElement | null>): boolean {
  const [near, setNear] = useState(() => typeof IntersectionObserver !== 'function')
  useEffect(() => {
    const element = box.current
    if (near || element === null) return
    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          setNear(true)
          observer.disconnect()
        }
      },
      { rootMargin: '800px 0px' },
    )
    observer.observe(element)
    return () => observer.disconnect()
  }, [box, near])
  return near
}

function Datasheet({ near }: { near: boolean }) {
  // Until the datasheet is near, the query waits: isPending, so the skeleton holds its place.
  const { data, isPending, isError } = useEvals({ enabled: near })

  if (isPending) {
    return (
      <div aria-hidden="true" className="space-y-px overflow-hidden rounded-[6px] border">
        {[0, 1, 2, 3].map((n) => (
          <Skeleton key={n} className="h-20 rounded-none motion-reduce:animate-none" />
        ))}
      </div>
    )
  }
  if (isError) {
    return (
      <p className="rounded-[6px] border bg-surface p-4 text-sm text-fg-muted">
        The numbers can&apos;t be loaded right now.{' '}
        <Link to="/evals" className="text-info underline underline-offset-4">
          Open the evals lab
        </Link>{' '}
        to see every run.
      </p>
    )
  }

  return (
    <div className="overflow-hidden rounded-[6px] border bg-surface">
      {/* The column names, as on a printed datasheet. Each row also says them in its own words. */}
      <div aria-hidden="true" className={`${ROW} hidden border-b bg-muted py-2 md:grid`}>
        <span className="label-mono">PARAMETER</span>
        <span className="label-mono">MEASURED</span>
        <span className="label-mono">CONDITIONS</span>
        <span className="label-mono">SOURCE</span>
      </div>
      <ul className="divide-y">
        {headlineTiles(data).map((tile) => (
          <li key={tile.title} className={`${ROW} items-baseline py-5`}>
            <p className="font-semibold">{tile.title}</p>
            <p className="font-mono text-3xl font-semibold tracking-tight">
              <TileValue value={tile.value} />
            </p>
            <p className="text-sm text-fg-muted">{tile.caption}</p>
            <p className="font-mono text-xs wrap-break-word text-fg-muted">
              <span className="sr-only">From </span>
              {sourceLine(tile)}
            </p>
          </li>
        ))}
        <li className="bg-muted px-4 py-1">
          <Link
            to="/evals"
            className="inline-flex min-h-11 items-center gap-1.5 rounded-lg text-sm font-medium text-info underline-offset-4 hover:underline"
          >
            Every run, model and question in the evals lab
            <ArrowRight aria-hidden="true" className="size-4" />
          </Link>
        </li>
      </ul>
    </div>
  )
}

/** Which report(s), and the day: "golden-d.md · 2026-11-08". */
function sourceLine(tile: Tile): string {
  const reports =
    tile.sources.length > 2 ? `${tile.sources.length} reports` : tile.sources.join(', ')
  return [reports, tile.date].filter(Boolean).join(' · ')
}
