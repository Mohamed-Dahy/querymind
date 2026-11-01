import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, it, vi } from 'vitest'

import type { TraceEvent } from '@/api/stream'
import { answerStream, repairStream } from '@/mocks/fixtures'

import { AgentRail } from './AgentRail'
import type { CellStatus } from './state'

function renderRail(
  trace: readonly TraceEvent[],
  { status = 'done', stopped = false }: { status?: CellStatus; stopped?: boolean } = {},
) {
  const onStop = vi.fn<() => void>()
  render(
    <>
      <h2 id="question">The question</h2>
      <AgentRail
        trace={trace}
        status={status}
        stopped={stopped}
        onStop={onStop}
        describedBy="question"
      />
    </>,
  )
  return { onStop, user: userEvent.setup() }
}

function step(name: string) {
  const items = within(screen.getByRole('list', { name: 'Agent steps' })).getAllByRole('listitem')
  const item = items.find((one) => one.textContent?.startsWith(name))
  if (item === undefined) throw new Error(`no step ${name}`)
  return item
}

/** The recorded events up to and including the first that matches. */
function until(events: readonly TraceEvent[], match: (event: TraceEvent) => boolean) {
  return events.slice(0, events.findIndex(match) + 1)
}

describe('AgentRail', () => {
  it('shows all seven steps, in order', () => {
    renderRail(answerStream)

    const items = within(screen.getByRole('list', { name: 'Agent steps' })).getAllByRole('listitem')
    expect(items.map((item) => item.querySelector('span span')?.textContent)).toEqual([
      'Retrieve',
      'Write SQL',
      'Validate',
      'Run',
      'Check',
      'Repair',
      'Summarize',
    ])
  })

  it('a finished clean run: each step says done and its time; repair was skipped', () => {
    renderRail(answerStream)

    expect(step('Write SQL')).toHaveTextContent(/^Write SQLdone, [\d.]+ (ms|s)$/)
    expect(step('Repair')).toHaveTextContent('Repairskipped')
    expect(screen.queryByRole('list', { name: 'SQL tries that failed' })).not.toBeInTheDocument()
    expect(screen.queryByText(/tries$/)).not.toBeInTheDocument()
  })

  it('before the first event, everything waits and Stop is there', () => {
    renderRail([], { status: 'running' })

    expect(step('Retrieve')).toHaveTextContent('Retrievewaiting')
    expect(screen.getByRole('button', { name: 'Stop' })).toHaveAccessibleDescription('The question')
  })

  it('marks the step at work for screen readers, and Stop calls back', async () => {
    const running = until(
      answerStream,
      (event) => event.type === 'step' && event.node === 'generate_sql',
    )
    const { onStop, user } = renderRail(running, { status: 'running' })

    expect(step('Write SQL')).toHaveAttribute('aria-current', 'step')
    expect(step('Write SQL')).toHaveTextContent('Write SQLrunning')
    expect(step('Retrieve')).not.toHaveAttribute('aria-current')

    await user.click(screen.getByRole('button', { name: 'Stop' }))
    expect(onStop).toHaveBeenCalledOnce()
  })

  it('no Stop once the run has ended', () => {
    renderRail(answerStream)

    expect(screen.queryByRole('button', { name: 'Stop' })).not.toBeInTheDocument()
  })

  it('a repair: the loop, the number of tries, and why the first one failed', () => {
    renderRail(repairStream)

    expect(screen.getByText('2 tries')).toBeInTheDocument()
    expect(step('Repair')).toHaveTextContent(/^Repairdone, /)
    const failures = screen.getByRole('list', { name: 'SQL tries that failed' })
    expect(within(failures).getByRole('listitem')).toHaveTextContent(
      /^Try 1 failed: The database could not run the query: .*GROUP BY/,
    )
  })

  it('shows the failure live, while the repair is still running', () => {
    const repairing = until(
      repairStream,
      (event) => event.type === 'step' && event.node === 'repair_sql',
    )
    renderRail(repairing, { status: 'running' })

    expect(step('Run')).toHaveTextContent(/^Runfailed/)
    expect(step('Repair')).toHaveTextContent('Repairrunning')
    expect(screen.getByRole('list', { name: 'SQL tries that failed' })).toBeInTheDocument()
  })

  it('a result that looked wrong has its own words', () => {
    const events: TraceEvent[] = [
      { type: 'sql', attempt: 1, sql: 'SELECT 1', explanation: 'x' },
      { type: 'attempt_failed', attempt: 1, kind: 'suspicious', message: 'No rows came back.' },
    ]
    renderRail(events, { status: 'running' })

    expect(screen.getByText('Try 1 looked wrong: No rows came back.')).toBeInTheDocument()
  })

  it('a stopped run says stopped where it was cut off, not failed', () => {
    const running = until(
      answerStream,
      (event) => event.type === 'step' && event.node === 'generate_sql',
    )
    renderRail(running, { status: 'failed', stopped: true })

    expect(step('Write SQL')).toHaveTextContent('Write SQLstopped')
    expect(step('Run')).toHaveTextContent('Runskipped')
  })

  it('a run that failed says failed where it was cut off', () => {
    const running = until(
      answerStream,
      (event) => event.type === 'step' && event.node === 'generate_sql',
    )
    renderRail(running, { status: 'failed' })

    expect(step('Write SQL')).toHaveTextContent('Write SQLfailed')
  })

  it('spins only while running, and not under reduced motion', () => {
    const running = until(
      answerStream,
      (event) => event.type === 'step' && event.node === 'generate_sql',
    )
    renderRail(running, { status: 'running' })

    const spinning = step('Write SQL').querySelector('svg')
    expect(spinning).toHaveClass('animate-spin', 'motion-reduce:animate-none')
    expect(step('Retrieve').querySelector('svg')).not.toHaveClass('animate-spin')
  })
})
