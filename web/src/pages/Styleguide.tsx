import {
  ChartColumn,
  CircleCheck,
  Code,
  Info,
  Loader2,
  OctagonX,
  Play,
  RotateCcw,
  Table2,
  TriangleAlert,
} from 'lucide-react'
import { useState, type ReactNode } from 'react'
import { toast } from 'sonner'

import { FIELD } from '@/components/field'
import { Kbd } from '@/components/Kbd'
import { ThemeSwitcher } from '@/components/ThemeSwitcher'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
  SheetTrigger,
} from '@/components/ui/sheet'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { CountUp, DrawPath, Reveal, Stagger, StaggerItem, Swap } from '@/motion'

// Dev-only page (http://localhost:5173/styleguide): the visual QA board for "Signal Lab" (ADR 026).
// Every token, type size, base component and motion primitive in one place, so a design change can be
// checked in both themes and with reduced motion. Not included in production builds.

const SURFACES = [
  { token: '--bg', use: 'page and canvas (graphite / paper)' },
  { token: '--surface', use: 'readouts, panels, dialogs' },
  { token: '--muted', use: 'inputs, stripes, hover, tab list' },
]

const INKS = [
  { token: '--fg', use: 'text' },
  { token: '--fg-muted', use: 'secondary text, mono labels' },
  { token: '--signal', use: 'the one accent: running step, primary, high confidence' },
  { token: '--info', use: 'second series, links, trace details' },
  { token: '--warn', use: 'warnings, medium confidence' },
  { token: '--danger', use: 'errors, refusals, low confidence, failed try' },
  { token: '--data-3', use: 'third data series' },
  { token: '--data-4', use: 'fourth data series' },
]

const LINES = [
  { token: '--border', use: 'decorative hairlines' },
  { token: '--input', use: 'control outlines (3:1)' },
  { token: '--ring', use: 'keyboard focus' },
  { token: '--glow', use: 'decorative halo behind the active thing, never text' },
]

const TYPE_SCALE = [
  { className: 'text-5xl font-heading font-semibold', label: 'Display · 48 px · Geist 600' },
  { className: 'text-3xl font-heading font-semibold', label: 'Heading 1 · 32 px · Geist 600' },
  { className: 'text-2xl font-heading font-semibold', label: 'Heading 2 · 24 px · Geist 600' },
  { className: 'text-lg font-heading font-semibold', label: 'Heading 3 · 18 px · Geist 600' },
  { className: 'text-base', label: 'Body · 16 px · Geist 400 · line-height 1.55' },
  { className: 'text-sm', label: 'Small · 14 px · Geist 400 (tables, labels)' },
  { className: 'text-xs text-fg-muted', label: 'Caption · 12 px · Geist 400 (never body text)' },
]

function Section({
  title,
  label,
  children,
}: {
  title: string
  label: string
  children: ReactNode
}) {
  const id = `section-${title.toLowerCase().replace(/\W+/g, '-')}`
  return (
    <section aria-labelledby={id} className="space-y-4">
      <div>
        <p aria-hidden="true" className="label-mono">
          {label}
        </p>
        <h2 id={id} className="mt-1 text-xl">
          {title}
        </h2>
      </div>
      {children}
    </section>
  )
}

function Swatch({ token, use }: { token: string; use: string }) {
  return (
    <div className="flex items-center gap-3">
      <span
        aria-hidden="true"
        className="size-10 shrink-0 rounded-lg border border-input"
        style={{ background: `var(${token})` }}
      />
      <div className="min-w-0">
        <code className="text-sm">{token}</code>
        <p className="text-xs text-fg-muted">{use}</p>
      </div>
    </div>
  )
}

/** Each text colour on each background: what the contrast test checks, made visible. */
function InkOnSurfaces() {
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      {SURFACES.map(({ token }) => (
        <div
          key={token}
          className="space-y-1 rounded-cell border p-4"
          style={{ background: `var(${token})` }}
        >
          <code className="text-xs text-fg-muted">on {token}</code>
          {INKS.map((ink) => (
            <p key={ink.token} className="text-sm" style={{ color: `var(${ink.token})` }}>
              {ink.token} · The quick brown fox
            </p>
          ))}
        </div>
      ))}
    </div>
  )
}

