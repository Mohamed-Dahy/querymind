/** What the evals lab reads: the saved eval results, from one committed file on the server. */
import { api, call } from './client'
import type { components } from './schema'

type Schemas = components['schemas']
export type EvalsReply = Schemas['EvalsReply']
export type EvalRun = Schemas['EvalRun']
export type EvalOutcome = Schemas['EvalOutcome']
export type EvalQuestion = Schemas['EvalQuestion']
export type EvalScore = Schemas['EvalScore']
export type AblationStudy = Schemas['AblationStudy']
export type AblationArm = Schemas['AblationArm']
export type ModelStudy = Schemas['ModelStudy']
export type ModelRow = Schemas['ModelRow']
export type Milestone = Schemas['Milestone']
export type SpeedUp = Schemas['SpeedUp']
export type QualityPanel = Schemas['QualityPanel']

const TIMEOUT_MS = 20_000

export function getEvals(signal?: AbortSignal): Promise<EvalsReply> {
  return call(TIMEOUT_MS, signal, (s) => api.GET('/evals', { signal: s }))
}
