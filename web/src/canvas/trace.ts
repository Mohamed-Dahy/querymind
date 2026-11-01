/**
 * The agent's events as what the rail (U2.4) draws: each step's state and time, and the SQL tries.
 * A pure function of the events, so a finished cell draws at once from its saved events (U3).
 */
import type { TraceEvent } from '@/api/stream'

/** The rail's steps. `validate` and `run` are one graph node (run_sql), split by its events. */
export const RAIL_STEPS = [
  'retrieve',
  'write',
  'validate',
  'run',
  'check',
  'repair',
  'summarize',
] as const
export type RailStepId = (typeof RAIL_STEPS)[number]

export type RailStepState = 'waiting' | 'running' | 'done' | 'failed' | 'skipped'
export type RailStep = { state: RailStepState; durationMs: number }

type StepEvent = Extract<TraceEvent, { type: 'step' }>
type AttemptFailed = Extract<TraceEvent, { type: 'attempt_failed' }>

/** One SQL try, and how it went wrong if it did. */
export type SqlTry = {
  attempt: number
  sql: string
  explanation: string
  failed?: { kind: AttemptFailed['kind']; message: string }
}

export type Rail = {
  steps: Record<RailStepId, RailStep>
  tries: SqlTry[]
  /** How many times the repair step ran. */
  repairs: number
}

const SINGLE_STEP: Record<Exclude<StepEvent['node'], 'run_sql'>, RailStepId> = {
  retrieve_schema: 'retrieve',
  generate_sql: 'write',
  check_result: 'check',
  repair_sql: 'repair',
  summarize: 'summarize',
}

// Which step a failed try blames: the validator refused it, the database did, or the check.
const BLAMED: Record<AttemptFailed['kind'], RailStepId> = {
  unsafe: 'validate',
  db_error: 'run',
  timeout: 'run',
  suspicious: 'check',
}

function startingSteps(): Rail['steps'] {
  const steps = {} as Rail['steps']
  for (const id of RAIL_STEPS) steps[id] = { state: 'waiting', durationMs: 0 }
  return steps
}

/**
 * `ended`: the run is over, so what never ran was skipped, and what was still running was cut off.
 * A later try replaces an earlier one's state (it runs again), and the times add up.
 */
export function railFrom(events: readonly TraceEvent[], { ended }: { ended: boolean }): Rail {
  const steps = startingSteps()
  const tries: SqlTry[] = []
  let repairs = 0

  function onRunSql({ status, duration_ms: ms }: StepEvent) {
    if (status === 'started') {
      steps.validate.state = 'running'
      steps.run.state = 'waiting'
      return
    }
    steps.run.durationMs += ms ?? 0
    for (const id of ['validate', 'run'] as const) {
      // A try that failed has already marked itself; only what was still running changes here.
      if (steps[id].state === 'running') steps[id].state = status === 'failed' ? 'failed' : 'done'
    }
  }

  function onStep(event: StepEvent) {
    if (event.node === 'run_sql') {
      onRunSql(event)
      return
    }
    const step = steps[SINGLE_STEP[event.node]]
    if (event.status === 'started') {
      step.state = 'running'
      if (event.node === 'repair_sql') repairs += 1
      return
    }
    step.durationMs += event.duration_ms ?? 0
    // check_result may have flagged the result already, so it stays failed; see BLAMED.
    if (step.state === 'running') step.state = event.status === 'failed' ? 'failed' : 'done'
  }

  for (const event of events) {
    switch (event.type) {
      case 'step':
        onStep(event)
        break
      case 'sql':
        tries.push({ attempt: event.attempt, sql: event.sql, explanation: event.explanation })
        break
      case 'validated':
        steps.validate.state = 'done'
        steps.run.state = 'running'
        break
      case 'attempt_failed': {
        steps[BLAMED[event.kind]].state = 'failed'
        const attempt = tries.find((one) => one.attempt === event.attempt)
        if (attempt) attempt.failed = { kind: event.kind, message: event.message }
        break
      }
      case 'schema':
      case 'rewrite':
      case 'examples':
      case 'checked':
        break // the drawer reads these from the final reply
      default:
        // tsc fails here when the API gains an event this file doesn't know. At runtime an event
        // from a newer API is ignored.
        event satisfies never
    }
  }

  if (ended) {
    for (const step of Object.values(steps)) {
      if (step.state === 'waiting') step.state = 'skipped'
      if (step.state === 'running') step.state = 'failed'
    }
  }
  return { steps, tries, repairs }
}

/** What a screen reader should hear during a run: only a repair, never every step. */
export function repairAnnouncement({ tries, repairs }: Rail): string {
  if (repairs === 0) return ''
  const failed = tries.findLast((one) => one.failed !== undefined)
  if (failed === undefined) return ''
  return failed.failed?.kind === 'suspicious'
    ? `The result of SQL try ${failed.attempt} looked wrong, repairing.`
    : `SQL try ${failed.attempt} failed, repairing.`
}
