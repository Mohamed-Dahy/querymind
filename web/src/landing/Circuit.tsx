import { cn } from '@/lib/utils'
import { DrawPath, Grow } from '@/motion'

/** The seven steps, as the rail shows them, in a sentence each (what the agent really does). */
const STEPS = [
  ['Retrieve', 'Finds the tables that matter for the question, from your data dictionary.'],
  ['Write SQL', 'Drafts one query, guided by similar questions it has solved before.'],
  ['Validate', 'Parses it first: a single read-only SELECT, on tables you may see.'],
  ['Run', 'Runs it as a read-only database role, with a time limit and a row limit.'],
  ['Check', 'Looks at the result for signs it went wrong, like no rows at all.'],
  ['Repair', 'If the database or the check says no, it fixes the SQL and tries again.'],
  ['Summarize', 'Answers in plain words, with a chart, the rows, the SQL and how sure it is.'],
] as const

const REPAIR = 5 // index of "Repair"
const WRITE = 1 // index of "Write SQL", where the repaired query goes back in

/**
 * The agent drawn as a circuit (U9.1): seven components U1–U7 wired in a row, and the repair loop as
 * a feedback wire from Repair back to Write SQL, in the warning colour. The wires trace themselves
 * in when the diagram scrolls into view. It is an ordered list underneath, so it reads as one in
 * order; the wires and pins are decoration. On a phone the row becomes a column, wired top to bottom.
 */
export function Circuit() {
  return (
    <div className="relative lg:pt-16">
      {/* The feedback wire, over the row (wide screens): Repair → back into Write SQL. The x of
          each end is its column's centre, as a share of the 7 columns (viewBox 0–700). */}
      <svg
        aria-hidden="true"
        viewBox="0 0 700 64"
        preserveAspectRatio="none"
        className="pointer-events-none absolute inset-x-0 top-0 hidden h-16 w-full text-warn lg:block"
      >
        <DrawPath
          inView
          delay={0.6}
          d={`M ${col(REPAIR)} 64 V 22 Q ${col(REPAIR)} 10 ${col(REPAIR) - 12} 10 H ${col(WRITE) + 12} Q ${col(WRITE)} 10 ${col(WRITE)} 22 V 60`}
          fill="none"
          stroke="currentColor"
          strokeWidth={1.5}
          strokeDasharray="5 4"
          vectorEffect="non-scaling-stroke"
        />
      </svg>
      <p className="absolute top-0 left-1/2 hidden -translate-x-1/2 bg-bg px-3 font-mono text-xs text-warn lg:block">
        Repair loop · up to 3 tries a question, by default
      </p>

      <ol className="grid gap-y-8 lg:grid-cols-7 lg:gap-y-0">
        {STEPS.map(([name, text], index) => (
          <li key={name} className="relative flex gap-4 lg:flex-col lg:items-center lg:text-center">
            {/* The wire to the next component: across on wide screens, down on a phone. */}
            {index < STEPS.length - 1 && (
              <>
                <Grow
                  aria-hidden="true"
                  delay={0.08 * index}
                  className="absolute top-7 left-[calc(50%+3.75rem)] hidden h-px w-[calc(100%-7.5rem)] bg-signal lg:block"
                />
                <span
                  aria-hidden="true"
                  className="absolute top-14 -bottom-8 left-14 w-px bg-border lg:hidden"
                />
              </>
            )}
            <Chip index={index} name={name} />
            <p className="pt-1 text-sm text-fg-muted lg:mt-4 lg:px-2 lg:pt-0">{text}</p>
          </li>
        ))}
      </ol>
    </div>
  )
}

/** One step as a chip: its designator (U1…U7) and name, with pins along its top and bottom. */
function Chip({ index, name }: { index: number; name: string }) {
  const pins =
    'absolute inset-x-3 h-1.5 bg-[repeating-linear-gradient(90deg,var(--input)_0_2px,transparent_2px_8px)]'
  return (
    <div
      className={cn(
        'relative grid h-14 w-28 shrink-0 place-items-center rounded-[6px] border bg-surface',
        index === REPAIR ? 'border-warn' : 'border-input',
      )}
    >
      <span aria-hidden="true" className={cn(pins, '-top-1.5')} />
      <span aria-hidden="true" className={cn(pins, '-bottom-1.5')} />
      <span className="text-center leading-tight">
        <span aria-hidden="true" className="block font-mono text-[0.6875rem] text-fg-muted">
          U{index + 1}
        </span>
        <span className="text-sm font-semibold">{name}</span>
      </span>
    </div>
  )
}

/** The centre of column `index` of 7, in the wire's viewBox (0–700). */
function col(index: number): number {
  return (index + 0.5) * 100
}
