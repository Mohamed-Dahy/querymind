import {
  ChartColumn,
  CircleCheck,
  Code,
  Info,
  Loader2,
  OctagonX,
  Play,
  Table2,
  TriangleAlert,
} from 'lucide-react'
import type { ReactNode } from 'react'
import { toast } from 'sonner'

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

// Dev-only page (http://localhost:5173/?styleguide): every token, type size and base component,
// so a design change can be checked in both themes in one place. Not included in production builds.

const SURFACES = [
  { token: '--bg', use: 'page and canvas' },
  { token: '--surface', use: 'cells, panels, dialogs' },
  { token: '--muted', use: 'inputs, stripes, hover' },
]

const INKS = [
  { token: '--fg', use: 'text' },
  { token: '--fg-muted', use: 'secondary text' },
  { token: '--signal', use: 'running agent, success, high confidence' },
  { token: '--warn', use: 'warnings, medium confidence' },
  { token: '--danger', use: 'errors, refusals, low confidence' },
  { token: '--info', use: 'trace steps, links' },
]

const LINES = [
  { token: '--border', use: 'decorative hairlines' },
  { token: '--input', use: 'control outlines (3:1)' },
  { token: '--ring', use: 'keyboard focus' },
]

const TYPE_SCALE = [
  { className: 'text-3xl font-heading font-semibold', label: 'Heading 1 · 30 px · Fira Code 600' },
  { className: 'text-2xl font-heading font-semibold', label: 'Heading 2 · 24 px · Fira Code 600' },
  { className: 'text-lg font-heading font-semibold', label: 'Heading 3 · 18 px · Fira Code 600' },
  { className: 'text-base', label: 'Body · 16 px · Fira Sans 400 · line-height 1.5' },
  { className: 'text-sm', label: 'Small · 14 px · Fira Sans 400 (tables, labels)' },
  {
    className: 'text-xs text-fg-muted',
    label: 'Caption · 12 px · Fira Sans 400 (never for body text)',
  },
]

function Section({ title, children }: { title: string; children: ReactNode }) {
  const id = `section-${title.toLowerCase().replace(/\W+/g, '-')}`
  return (
    <section aria-labelledby={id} className="space-y-4">
      <h2 id={id} className="text-lg">
        {title}
      </h2>
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
    </div>
  )
}

export default function Styleguide() {
  return (
    <div className="canvas-grid min-h-dvh">
      <header className="sticky top-0 z-10 border-b bg-bg/90 backdrop-blur-sm">
        <div className="mx-auto flex max-w-6xl flex-wrap items-center justify-between gap-3 px-4 py-3">
          <h1 className="text-xl">QueryMind · style guide</h1>
          <ThemeSwitcher />
        </div>
      </header>

      <main className="mx-auto max-w-6xl space-y-12 px-4 py-8">
        <p className="max-w-prose text-fg-muted">
          Dev-only page. Tokens live in <code>src/styles/tokens.css</code>; the rules are in{' '}
          <code>docs/ui-design-system.md</code>. Switch the theme above: everything below must stay
          readable in both.
        </p>

        <Section title="Colour tokens">
          <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[...SURFACES, ...INKS, ...LINES].map((swatch) => (
              <Swatch key={swatch.token} {...swatch} />
            ))}
          </div>
          <InkOnSurfaces />
        </Section>

        <Section title="Type">
          <div className="space-y-3 rounded-cell border bg-surface p-6">
            {TYPE_SCALE.map(({ className, label }) => (
              <p key={label} className={className}>
                {label}
              </p>
            ))}
            <pre className="overflow-x-auto rounded-lg bg-muted p-4 text-sm">
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

        <Section title="Buttons">
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
              <Loader2 aria-hidden="true" className="animate-spin" />
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
          </div>
          <p className="text-xs text-fg-muted">
            Icon-only buttons always have an aria-label. On touch screens every button is at least
            44 px high.
          </p>
        </Section>

        <Section title="Badges">
          <ConfidenceBadges />
          <p className="text-xs text-fg-muted">
            Colour is never the only signal: each badge has an icon and words.
          </p>
        </Section>

        <Section title="Cell">
          <article className="space-y-4 rounded-cell border bg-surface p-6 shadow-sm">
            <header className="flex flex-wrap items-center justify-between gap-2">
              <h3 className="text-base">Example question in a cell</h3>
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
          </article>
        </Section>

        <Section title="Overlays and feedback">
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
                    The drawer that will list the tables, examples and SQL tries (U2.5).
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

        <Section title="Shape and motion">
          <ul className="list-disc space-y-1 pl-5 text-sm">
            <li>Radius: 12 px on cells (rounded-cell), 8 px on controls (rounded-lg).</li>
            <li>Spacing: steps of 4 / 8 px (Tailwind p-1 = 4 px, p-2 = 8 px).</li>
            <li>
              Motion: 150–300 ms, ease-out to enter, faster to leave; off with reduced motion.
            </li>
            <li>Breakpoints: 375 · 768 (md) · 1024 (lg) · 1440 px.</li>
          </ul>
        </Section>
      </main>
    </div>
  )
}
