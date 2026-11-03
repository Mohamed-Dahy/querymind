/**
 * Where each table goes in the graph. React Flow draws, but doesn't place anything, so this does: one
 * block per business, stacked, and inside a block one column per foreign-key depth. A table with no
 * foreign keys is at depth 0, and a table pointing at it is further right.
 *
 * A pure function: the same tables always give the same picture, and overlaps can be tested without
 * a browser.
 */
import type { ExplorerTable } from './data'

export const NODE_WIDTH = 200
export const NODE_HEIGHT = 56
const COLUMN_GAP = 48
const ROW_GAP = 14
const BLOCK_PADDING = 16
const BLOCK_HEADER = 32
const BLOCK_GAP = 28
const ORDERING_SWEEPS = 3

export type PlacedTable = { name: string; x: number; y: number }
export type PlacedBlock = {
  business: string | null
  x: number
  y: number
  width: number
  height: number
}
export type Layout = { blocks: PlacedBlock[]; tables: PlacedTable[] }

type Links = Map<string, string[]>

/** How long the longest chain of foreign keys from each table is. A cycle just stops the count. */
function depthsOf(tables: readonly ExplorerTable[]): Map<string, number> {
  const inBlock = new Map(tables.map((table) => [table.name, table]))
  const depths = new Map<string, number>()
  const visiting = new Set<string>()

  function depth(name: string): number {
    const known = depths.get(name)
    if (known !== undefined) return known
    if (visiting.has(name)) return 0
    visiting.add(name)
    const references = (inBlock.get(name)?.references ?? []).filter((ref) => inBlock.has(ref))
    const found = Math.max(0, ...references.map((ref) => depth(ref) + 1))
    visiting.delete(name)
    depths.set(name, found)
    return found
  }

  for (const { name } of tables) depth(name)
  return depths
}

/**
 * Orders one column by the average row of each table's neighbours in the column it is compared with,
 * so lines cross less. Tables with no neighbour there go last. Ties go by name, so it's repeatable.
 */
function orderColumn(column: string[], other: readonly string[], links: Links): string[] {
  const rowOf = new Map(other.map((name, row) => [name, row]))
  const score = (name: string) => {
    const rows = (links.get(name) ?? []).flatMap((near) => rowOf.get(near) ?? [])
    return rows.length === 0
      ? Number.POSITIVE_INFINITY
      : rows.reduce((sum, row) => sum + row, 0) / rows.length
  }
  return [...column].sort((a, b) => {
    const [scoreA, scoreB] = [score(a), score(b)]
    if (scoreA === scoreB) return a.localeCompare(b)
    return scoreA - scoreB
  })
}

function columnsOf(tables: readonly ExplorerTable[]): string[][] {
  const depths = depthsOf(tables)
  const columns: string[][] = []
  for (const { name } of tables) {
    const depth = depths.get(name) ?? 0
    columns[depth] = [...(columns[depth] ?? []), name]
  }
  const links: Links = new Map(
    tables.map((table) => [table.name, [...table.references, ...table.referencedBy]]),
  )

  // Back and forth: each sweep orders every column against the one it has just passed.
  let ordered = columns.map((column) => column ?? [])
  for (let sweep = 0; sweep < ORDERING_SWEEPS; sweep += 1) {
    const forward = sweep % 2 === 0
    const indexes = ordered.map((_, index) => index)
    for (const index of forward ? indexes.slice(1) : indexes.reverse().slice(1)) {
      const next = [...ordered]
      next[index] = orderColumn(
        ordered[index] ?? [],
        ordered[forward ? index - 1 : index + 1] ?? [],
        links,
      )
      ordered = next
    }
  }
  return ordered
}

function columnHeight(rows: number): number {
  return rows * NODE_HEIGHT + Math.max(0, rows - 1) * ROW_GAP
}

/** Every table's place, and the frame around each business. */
export function layoutTables(tables: readonly ExplorerTable[]): Layout {
  const businesses = [...new Set(tables.map(({ business }) => business))].sort((a, b) => {
    if (a === null) return 1
    if (b === null) return -1
    return a.localeCompare(b)
  })

  const blocks: PlacedBlock[] = []
  const placed: PlacedTable[] = []
  let blockY = 0
  for (const business of businesses) {
    const inside = tables
      .filter((table) => table.business === business)
      .sort((a, b) => a.name.localeCompare(b.name))
    const columns = columnsOf(inside)
    const contentHeight = Math.max(...columns.map((column) => columnHeight(column.length)))
    const contentTop = blockY + BLOCK_HEADER

    columns.forEach((column, index) => {
      const x = BLOCK_PADDING + index * (NODE_WIDTH + COLUMN_GAP)
      // Centred, so short columns sit next to what they join to.
      const top = contentTop + (contentHeight - columnHeight(column.length)) / 2
      column.forEach((name, row) => {
        placed.push({ name, x, y: top + row * (NODE_HEIGHT + ROW_GAP) })
      })
    })

    const height = BLOCK_HEADER + contentHeight + BLOCK_PADDING
    blocks.push({
      business,
      x: 0,
      y: blockY,
      width: 2 * BLOCK_PADDING + columns.length * NODE_WIDTH + (columns.length - 1) * COLUMN_GAP,
      height,
    })
    blockY += height + BLOCK_GAP
  }
  return { blocks, tables: placed }
}