function ConfidenceBadges() {
  return (
    <div className="flex flex-wrap gap-2">
      <Badge variant="outline" className="border-signal/50">
        <CircleCheck aria-hidden="true" className="text-signal" />
        High confidence
      </Badge>
      <Badge variant="outline" className="border-warn/50">
        <TriangleAlert aria-hidden="true" className="text-warn" />
        Medium confidence
      </Badge>
      <Badge variant="outline" className="border-danger/50">
        <OctagonX aria-hidden="true" className="text-danger" />
        Low confidence
      </Badge>
      <Badge variant="secondary">
        <Info aria-hidden="true" className="text-info" />
        Answer from template
      </Badge>
      <Badge variant="secondary">Admin</Badge>
    </div>
  )
}

/** A replay button: remounting (a new key) runs an entrance animation again. */
function useReplay(): [number, ReactNode] {
  const [run, setRun] = useState(0)
  const button = (
    <Button size="sm" variant="outline" onClick={() => setRun((n) => n + 1)}>
      <RotateCcw aria-hidden="true" />
      Replay
    </Button>
  )
  return [run, button]
}

const EXAMPLES = [
  'Which city had the biggest sales drop last month?',
  'Top 5 products by revenue in 1997',
  'How many customers ordered beverages twice?',
]

function MotionBoard() {
  const [run, replay] = useReplay()
  const [tab, setTab] = useState<'answer' | 'sql'>('answer')

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-3">
        {replay}
        <p className="text-sm text-fg-muted">
          With reduced motion on (or in unit tests) everything below is simply there.
        </p>
      </div>

      <div key={run} className="grid gap-4 md:grid-cols-2">
        <div className="rounded-cell border bg-surface p-5">
          <p className="label-mono">REVEAL · STAGGER</p>
          <Reveal as="h3" className="mt-2 text-lg">
            A readout arrives
          </Reveal>
          <Stagger as="ul" className="mt-3 space-y-2" delay={0.1}>
            {EXAMPLES.map((example) => (
              <StaggerItem
                as="li"
                key={example}
                className="lift rounded-lg border bg-muted px-3 py-2 text-sm"
              >
                {example}
              </StaggerItem>
            ))}
          </Stagger>
        </div>

        <div className="rounded-cell border bg-surface p-5">
          <p className="label-mono">COUNTUP · DRAWPATH</p>
          <p className="mt-2 text-5xl font-semibold tracking-tight">
            <CountUp value={57} />
            <span className="text-fg-muted">/100</span>
          </p>
          <p className="text-sm text-fg-muted">BIRD-100 with hints (a real report's number)</p>
          <svg viewBox="0 0 220 60" className="mt-3 h-16 w-full text-signal" aria-hidden="true">
            <DrawPath
              d="M4 50 C 40 50, 50 12, 90 12 S 150 44, 216 10"
              fill="none"
              stroke="currentColor"
              strokeWidth={2}
              strokeLinecap="round"
            />
          </svg>
        </div>

        <div className="rounded-cell border bg-surface p-5 md:col-span-2">
          <p className="label-mono">SWAP</p>
          <div className="mt-2 flex gap-2">
            <Button
              size="sm"
              variant={tab === 'answer' ? 'secondary' : 'ghost'}
              aria-pressed={tab === 'answer'}
              onClick={() => setTab('answer')}
            >
              Answer
            </Button>
            <Button
              size="sm"
              variant={tab === 'sql' ? 'secondary' : 'ghost'}
              aria-pressed={tab === 'sql'}
              onClick={() => setTab('sql')}
            >
              SQL
            </Button>
          </div>
          <Swap id={tab} className="mt-3">
            {tab === 'answer' ? (
              <p className="text-lg">London's sales fell 18.4% in October.</p>
            ) : (
              <pre className="rounded-lg bg-muted p-3 text-sm whitespace-pre-wrap">
                <code>SELECT city, SUM(amount) FROM sales GROUP BY city;</code>
              </pre>
            )}
          </Swap>
        </div>
      </div>
    </div>
  )
}

