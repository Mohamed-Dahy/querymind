import {
  Handle,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
  type NodeProps,
} from '@xyflow/react'
import '@xyflow/react/dist/base.css'
import { Maximize, ShieldAlert, ZoomIn, ZoomOut } from 'lucide-react'
import { useMemo } from 'react'

import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

import {
  businessLabel,
  formatRowCount,
  joinKey,
  neighboursOf,
  type ExplorerData,
  type ExplorerTable,
} from './data'
import { layoutTables, NODE_HEIGHT, NODE_WIDTH, type Layout } from './layout'

type Emphasis = 'focused' | 'near' | 'dimmed' | 'plain'
type TableNode = Node<{ table: ExplorerTable; emphasis: Emphasis }, 'table'>
type BlockNode = Node<{ label: string }, 'block'>

const HANDLE = 'pointer-events-none !size-px !min-w-0 !border-0 !bg-transparent'

function TableCard({ data: { table, emphasis } }: NodeProps<TableNode>) {
  return (
    <div
      className={cn(
        'flex size-full cursor-pointer flex-col justify-center rounded-lg border bg-surface px-3 shadow-sm',
        emphasis === 'focused' && 'border-2 border-signal',
        emphasis === 'near' && 'border-2 border-info',
        // A faint frame, not lower opacity: the text has to stay readable.
        emphasis === 'dimmed' && 'border-border bg-transparent shadow-none',
        emphasis === 'plain' && 'border-input',
      )}
      style={{ width: NODE_WIDTH, height: NODE_HEIGHT }}
      title={table.name}
    >
      {/* Foreign-key lines leave on the left, towards the table they point at. */}
      <Handle type="source" position={Position.Left} className={HANDLE} isConnectable={false} />
      <Handle type="target" position={Position.Right} className={HANDLE} isConnectable={false} />
      <span
        className={cn(
          'truncate text-sm font-medium',
          emphasis === 'dimmed' && 'font-normal text-fg-muted',
        )}
      >
        {table.name}
      </span>
      <span className="flex items-center gap-1.5 text-xs text-fg-muted">
        {formatRowCount(table.rowCount)}
        {table.piiColumns.length > 0 && (
          <span className="inline-flex items-center gap-0.5 text-warn">
            <ShieldAlert aria-hidden="true" className="size-3" />
            PII
          </span>
        )}
      </span>
    </div>
  )
}

function BusinessFrame({ data: { label } }: NodeProps<BlockNode>) {
  return (
    <div className="pointer-events-none size-full rounded-cell border border-dashed border-input">
      <p className="px-4 pt-2 text-xs tracking-wide text-fg-muted uppercase">{label}</p>
    </div>
  )
}

const NODE_TYPES = { table: TableCard, block: BusinessFrame }
const FIT = { padding: 0.02 }

function aspectRatioOf({ blocks }: Layout): string {
  const width = Math.max(...blocks.map((block) => block.x + block.width))
  const height = Math.max(...blocks.map((block) => block.y + block.height))
  return `${width} / ${height}`
}

function emphasisOf(name: string, focused: ExplorerTable | undefined): Emphasis {
  if (focused === undefined) return 'plain'
  if (name === focused.name) return 'focused'
  return neighboursOf(focused).includes(name) ? 'near' : 'dimmed'
}

function buildNodes(
  tables: readonly ExplorerTable[],
  { blocks, tables: placed }: Layout,
  focused: ExplorerTable | undefined,
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
    data: { table: byName.get(name) as ExplorerTable, emphasis: emphasisOf(name, focused) },
    width: NODE_WIDTH,
    height: NODE_HEIGHT,
  }))
  return [...frames, ...cards]
}

function buildEdges(
  tables: readonly ExplorerTable[],
  preferredJoins: ReadonlySet<string>,
  focused: ExplorerTable | undefined,
): Edge[] {
  return tables.flatMap((table) =>
    table.references.map((target): Edge => {
      const preferred = preferredJoins.has(joinKey(table.name, target))
      const touchesFocus =
        focused === undefined || focused.name === table.name || focused.name === target
      return {
        id: `${table.name}->${target}`,
        source: table.name,
        target,
        style: {
          stroke: preferred ? 'var(--info)' : 'var(--input)',
          strokeWidth: preferred ? 2 : 1,
          opacity: touchesFocus ? 1 : 0.2,
        },
      }
    }),
  )
}

function describe({ tables }: ExplorerData, focused: ExplorerTable | undefined): string {
  const joins = tables.reduce((sum, table) => sum + table.references.length, 0)
  const whole = `Graph of ${tables.length} tables and the ${joins} joins between them. The list has the same tables and joins.`
  if (focused === undefined) return whole
  return `${whole} ${focused.name} is highlighted, with the ${neighboursOf(focused).length} tables it joins.`
}

function ZoomButtons() {
  const { zoomIn, zoomOut, fitView } = useReactFlow()
  return (
    <div className="flex gap-1">
      <Button
        type="button"
        variant="outline"
        size="icon-sm"
        aria-label="Zoom in"
        title="Zoom in"
        onClick={() => void zoomIn({ duration: 0 })}
      >
        <ZoomIn aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="outline"
        size="icon-sm"
        aria-label="Zoom out"
        title="Zoom out"
        onClick={() => void zoomOut({ duration: 0 })}
      >
        <ZoomOut aria-hidden="true" />
      </Button>
      <Button
        type="button"
        variant="outline"
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
  onFocus: (table: string | null) => void
}

/**
 * The tables as a graph, for people who can see it and use a pointer. Everyone else has the list next
 * to it, which holds the same tables and joins. So the graph is one picture with a description: its
 * nodes aren't Tab stops, and the only controls are the buttons outside it.
 */
export function Constellation({ data, focused, onFocus }: ConstellationProps) {
  const { tables, preferredJoins } = data
  const layout = useMemo(() => layoutTables(tables), [tables])
  const nodes = useMemo(() => buildNodes(tables, layout, focused), [tables, layout, focused])
  const edges = useMemo(
    () => buildEdges(tables, preferredJoins, focused),
    [tables, preferredJoins, focused],
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
          role="img"
          aria-label={describe(data, focused)}
          // The graph's own proportions, so fitting it to the width keeps the names readable. It may
          // be taller than the screen: the page scrolls, and the list stays beside it.
          style={{ aspectRatio: aspectRatioOf(layout) }}
          className="rounded-cell border bg-bg"
        >
          <ReactFlow
            nodes={nodes}
            edges={edges}
            nodeTypes={NODE_TYPES}
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
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_, node) => {
              if (node.type === 'table') onFocus(node.id === focused?.name ? null : node.id)
            }}
            onPaneClick={() => onFocus(null)}
          />
        </div>
        <p className="mt-1 text-xs text-fg-muted">
          Drawn with{' '}
          <a
            href="https://reactflow.dev"
            target="_blank"
            rel="noreferrer"
            className="underline underline-offset-4"
          >
            React Flow
          </a>
          .
        </p>
      </section>
    </ReactFlowProvider>
  )
}
