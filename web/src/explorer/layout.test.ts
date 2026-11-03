import { describe, expect, it } from 'vitest'

import { catalog, semantic } from '@/mocks/fixtures'

import { joinData, type ExplorerTable } from './data'
import { layoutTables, NODE_HEIGHT, NODE_WIDTH, type PlacedTable } from './layout'

const { tables } = joinData(catalog, semantic)

function table(name: string, references: string[] = [], business = 'shop'): ExplorerTable {
  return {
    name,
    business,
    rowCount: 1,
    columns: [],
    synonyms: [],
    piiColumns: [],
    references,
    referencedBy: [],
  }
}

function overlaps(a: PlacedTable, b: PlacedTable): boolean {
  return (
    a.x < b.x + NODE_WIDTH &&
    b.x < a.x + NODE_WIDTH &&
    a.y < b.y + NODE_HEIGHT &&
    b.y < a.y + NODE_HEIGHT
  )
}

const place = (input: ExplorerTable[], name: string) =>
  layoutTables(input).tables.find((placed) => placed.name === name) as PlacedTable

describe('layoutTables on the real tables', () => {
  const layout = layoutTables(tables)

  it('places every table once', () => {
    expect(layout.tables.map(({ name }) => name).sort()).toEqual(
      tables.map(({ name }) => name).sort(),
    )
  })

  it('no two tables overlap', () => {
    const clashes = layout.tables.flatMap((a, index) =>
      layout.tables
        .slice(index + 1)
        .filter((b) => overlaps(a, b))
        .map((b) => `${a.name} / ${b.name}`),
    )
    expect(clashes).toEqual([])
  })

  it('gives each business its own frame, and they do not overlap', () => {
    expect(layout.blocks.map(({ business }) => business)).toEqual(['chinook', 'northwind'])
    const [first, second] = layout.blocks
    expect((first?.y ?? 0) + (first?.height ?? 0)).toBeLessThan(second?.y ?? 0)
  })

  it('keeps every table inside its own business frame', () => {
    const outside = layout.tables.filter(({ name, x, y }) => {
      const business = tables.find((one) => one.name === name)?.business
      const frame = layout.blocks.find((block) => block.business === business)
      return (
        frame === undefined ||
        x < frame.x ||
        y < frame.y ||
        x + NODE_WIDTH > frame.x + frame.width ||
        y + NODE_HEIGHT > frame.y + frame.height
      )
    })
    expect(outside).toEqual([])
  })

  it('gives the same picture every time, whatever order the tables come in', () => {
    expect(layoutTables([...tables].reverse())).toEqual(layout)
  })

  it('puts a table to the right of what it points at', () => {
    const at = new Map(layout.tables.map((placed) => [placed.name, placed]))
    const backwards = tables.flatMap((one) =>
      one.references
        .filter((ref) => (at.get(ref)?.x ?? 0) >= (at.get(one.name)?.x ?? 0))
        .map((ref) => `${one.name} -> ${ref}`),
    )
    expect(backwards).toEqual([])
  })
})

describe('layoutTables on awkward tables', () => {
  it('a cycle of foreign keys ends, and places both', () => {
    const input = [table('a', ['b']), table('b', ['a'])]

    expect(
      layoutTables(input)
        .tables.map(({ name }) => name)
        .sort(),
    ).toEqual(['a', 'b'])
  })

  it('a key to a table outside the business does not change its depth', () => {
    const input = [table('a', ['elsewhere']), table('b')]

    expect(place(input, 'a').x).toBe(place(input, 'b').x)
  })

  it('a table no business covers goes last, in its own frame', () => {
    const input = [table('a'), { ...table('b'), business: null }]

    expect(layoutTables(input).blocks.map(({ business }) => business)).toEqual(['shop', null])
  })

  it('no tables at all is an empty picture', () => {
    expect(layoutTables([])).toEqual({ blocks: [], tables: [] })
  })
})