export default function Styleguide() {
  return (
    <div className="canvas-grid min-h-dvh">
      <header className="sticky top-0 z-10 border-b bg-bg/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <div>
            <p aria-hidden="true" className="label-mono">
              SIGNAL LAB · ADR 026
            </p>
            <h1 className="text-xl">QueryMind · style guide</h1>
          </div>
          <ThemeSwitcher />
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-14 px-4 py-10">
        <p className="max-w-prose text-fg-muted">
          Dev-only page. Tokens live in <code>src/styles/tokens.css</code>, motion in{' '}
          <code>src/motion/</code>; the rules are in <code>docs/ui-design-system.md</code>. Switch
          the theme above: everything below must stay readable in both.
        </p>

        <Section title="Colour tokens" label="01 · COLOUR">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[...SURFACES, ...INKS, ...LINES].map((swatch) => (
              <Swatch key={swatch.token} {...swatch} />
            ))}
          </div>
          <InkOnSurfaces />
          <div className="flex flex-wrap gap-4">
            <div className="glow rounded-cell border bg-surface px-4 py-3 text-sm">
              .glow: the one active thing
            </div>
            <div className="lift rounded-cell border bg-surface px-4 py-3 text-sm">
              .lift: hover me
            </div>
          </div>
        </Section>

        <Section title="Type" label="02 · TYPE">
          <div className="space-y-3 rounded-cell border bg-surface p-6">
            {TYPE_SCALE.map(({ className, label }) => (
              <p key={label} className={className}>
                {label}
              </p>
            ))}
            <p className="label-mono">READOUT 01 · STEP 3/6 · Today (label-mono, 11 px)</p>
            {/* Wraps instead of scrolling: at 320 px a scroll box here had no keyboard access. */}
            <pre className="rounded-lg bg-muted p-4 text-sm break-words whitespace-pre-wrap">
              <code>{'SELECT category, SUM(amount) AS total\nFROM sales\nGROUP BY category;'}</code>
            </pre>
            <table className="text-sm tabular-nums">
              <caption className="pb-2 text-left text-xs text-fg-muted">
                Numbers use tabular figures, so columns line up
              </caption>
              <tbody>
                {['1,111.10', '88,888.80', '111.00'].map((value) => (
                  <tr key={value}>
                    <td className="pr-6 font-mono">{value}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </Section>

        <Section title="Buttons and fields" label="03 · CONTROLS">
          <div className="flex flex-wrap items-center gap-3">
            <Button>
              <Play aria-hidden="true" />
              Ask
            </Button>
            <Button variant="outline">Outline</Button>
            <Button variant="secondary">Secondary</Button>
            <Button variant="ghost">Ghost</Button>
            <Button variant="destructive">Destructive</Button>
            <Button variant="link">Link</Button>
            <Button disabled>Disabled</Button>
            <Button disabled aria-busy="true">
              <Loader2 aria-hidden="true" className="animate-spin motion-reduce:animate-none" />
              Thinking…
            </Button>
          </div>
          <div className="flex flex-wrap items-center gap-3">
            <Button size="sm">Small</Button>
            <Button>Default</Button>
            <Button size="lg">Large</Button>
            <Tooltip>
              <TooltipTrigger asChild>
                <Button size="icon" variant="outline" aria-label="Show the SQL">
                  <Code aria-hidden="true" />
                </Button>
              </TooltipTrigger>
              <TooltipContent>Show the SQL</TooltipContent>
            </Tooltip>
            <span className="text-sm text-fg-muted">
              <Kbd>Ctrl</Kbd> + <Kbd>K</Kbd>
            </span>
          </div>
          <label className="block max-w-sm text-sm font-medium">
            Email
            <input type="email" className={FIELD} placeholder="ana@example.com" />
          </label>
          <p className="text-xs text-fg-muted">
            Icon-only buttons always have an aria-label. On touch screens every button is at least
            44 px high. Pressed buttons scale to 0.97.
          </p>
        </Section>

        <Section title="Badges" label="04 · BADGES">
          <ConfidenceBadges />
          <p className="text-xs text-fg-muted">
            Colour is never the only signal: each badge has an icon and words.
          </p>
        </Section>

        <Section title="Readout" label="05 · READOUT">
          <article className="space-y-4 rounded-cell border bg-surface p-6">
            <header className="flex flex-wrap items-center justify-between gap-2">
              <div>
                <p aria-hidden="true" className="label-mono">
                  READOUT 01
                </p>
                <h3 className="mt-1 text-lg">Example question in a readout</h3>
              </div>
              <ConfidenceBadges />
            </header>
            <Tabs defaultValue="chart">
              <TabsList>
                <TabsTrigger value="chart">
                  <ChartColumn aria-hidden="true" />
                  Chart
                </TabsTrigger>
                <TabsTrigger value="table">
                  <Table2 aria-hidden="true" />
                  Table
                </TabsTrigger>
                <TabsTrigger value="sql">
                  <Code aria-hidden="true" />
                  SQL
                </TabsTrigger>
              </TabsList>
              <TabsContent value="chart" className="pt-2">
                <Skeleton className="h-40 w-full" />
              </TabsContent>
              <TabsContent value="table" className="pt-2">
                <div className="space-y-2">
                  <Skeleton className="h-6 w-full" />
                  <Skeleton className="h-6 w-5/6" />
                  <Skeleton className="h-6 w-4/6" />
                </div>
              </TabsContent>
              <TabsContent value="sql" className="pt-2">
                <pre className="rounded-lg bg-muted p-4 text-sm">
                  <code>SELECT 1;</code>
                </pre>
              </TabsContent>
            </Tabs>
            <Tabs defaultValue="key">
              {/* Wraps on a narrow page: three nowrap labels are wider than 320 px. */}
              <TabsList variant="line" className="h-auto max-w-full flex-wrap">
                <TabsTrigger value="key">Line tabs</TabsTrigger>
                <TabsTrigger value="databases">slide a lime bar</TabsTrigger>
                <TabsTrigger value="members">between them</TabsTrigger>
              </TabsList>
              {/* A panel per tab: each tab's aria-controls must name an element that exists
                  (axe: aria-valid-attr-value). Radix mounts only the open one. */}
              <TabsContent value="key" className="pt-2 text-sm text-fg-muted">
                The first tab&apos;s panel.
              </TabsContent>
              <TabsContent value="databases" className="pt-2 text-sm text-fg-muted">
                The second tab&apos;s panel.
              </TabsContent>
              <TabsContent value="members" className="pt-2 text-sm text-fg-muted">
                The third tab&apos;s panel.
              </TabsContent>
            </Tabs>
          </article>
        </Section>

        <Section title="Motion" label="06 · MOTION">
          <MotionBoard />
        </Section>

        <Section title="Overlays and feedback" label="07 · OVERLAYS">
          <div className="flex flex-wrap gap-3">
            <Dialog>
              <DialogTrigger asChild>
                <Button variant="outline">Open dialog</Button>
              </DialogTrigger>
              <DialogContent>
                <DialogHeader>
                  <DialogTitle>Start a new conversation?</DialogTitle>
                  <DialogDescription>
                    The next question won't be read as a follow-up.
                  </DialogDescription>
                </DialogHeader>
                <DialogFooter>
                  <DialogClose asChild>
                    <Button variant="outline">Cancel</Button>
                  </DialogClose>
                  <DialogClose asChild>
                    <Button>New conversation</Button>
                  </DialogClose>
                </DialogFooter>
              </DialogContent>
            </Dialog>

            <Sheet>
              <SheetTrigger asChild>
                <Button variant="outline">Show your work</Button>
              </SheetTrigger>
              <SheetContent>
                <SheetHeader>
                  <SheetTitle>Show your work</SheetTitle>
                  <SheetDescription>
                    The drawer that lists the tables, examples and SQL tries.
                  </SheetDescription>
                </SheetHeader>
              </SheetContent>
            </Sheet>

            <Button variant="outline" onClick={() => toast.success('Saved to your questions')}>
              Success toast
            </Button>
            <Button variant="outline" onClick={() => toast.error("Can't reach the API")}>
              Error toast
            </Button>
          </div>
        </Section>

        <Section title="Shape" label="08 · SHAPE">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            <li>Radius: 14 px on readouts and panels (rounded-cell), 10 px on controls.</li>
            <li>Hairline borders instead of shadows; one shadow for overlays.</li>
            <li>Spacing: steps of 4 / 8 px; section gaps 16 / 24 / 32 / 48 / 96.</li>
            <li>Motion: 150–320 ms, springs for movement, exits ~65 % of entries.</li>
            <li>Breakpoints: 375 · 768 (md) · 1024 (lg) · 1440 px.</li>
          </ul>
        </Section>
      </main>
    </div>
  )
}
