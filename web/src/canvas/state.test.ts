import { describe, expect, it } from 'vitest'

import type { TraceEvent } from '@/api/stream'
import { answer, clarification, followup, refusal, repairStream } from '@/mocks/fixtures'

import {
  canvasReducer,
  conversationAt,
  conversationReducer,
  EMPTY_STORE,
  NEW_CONVERSATION,
  type CanvasAction,
  type CanvasStore,
  type Conversation,
} from './state'

const EMPTY: Conversation = { id: null, cells: [] }
const ERROR = { code: 'db_unavailable', message: 'Could not connect.', status: 503 }

function asked(state: Conversation, cellId = 'c1', question = 'Sales per category?') {
  return conversationReducer(state, { type: 'asked', cellId, question })
}

describe('conversationReducer', () => {
  it('adds a running cell when a question is asked, without changing the old state', () => {
    const next = asked(EMPTY)

    expect(next.cells).toEqual([{ id: 'c1', question: 'Sales per category?', status: 'running' }])
    expect(EMPTY.cells).toEqual([])
  })

  it('keeps the parent of a follow-up', () => {
    const next = conversationReducer(EMPTY, {
      type: 'asked',
      cellId: 'c2',
      question: 'Only 1997',
      parentId: 'c1',
    })

    expect(next.cells[0]?.parentId).toBe('c1')
  })

  it.each([
    ['answer', answer],
    ['clarification', clarification],
    ['refusal', refusal],
  ] as const)('stores a %s reply, and takes the conversation id from it', (_kind, reply) => {
    const next = conversationReducer(asked(EMPTY), { type: 'replied', cellId: 'c1', reply })

    expect(next.cells[0]).toEqual({
      id: 'c1',
      question: 'Sales per category?',
      status: 'done',
      reply,
    })
    expect(next.id).toBe(reply.conversation_id)
  })

  it('keeps its id on later replies (a follow-up continues the same conversation)', () => {
    const named: Conversation = { id: 'conv-1', cells: [] }
    const next = conversationReducer(asked(named), {
      type: 'replied',
      cellId: 'c1',
      reply: { ...followup, conversation_id: 'something-else' },
    })

    expect(next.id).toBe('conv-1')
  })

  it('collects the events while a cell runs, and keeps them after it ends', () => {
    const [first, second] = repairStream as [TraceEvent, TraceEvent]
    const traced = [first, second].reduce(
      (state, event) => conversationReducer(state, { type: 'traced', cellId: 'c1', event }),
      asked(EMPTY),
    )
    expect(traced.cells[0]?.trace).toEqual([first, second])

    const done = conversationReducer(traced, { type: 'replied', cellId: 'c1', reply: answer })
    expect(done.cells[0]?.trace).toEqual([first, second])

    // The cell has ended: a late event changes nothing.
    expect(conversationReducer(done, { type: 'traced', cellId: 'c1', event: first })).toBe(done)
  })

  it('a failure keeps what happened before it; a retry starts a fresh trace', () => {
    const [first] = repairStream as [TraceEvent]
    const traced = conversationReducer(asked(EMPTY), { type: 'traced', cellId: 'c1', event: first })
    const failed = conversationReducer(traced, { type: 'failed', cellId: 'c1', error: ERROR })
    expect(failed.cells[0]?.trace).toEqual([first])

    const retried = conversationReducer(failed, { type: 'retried', cellId: 'c1' })
    expect(retried.cells[0]?.trace).toBeUndefined()
  })

  it('marks a failure, and a retry runs the same cell again in its place', () => {
    const failed = conversationReducer(asked(asked(EMPTY), 'c2', 'second'), {
      type: 'failed',
      cellId: 'c1',
      error: ERROR,
    })
    expect(failed.cells[0]).toMatchObject({ status: 'failed', error: ERROR })

    const retried = conversationReducer(failed, { type: 'retried', cellId: 'c1' })
    expect(retried.cells.map((cell) => cell.id)).toEqual(['c1', 'c2'])
    expect(retried.cells[0]).toEqual({
      id: 'c1',
      question: 'Sales per category?',
      status: 'running',
    })
  })

  it('ignores actions a cell cannot take, returning the same state object', () => {
    const running = asked(EMPTY)
    const done = conversationReducer(running, { type: 'replied', cellId: 'c1', reply: answer })

    // An unknown cell, a second reply, a retry of a cell that didn't fail.
    expect(conversationReducer(running, { type: 'replied', cellId: 'nope', reply: answer })).toBe(
      running,
    )
    expect(conversationReducer(done, { type: 'failed', cellId: 'c1', error: ERROR })).toBe(done)
    expect(conversationReducer(done, { type: 'retried', cellId: 'c1' })).toBe(done)
  })
})

describe('canvasReducer', () => {
  function run(...actions: CanvasAction[]): CanvasStore {
    return actions.reduce(canvasReducer, EMPTY_STORE)
  }
  const askNew: CanvasAction = {
    key: NEW_CONVERSATION,
    type: 'asked',
    cellId: 'c1',
    question: 'q1',
  }
  const replyNew: CanvasAction = {
    key: NEW_CONVERSATION,
    type: 'replied',
    cellId: 'c1',
    reply: answer,
  }
  const id = answer.conversation_id

  it('files the new conversation under its id after the first reply, and `/` still shows it', () => {
    const store = run(askNew, replyNew)

    expect(Object.keys(store.conversations)).toEqual([id])
    expect(store.promotedTo).toBe(id)
    // `/` (still on screen until the URL changes) and `/c/<id>` show the same conversation.
    expect(conversationAt(store, NEW_CONVERSATION)).toBe(conversationAt(store, id))
    expect(conversationAt(store, id).cells).toHaveLength(1)
  })

  it('sends a follow-up asked at `/` to the promoted conversation', () => {
    const store = run(askNew, replyNew, {
      key: NEW_CONVERSATION,
      type: 'asked',
      cellId: 'c2',
      question: 'Only 1997',
    })

    expect(conversationAt(store, id).cells.map((cell) => cell.id)).toEqual(['c1', 'c2'])
    expect(store.conversations[NEW_CONVERSATION]).toBeUndefined()
  })

  it('keeps the new conversation unnamed while its first question fails', () => {
    const store = run(askNew, { key: NEW_CONVERSATION, type: 'failed', cellId: 'c1', error: ERROR })

    expect(store.promotedTo).toBeNull()
    expect(conversationAt(store, NEW_CONVERSATION)).toMatchObject({ id: null })
  })

  it('reset gives a fresh `/`, and the old conversation stays at its id', () => {
    const store = run(askNew, replyNew, { type: 'reset' })

    expect(conversationAt(store, NEW_CONVERSATION)).toEqual({ id: null, cells: [] })
    expect(conversationAt(store, id).cells).toHaveLength(1)
  })

  it('an unknown /c/<id> is an empty conversation that already has that id', () => {
    expect(conversationAt(EMPTY_STORE, 'abc')).toEqual({ id: 'abc', cells: [] })
  })

  it('returns the same store when nothing changed', () => {
    const store = run(askNew)
    expect(canvasReducer(store, { key: 'other', type: 'retried', cellId: 'c1' })).toBe(store)
  })
})
