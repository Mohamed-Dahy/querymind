import { describe, expect, it } from 'vitest'

import type { TraceEvent } from '@/api/stream'
import type { ConversationDetail } from '@/api/conversations'
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

describe('saved conversations', () => {
  const SAVED: ConversationDetail = {
    id: 'conv-9',
    title: 'Sales',
    pinned: false,
    created_at: '2026-11-01T10:00:00Z',
    updated_at: '2026-11-01T10:00:00Z',
    turns: 2,
    saved_turns: [
      {
        n: 1,
        created_at: '2026-11-01T10:00:00Z',
        reply: { ...answer, rows: [] },
        trace: repairStream,
      },
      { n: 2, created_at: '2026-11-01T10:05:00Z', reply: refusal, trace: [] },
    ],
  }

  it('fills an empty conversation with saved cells', () => {
    const store = canvasReducer(EMPTY_STORE, { type: 'loaded', conversation: SAVED })
    const { id, cells } = conversationAt(store, 'conv-9')

    expect(id).toBe('conv-9')
    expect(cells.map((cell) => cell.status)).toEqual(['done', 'done'])
    expect(cells[0]?.question).toBe(answer.question)
    expect(cells[0]?.saved).toEqual({ n: 1, createdAt: '2026-11-01T10:00:00Z' })
    expect(cells[0]?.trace).toEqual(repairStream)
    expect(cells[1]).not.toHaveProperty('trace') // it didn't stream: no rail
  })

  it('never replaces live cells', () => {
    const live: CanvasStore = canvasReducer(EMPTY_STORE, {
      key: 'conv-9',
      type: 'asked',
      cellId: 'c1',
      question: 'Asked while it was loading',
    })

    expect(canvasReducer(live, { type: 'loaded', conversation: SAVED })).toBe(live)
  })

  it('a re-run puts fresh rows into the answer, and says when they were read', () => {
    const store = canvasReducer(EMPTY_STORE, { type: 'loaded', conversation: SAVED })
    const next = canvasReducer(store, {
      key: 'conv-9',
      type: 'rerun',
      cellId: 'saved-conv-9-1',
      result: {
        columns: ['a'],
        rows: [[1], [2], [3]],
        row_count: 3,
        truncated: false,
        ran_at: '2026-11-03T09:00:00Z',
      },
    })
    const [cell] = conversationAt(next, 'conv-9').cells

    expect(cell?.reply).toMatchObject({ columns: ['a'], rows: [[1], [2], [3]], row_count: 3 })
    expect(cell?.reply).toMatchObject({ answer: answer.answer }) // the words stay as they were
    expect(cell?.saved).toEqual({
      n: 1,
      createdAt: '2026-11-01T10:00:00Z',
      freshAt: '2026-11-03T09:00:00Z',
    })
  })

  it('a re-run does nothing to a cell that was not saved', () => {
    const state = conversationReducer(asked(EMPTY), {
      type: 'replied',
      cellId: 'c1',
      reply: answer,
    })

    const next = conversationReducer(state, {
      type: 'rerun',
      cellId: 'c1',
      result: { columns: [], rows: [], row_count: 0, truncated: false, ran_at: 'x' },
    })

    expect(next.cells[0]?.reply).toEqual(answer)
  })

  it('a dropped conversation is gone, and so is `/` pointing at it', () => {
    const store = canvasReducer(
      canvasReducer(EMPTY_STORE, {
        key: NEW_CONVERSATION,
        type: 'asked',
        cellId: 'c1',
        question: 'q',
      }),
      {
        key: NEW_CONVERSATION,
        type: 'replied',
        cellId: 'c1',
        reply: { ...answer, conversation_id: 'conv-1' },
      },
    )
    expect(store.promotedTo).toBe('conv-1')

    const next = canvasReducer(store, { type: 'dropped', conversationId: 'conv-1' })

    expect(next.promotedTo).toBeNull()
    expect(conversationAt(next, NEW_CONVERSATION).cells).toEqual([])
  })
})
