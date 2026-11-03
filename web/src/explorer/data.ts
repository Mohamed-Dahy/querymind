/** The two API responses as one list of tables, in the shape the explorer's screens need. */
import type {
  Catalog,
  CatalogTable,
  MetricMeaning,
  SemanticOverview,
  TableMeaning,
} from '@/api/explorer'

export type ExplorerTable = {
  name: string
  business: string | null
  rowCount: number | null
  columns: string[]
  synonyms: string[]
  piiColumns: string[]
  /** The tables this one's foreign keys point to. A key to itself isn't a join between tables. */
  references: string[]
  /** The tables whose foreign keys point to this one. */
  referencedBy: string[]
}

export type ExplorerData = {
  tables: ExplorerTable[]
  /** The table pairs the semantic layer prefers to join: see `joinKey`. */
  preferredJoins: Set<string>
}

/** The same key whichever way round the two tables are given. */
export function joinKey(one: string, other: string): string {
  return one < other ? `${one}|${other}` : `${other}|${one}`
}

export function joinData(catalog: Catalog, overview: SemanticOverview): ExplorerData {
  const meanings = new Map(
    overview.businesses.flatMap(({ tables }) => tables.map((table) => [table.name, table])),
  )
  const referencedBy = new Map<string, string[]>()
  const referencesOf = new Map<string, string[]>()
  for (const { name, foreign_keys: keys } of catalog.tables) {
    const references = [...new Set(keys.map(({ ref_table: target }) => target))]
      .filter((target) => target !== name)
      .sort()
    referencesOf.set(name, references)
    for (const target of references) {
      referencedBy.set(target, [...(referencedBy.get(target) ?? []), name])
    }
  }

  const tables = catalog.tables.map((table): ExplorerTable => {
    const meaning = meanings.get(table.name)
    return {
      name: table.name,
      business: table.business,
      rowCount: table.row_count,
      columns: table.columns.map(({ name }) => name),
      synonyms: meaning?.synonyms ?? [],
      piiColumns: (meaning?.columns ?? []).filter(({ pii }) => pii).map(({ name }) => name),
      references: referencesOf.get(table.name) ?? [],
      referencedBy: (referencedBy.get(table.name) ?? []).sort(),
    }
  })
  const preferredJoins = new Set(
    overview.businesses.flatMap(({ joins }) =>
      joins.map(({ left, right }) =>
        joinKey(left.split('.')[0] as string, right.split('.')[0] as string),
      ),
    ),
  )
  return { tables, preferredJoins }
}

/** Finds a table by its name, another word for it, or one of its columns. */
export function matchesSearch(table: ExplorerTable, search: string): boolean {
  const needle = search.trim().toLowerCase()
  return (
    !needle ||
    [table.name, ...table.synonyms, ...table.columns].some((word) =>
      word.toLowerCase().includes(needle),
    )
  )
}

/** The tables joined to this one, either way round. */
export function neighboursOf(table: ExplorerTable): string[] {
  return [...new Set([...table.references, ...table.referencedBy])].sort()
}

export function formatRowCount(count: number | null): string {
  if (count === null) return 'rows unknown'
  return `${count.toLocaleString('en')} ${count === 1 ? 'row' : 'rows'}`
}

/** "chinook" → "Chinook"; a table no semantic layer covers goes under "Other". */
export function businessLabel(business: string | null): string {
  return business === null ? 'Other' : business.charAt(0).toUpperCase() + business.slice(1)
}

/** "late_shipment" → "Late shipment". */
export function termName(name: string): string {
  const words = name.replaceAll('_', ' ')
  return words.charAt(0).toUpperCase() + words.slice(1)
}

export type Relation = {
  id: string
  /** `table.column`: the side holding the foreign key. Several columns are joined with commas. */
  from: string
  /** `table.column`: the side it points to. */
  to: string
  /** The table at the far end, from this page's point of view. */
  other: string
  preferred: boolean
  note: string
}

export type TableDetails = {
  table: CatalogTable
  meaning: TableMeaning | undefined
  businessDescription: string
  metrics: MetricMeaning[]
  relations: Relation[]
}

const qualified = (table: string, columns: readonly string[]) =>
  columns.map((column) => `${table}.${column}`).join(', ')

/** All one table's page needs, or undefined if there is no such table. */
export function tableDetails(
  name: string,
  catalog: Catalog,
  overview: SemanticOverview,
): TableDetails | undefined {
  const table = catalog.tables.find((one) => one.name === name)
  if (table === undefined) return undefined
  const business = overview.businesses.find(({ database }) => database === table.business)

  const joins = overview.businesses.flatMap((one) => one.joins)
  const relations = catalog.tables.flatMap((source) =>
    source.foreign_keys
      .filter(({ ref_table: target }) => source.name === name || target === name)
      .map(({ columns, ref_table: target, ref_columns: refColumns }): Relation => {
        const from = qualified(source.name, columns)
        const to = qualified(target, refColumns)
        const join = joins.find(
          ({ left, right }) => (left === from && right === to) || (left === to && right === from),
        )
        return {
          id: `${from}->${to}`,
          from,
          to,
          other: source.name === name ? target : source.name,
          preferred: join !== undefined,
          note: join?.note ?? '',
        }
      }),
  )
  return {
    table,
    meaning: business?.tables.find((one) => one.name === name),
    businessDescription: business?.description ?? '',
    metrics: (business?.metrics ?? []).filter((metric) => metric.table === name),
    relations,
  }
}
