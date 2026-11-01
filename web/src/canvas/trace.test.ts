import { describe, expect, it } from 'vitest'

import type { TraceEvent } from '@/api/stream'
import { answerStream, clarificationStream, repairStream } from '@/mocks/fixtures'

import {
  formatDuration,
  railFrom,
  repairAnnouncement,
  type RailStepId,
  type RailStepState,
} from './trace'

function states(events: readonly TraceEvent[], ended = false) {
  const { steps } = railFrom(events, { ended })
  return Object.fromEntries(Object.entries(steps).map(([id, step]) => [id, step.state])) as Record<
    RailStepId,
    RailStepState
  >
}

/** The events of the recorded repair, up to and including the one that matches. */
function until(events: readonly TraceEvent[], match: (event: TraceEvent) => boolean) {
  return events.slice(0, events.findIndex(match) + 1)
}

const isStep = (node: string, status: string) => (event: TraceEvent) =>
  event.type === 'step' && event.node === node && event.status === status

describe('railFrom on recorded runs', () => {
  it('a clean answer: all done, repair skipped, and each step has its time', () => {
    const rail = railFrom(answerStream, { ended: true })

    expect(states(answerStream, true)).toEqual({
      retrieve: 'done',
      write: 'done',
      validate: 'done',
      run: 'done',
      check: 'done',
      repair: 'skipped',
      summarize: 'done',
    })
    expect(rail.repairs).toBe(0)
    expect(rail.tries.map((one) => one.attempt)).toEqual([1])
    expect(rail.tries[0]?.failed).toBeUndefined()
    expect(rail.steps.write.durationMs).toBeGreaterThan(0)
  })

  it('a repair: every try is kept, the failed one says why, and it ends all done', () => {
    const rail = railFrom(repairStream, { ended: true })

    expect(rail.repairs).toBe(1)
    expect(rail.tries.map((one) => one.attempt)).toEqual([1, 2])
    expect(rail.tries[0]?.failed?.kind).toBe('db_error')
    expect(rail.tries[0]?.failed?.message).toMatch(/GROUP BY/)
    expect(rail.tries[1]?.failed).toBeUndefined()
    expect(states(repairStream, true)).toMatchObject({ run: 'done', repair: 'done', check: 'done' })
  })

  it('a question back: only retrieve and write ran', () => {
    expect(states(clarificationStream, true)).toEqual({
      retrieve: 'done',
      write: 'done',
      validate: 'skipped',
      run: 'skipped',
      check: 'skipped',
      repair: 'skipped',
      summarize: 'skipped',
    })
  })
})

describe('railFrom while it runs', () => {
  it('nothing yet: all waiting', () => {
    expect(new Set(Object.values(states([])))).toEqual(new Set(['waiting']))
  })

  it('run_sql started: validating; `validated`: running, validate done', () => {
    const started = until(repairStream, isStep('run_sql', 'started'))
    expect(states(started)).toMatchObject({ write: 'done', validate: 'running', run: 'waiting' })

    const validated = until(repairStream, (event) => event.type === 'validated')
    expect(states(validated)).toMatchObject({ validate: 'done', run: 'running' })
  })

  it('a failed query: run failed, then running again after the repair', () => {
    const failed = until(repairStream, (event) => event.type === 'attempt_failed')
    expect(states(failed)).toMatchObject({ validate: 'done', run: 'failed', repair: 'waiting' })

    const repairing = until(repairStream, isStep('repair_sql', 'started'))
    expect(states(repairing)).toMatchObject({ run: 'failed', repair: 'running' })

    const second = repairStream.findLastIndex(isStep('run_sql', 'started'))
    expect(states(repairStream.slice(0, second + 1))).toMatchObject({
      validate: 'running',
      run: 'waiting',
      repair: 'done',
    })
  })

  it('the times of both tries add up', () => {
    const { steps } = railFrom(
      [
        { type: 'step', node: 'run_sql', status: 'finished', duration_ms: 20 },
        { type: 'step', node: 'run_sql', status: 'finished', duration_ms: 5 },
      ],
      { ended: false },
    )

    expect(steps.run.durationMs).toBe(25)
  })
})

describe('other endings', () => {
  it('unsafe SQL: the validator failed, and run never started', () => {
    const events: TraceEvent[] = [
      { type: 'step', node: 'run_sql', status: 'started' },
      { type: 'attempt_failed', attempt: 1, kind: 'unsafe', message: 'Only SELECT.' },
      { type: 'step', node: 'run_sql', status: 'finished', duration_ms: 1 },
    ]

    expect(states(events)).toMatchObject({ validate: 'failed', run: 'waiting' })
  })

  it('a result that looks wrong: the check failed, and stays failed when it finishes', () => {
    const events: TraceEvent[] = [
      { type: 'step', node: 'check_result', status: 'started' },
      { type: 'checked', warnings: [{ code: 'empty', message: 'No rows.' }] },
      { type: 'attempt_failed', attempt: 1, kind: 'suspicious', message: 'No rows.' },
      { type: 'step', node: 'check_result', status: 'finished', duration_ms: 1 },
    ]

    expect(states(events)).toMatchObject({ check: 'failed' })
  })

  it('a step that failed (the LLM, say)', () => {
    const events: TraceEvent[] = [
      { type: 'step', node: 'generate_sql', status: 'started' },
      { type: 'step', node: 'generate_sql', status: 'failed', duration_ms: 3 },
    ]

    expect(states(events)).toMatchObject({ write: 'failed' })
  })

  it('a run cut off in the middle: what was running failed, the rest was skipped', () => {
    const events: TraceEvent[] = [{ type: 'step', node: 'generate_sql', status: 'started' }]

    expect(states(events, true)).toMatchObject({ write: 'failed', run: 'skipped' })
  })

  it('an event from a newer API is ignored, not a crash', () => {
    const newer = { type: 'something_new' } as unknown as TraceEvent

    expect(states([newer])).toMatchObject({ write: 'waiting' })
  })
})

describe('formatDuration', () => {
  it.each([
    [56, '56 ms'],
    [999, '999 ms'],
    [1000, '1.0 s'],
    [1439, '1.4 s'],
  ])('%i ms is "%s"', (milliseconds, text) => {
    expect(formatDuration(milliseconds)).toBe(text)
  })
})

describe('repairAnnouncement', () => {
  it('says nothing without a repair', () => {
    expect(repairAnnouncement(railFrom(answerStream, { ended: false }))).toBe('')
  })

  it('says which try failed once the repair starts', () => {
    const failed = until(repairStream, (event) => event.type === 'attempt_failed')
    expect(repairAnnouncement(railFrom(failed, { ended: false }))).toBe('')

    const repairing = until(repairStream, isStep('repair_sql', 'started'))
    expect(repairAnnouncement(railFrom(repairing, { ended: false }))).toBe(
      'SQL try 1 failed, repairing.',
    )
  })

  it('words a suspicious result differently', () => {
    const events: TraceEvent[] = [
      { type: 'sql', attempt: 1, sql: 'SELECT 1', explanation: 'x' },
      { type: 'attempt_failed', attempt: 1, kind: 'suspicious', message: 'No rows.' },
      { type: 'step', node: 'repair_sql', status: 'started' },
    ]

    expect(repairAnnouncement(railFrom(events, { ended: false }))).toBe(
      'The result of SQL try 1 looked wrong, repairing.',
    )
  })
})
