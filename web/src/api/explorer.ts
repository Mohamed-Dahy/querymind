/** What the data explorer reads: the tables, and what they mean. Metadata only, never a value. */
import { api, call } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type Catalog = Schemas['Catalog']
export type CatalogTable = Schemas['CatalogTable']
export type TableMeaning = Schemas['TableMeaning']
export type MetricMeaning = Schemas['MetricMeaning']
export type SemanticOverview = Schemas['SemanticOverview']
export type TableQuestionList = Schemas['TableQuestionList']

const TIMEOUT_MS = 20_000 // the first read of the catalog may have to count a few tables

/** Which database (U7.1b): one of the workspace's saved ones, or null for the demo. */
export type DatabaseId = string | null

/** `?connection_id=` for a saved database; nothing for the demo. */
function onDatabase(database: DatabaseId) {
  return database === null ? {} : { connection_id: database }
}

export function getCatalog(database: DatabaseId, signal?: AbortSignal): Promise<Catalog> {
  return call(TIMEOUT_MS, signal, (s) =>
    api.GET('/catalog', { params: { query: onDatabase(database) }, signal: s }),
  )
}

export function getSemantic(database: DatabaseId, signal?: AbortSignal): Promise<SemanticOverview> {
  return call(TIMEOUT_MS, signal, (s) =>
    api.GET('/semantic', { params: { query: onDatabase(database) }, signal: s }),
  )
}

/** Recent saved questions on this database whose SQL read this table. Fails with history off. */
export function getTableQuestions(
  table: string,
  database: DatabaseId,
  signal?: AbortSignal,
): Promise<TableQuestionList> {
  return call(TIMEOUT_MS, signal, (s) =>
    api.GET('/catalog/tables/{table}/questions', {
      params: { path: { table }, query: onDatabase(database) },
      signal: s,
    }),
  )
}
