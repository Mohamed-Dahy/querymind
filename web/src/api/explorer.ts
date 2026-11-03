/** What the data explorer reads: the tables, and what they mean. Metadata only, never a value. */
import { api, call } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type Catalog = Schemas['Catalog']
export type SemanticOverview = Schemas['SemanticOverview']

const TIMEOUT_MS = 20_000 // the first read of the catalog may have to count a few tables

export function getCatalog(signal?: AbortSignal): Promise<Catalog> {
  return call(TIMEOUT_MS, signal, (s) => api.GET('/catalog', { signal: s }))
}

export function getSemantic(signal?: AbortSignal): Promise<SemanticOverview> {
  return call(TIMEOUT_MS, signal, (s) => api.GET('/semantic', { signal: s }))
}
