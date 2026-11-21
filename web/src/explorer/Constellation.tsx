import {
  getBezierPath,
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type EdgeProps,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/base.css'
import { Maximize, ShieldAlert, ZoomIn, ZoomOut } from 'lucide-react'
import { useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'
import { DrawPath } from '@/motion'

import {
  businessLabel,
  formatRowCount,
  joinKey,
  neighboursOf,
  type ExplorerData,
  type ExplorerTable,
  type Highlights,
} from './data'
import { layoutTables, NODE_HEIGHT, NODE_WIDTH, type Layout } from './layout'

type Emphasis = 'focused' | 'near' | 'used' | 'shown' | 'dimmed' | 'plain'
type TableNode = Node<{ table: ExplorerTable; emphasis: Emphasis }, 'table'>
type BlockNode = Node<{ label: string }, 'block'>
/** A join line: its colour says why it is lit, its width whether the semantic layer prefers it. */
type JoinEdge = Edge<{ stroke: string; width: number; lit: boolean }, 'join'>

const HANDLE = 'pointer-events-none !size-px !min-w-0 !border-0 !bg-transparent'

/** The small light in a card's corner: lime for the chosen or used table, blue for its neighbours. */
const DOT: Record<Emphasis, string> = {
  focused: 'bg-signal',
  used: 'bg-signal',
  near: 'bg-info',
  shown: 'bg-info',
  dimmed: 'bg-border',
  plain: 'bg-input',
}

function TableCard({ data: { table, emphasis } }: NodeProps<TableNode>) {
  return (
    <div
      className={cn(
        // Signal Lab (U8.5): a hairline instrument card. Colour, border and glow change smoothly;
        // size and position never do (the cards must not move or overlap).
        'relative flex size-full cursor-pointer flex-col justify-center rounded-[10px] border bg-surface px-3',
        'transition-[border-color,box-shadow,background-color] duration-200 ease-out-expo hover:border-fg-muted',
        // The chosen table and the answer's tables are lit: a lime frame with a soft glow.
        emphasis === 'focused' && 'glow border-2 border-signal hover:border-signal',
        emphasis === 'near' && 'border-2 border-info hover:border-info',
        emphasis === 'used' && 'glow border-2 border-signal hover:border-signal',
        emphasis === 'shown' && 'border-2 border-dashed border-info hover:border-info',
        // A faint frame on the page's own colour, not lower opacity: the text has to stay readable.
        emphasis === 'dimmed' && 'border-border bg-bg',
        emphasis === 'plain' && 'border-input',
      )}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
      title={table.name}
    >
      {/* Foreign-key lines leave on the left, towards the table they point at. */}
      <Handle type="source" position={Position.Left} className={HANDLE} isConnectable={false} />
      <Handle type="target" position={Position.Right} className={HANDLE} isConnectable={false} />
      <span
        aria-hidden="true"
        className={cn('absolute top-2 right-2 size-1.5 rounded-full', DOT[emphasis])}
      />
      <span
        className={cn(
          'truncate pr-3 text-sm font-medium tracking-tight',
          emphasis === 'dimmed' && 'font-normal text-fg-muted',
        )}
      >
        {table.name}
      </span>
      <span className="flex items-center gap-1.5 font-mono text-[11px] text-fg-muted tabular-nums">
        {formatRowCount(table.rowCount)}
        {table.piiColumns.length > 0 && (
          <span className="inline-flex items-center gap-0.5 text-warn">
            <ShieldAlert aria-hidden="true" className="size-3" />
            PII
          </span>
        )}
        {emphasis === 'used' && <span className="font-medium text-signal">used</span>}
        {emphasis === 'shown' && <span className="text-info">shown</span>}
      </span>
    </div>
  )
}

function BusinessFrame({ data: { label } }: NodeProps<BlockNode>) {
  return (
    <div className="pointer-events-none size-full rounded-cell border border-dashed border-input">
      {/* CSS capitals: the text itself stays "Chinook", which is what the graph's tests look for. */}
      <p className="label-mono px-4 pt-2.5 uppercase">{label}</p>
    </div>
  )
}

/**
 * A join line that draws itself in when the graph first appears (U8.5), like a signal being traced.
 * It is the curve React Flow draws by default (`getBezierPath`), so nothing moves. Its id never
 * changes, so choosing a table recolours the line without drawing it again. The dimming sits on the
 * group, not on the path: `DrawPath` animates the path's own opacity to 1, and the two would fight.
 */
function JoinLine({
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
}: EdgeProps<JoinEdge>) {
  const [path] = getBezierPath({
    sourceX,
    sourceY,
    targetX,
    targetY,
    sourcePosition,
    targetPosition,
  })
  return (
    <g
      className="transition-opacity duration-200 ease-out-expo"
      style={{ opacity: data?.lit === false ? 0.2 : 1 }}
    >
      <DrawPath
        d={path}
        delay={0.15}
        fill="none"
        className="react-flow__edge-path"
        style={{
          stroke: data?.stroke,
          strokeWidth: data?.width,
          transition: 'stroke 200ms var(--ease-out-expo)',
        }}
      />
    </g>
  )
}

const NODE_TYPES = { table: TableCard, block: BusinessFrame }
const EDGE_TYPES = { join: JoinLine }
const FIT = { padding: 0.02 }

function aspectRatioOf({ blocks }: Layout): string {
  const width = Math.max(...blocks.map((block) => block.x + block.width))
  const height = Math.max(...blocks.map((block) => block.y + block.height))
  return `${width} / ${height}`
}

function emphasisOf(
  name: string,
  focused: ExplorerTable | undefined,
  { used, shown }: Highlights,
): Emphasis {
  if (focused !== undefined) {
    if (name === focused.name) return 'focused'
    return neighboursOf(focused).includes(name) ? 'near' : 'dimmed'
  }
  if (used.has(name)) return 'used'
  if (shown.has(name)) return 'shown'
  return used.size + shown.size > 0 ? 'dimmed' : 'plain'
}

function buildNodes(
  tables: readonly ExplorerTable[],
  { blocks, tables: placed }: Layout,
  focused: ExplorerTable | undefined,
  highlights: Highlights,
): (TableNode | BlockNode)[] {
  const byName = new Map(tables.map((table) => [table.name, table]))
  const frames = blocks.map(({ business, x, y, width, height }): BlockNode => ({
    id: `business:${business ?? 'other'}`,
    type: 'block',
    position: { x, y },
    data: { label: businessLabel(business) },
    width,
    height,
    selectable: false,
    draggable: false,
    style: { pointerEvents: 'none' },
  }))
  const cards = placed.map(({ name, x, y }): TableNode => ({
    id: name,
    type: 'table',
    position: { x, y },
    data: {
      table: byName.get(name) as ExplorerTable,
      emphasis: emphasisOf(name, focused, highlights),
    },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  }))
  return [...frames, ...cards]
}

function buildEdges(
  tables: readonly ExplorerTable[],
  preferredJoins: ReadonlySet<string>,
  focused: ExplorerTable | undefined,
  { used }: Highlights,
): JoinEdge[] {
  return tables.flatMap((table) =>
    table.references.map((target): JoinEdge => {
      const preferred = preferredJoins.has(joinKey(table.name, target))
      // A chosen table's own lines; or, with none chosen, the lines between tables the SQL read.
      const lit =
        focused !== undefined
          ? focused.name === table.name || focused.name === target
          : used.size === 0 || (used.has(table.name) && used.has(target))
      // Lime when a choice lit the line (a table, or an answer's tables). Otherwise a quiet line,
      // a little brighter when the semantic layer prefers the join.
      const chosen = lit && (focused !== undefined || used.size > 0)
      return {
        id: `${table.name}->${target}`,
        type: 'join',
        source: table.name,
        target,
        data: {
          stroke: chosen ? 'var(--signal)' : preferred ? 'var(--fg-muted)' : 'var(--input)',
          width: preferred ? 2 : 1,
          lit,
        },
      }
    }),
  )
}

function describe(
  { tables }: ExplorerData,
  focused: ExplorerTable | undefined,
  { used, shown }: Highlights,
): string {
  const joins = tables.reduce((sum, table) => sum + table.references.length, 0)
  const whole = `Graph of ${tables.length} tables and the ${joins} joins between them. The list has the same tables and joins.`
  if (focused === undefined) {
    const lit = [
      used.size > 0 && `The answer's SQL read ${[...used].join(', ')}.`,
      shown.size > 0 && `It was also shown ${[...shown].join(', ')}.`,
    ].filter(Boolean)
    return [whole, ...lit].join(' ')
  }
  return `${whole} ${focused.name} is highlighted, with the ${neighboursOf(focused).length} tables it joins.`
}

function ZoomButtons() {
  const { zoomIn, zoomOut, fitView } = useReactFlow()
  return (
    <div className="flex gap-0.5 rounded-lg border bg-surface p-0.5">
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Zoom in"
        title="Zoom in"
        onClick={() => void zoomIn({ duration: 0 })}
      >
        <ZoomIn aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Zoom out"
        title="Zoom out"
        onClick={() => void zoomOut({ duration: 0 })}
      >
        <ZoomOut aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="ghost"
        size="icon-sm"
        aria-label="Fit the graph to its area"
        title="Fit the graph to its area"
        onClick={() => void fitView({ ...FIT, duration: 0 })}
      >
        <Maximize aria-hidden="true" />
      </Button>
    </div>
  )
}

type ConstellationProps = {
  data: ExplorerData
  focused: ExplorerTable | undefined
  highlights: Highlights
  onFocus: (table: string | null) => void
}

/**
 * The tables as a graph, for people who can see it and use a pointer. Everyone else has the list next
 * to it, which holds the same tables and joins. So the graph is labelled with a description, and its
 * nodes aren't Tab stops. The only controls are the buttons outside it; the only link is the credit.
 */
export function Constellation({ data, focused, highlights, onFocus }: ConstellationProps) {
  const { tables, preferredJoins } = data
  const layout = useMemo(() => layoutTables(tables), [tables])
  const nodes = useMemo(
    () => buildNodes(tables, layout, focused, highlights),
    [tables, layout, focused, highlights],
  )
  const edges = useMemo(
    () => buildEdges(tables, preferredJoins, focused, highlights),
    [tables, preferredJoins, focused, highlights],
  )

  return (
    <ReactFlowProvider>
      <section aria-label="Graph of tables" className="min-w-0">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <p className="text-sm text-fg-muted">
            Thick lines are the joins the semantic layer prefers. Click a table to see what it
            joins.
          </p>
          <ZoomButtons />
        </div>
        <div
          role="group"
          aria-label={describe(data, focused, highlights)}
          // The graph's own proportions, so fitting it to the width keeps the names readable. It may
          // be taller than the screen: the page scrolls, and the list stays beside it.
          style={{ aspectRatio: aspectRatioOf(layout) }}
          // React Flow's credit stays, as its authors ask, in colours that pass the contrast check. With
          // `!`, because its stylesheet is outside Tailwind's layers, and so beats them otherwise.
          // `canvas-grid`: the canvas's hairline grid. The e2e axe check removes it, since it can't
          // measure text over a pattern; every card has a solid background of its own.
          className="canvas-grid rounded-cell border [&_.react-flow\_\_attribution]:bg-transparent! [&_.react-flow\_\_attribution_a]:text-fg-muted!"
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
            edgeTypes={EDGE_TYPES}
            fitView
            fitViewOptions={FIT}
            minZoom={0.2}
            maxZoom={1.5}
            nodesDraggable={false}
            nodesConnectable={false}
            nodesFocusable={false}
            edgesFocusable={false}
            elementsSelectable={false}
            disableKeyboardA11y
            // The wheel scrolls the page, as everywhere else. Dragging pans; the buttons zoom.
            zoomOnScroll={false}
            preventScrolling={false}
            onNodeClick={(_, node) => {
              if (node.type === 'table') onFocus(node.id === focused?.name ? null : node.id)
            }}
            onPaneClick={() => onFocus(null)}
          />
        </div>
      </section>
    </ReactFlowProvider>
  )
}
