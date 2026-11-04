/**
 * The fake API's saved conversations. In `localStorage`, because MSW's handlers live in the page
 * and would otherwise forget everything on a reload, which is the very thing to test here.
 */
import type { AskReply } from '@/api/client'
import type {
  ConversationDetail,
  ConversationList,
  ConversationSummary,
  ConversationUpdate,
  RerunResult,
} from '@/api/conversations'
import type { TableQuestionList } from '@/api/explorer'
import type { SavedQuestion } from '@/api/savedQuestions'
import type { TraceEvent } from '@/api/stream'

const KEY = 'querymind-mock-conversations'
const SAVED_KEY = 'querymind-mock-saved-questions'
const TITLE_LENGTH = 80

type StoredTurn = {
  n: number
  created_at: string
  reply: AskReply
  trace: TraceEvent[]
  /** Only here so a re-run has rows to give back. The real API never stores them. */
  rows: RerunResult['rows']
}
type Stored = Omit<ConversationSummary, 'turns'> & { stored_turns: StoredTurn[] }

function load(): Stored[] {
  return JSON.parse(localStorage.getItem(KEY) ?? '[]') as Stored[]
}

function save(conversations: Stored[]) {
  localStorage.setItem(KEY, JSON.stringify(conversations))
}

function summary({ stored_turns: turns, ...conversation }: Stored): ConversationSummary {
  return { ...conversation, turns: turns.length }
}

export function recordTurn(reply: AskReply, trace: readonly TraceEvent[]) {
  const conversations = load()
  const now = new Date().toISOString()
  let conversation = conversations.find(({ id }) => id === reply.conversation_id)
  if (conversation === undefined) {
    conversation = {
      id: reply.conversation_id,
      title: reply.question.slice(0, TITLE_LENGTH),
      pinned: false,
      created_at: now,
      updated_at: now,
      stored_turns: [],
    }
    conversations.push(conversation)
  }
  const rows = reply.kind === 'answer' ? reply.rows : []
  conversation.updated_at = now
  conversation.stored_turns.push({
    n: conversation.stored_turns.length + 1,
    created_at: now,
    reply: reply.kind === 'answer' ? { ...reply, rows: [] } : reply,
    trace: [...trace],
    rows,
  })
  save(conversations)
}

export function listStored(q: string | null, limit: number): ConversationList {
  const needle = q?.toLowerCase()
  const found = load()
    .filter(
      ({ title, stored_turns: turns }) =>
        !needle ||
        title.toLowerCase().includes(needle) ||
        turns.some(({ reply }) => reply.question.toLowerCase().includes(needle)),
    )
    .sort((a, b) => Number(b.pinned) - Number(a.pinned) || b.updated_at.localeCompare(a.updated_at))
  return { conversations: found.slice(0, limit).map(summary), has_more: found.length > limit }
}

export function getStored(id: string): ConversationDetail | undefined {
  const found = load().find((conversation) => conversation.id === id)
  if (found === undefined) return undefined
  return {
    ...summary(found),
    saved_turns: found.stored_turns.map(({ n, created_at, reply, trace }) => ({
      n,
      created_at,
      reply,
      trace,
    })),
  }
}

export function updateStored(id: string, change: ConversationUpdate) {
  const conversations = load()
  const found = conversations.find((conversation) => conversation.id === id)
  if (found === undefined) return undefined
  if (change.title != null) found.title = change.title
  if (change.pinned != null) found.pinned = change.pinned
  save(conversations)
  return summary(found)
}

export function deleteStored(id: string): boolean {
  const conversations = load()
  const kept = conversations.filter((conversation) => conversation.id !== id)
  save(kept)
  return kept.length < conversations.length
}

export function rerunStored(id: string, n: number): RerunResult | undefined {
  const turn = load()
    .find((conversation) => conversation.id === id)
    ?.stored_turns.find((one) => one.n === n)
  if (turn === undefined || turn.reply.kind !== 'answer') return undefined
  return {
    columns: turn.reply.columns,
    rows: turn.rows,
    row_count: turn.rows.length,
    truncated: false,
    ran_at: new Date().toISOString(),
  }
}

/** Like the real store: the newest saved questions whose SQL read this table. */
export function questionsUsing(table: string, limit = 5): TableQuestionList {
  const questions = load()
    .flatMap(({ id, stored_turns: turns }) =>
      turns
        .filter(({ reply }) => reply.kind === 'answer' && reply.tables_used.includes(table))
        .map(({ n, created_at, reply }) => ({
          conversation_id: id,
          n,
          question: reply.question,
          created_at,
        })),
    )
    .sort((a, b) => b.created_at.localeCompare(a.created_at))
  return { questions: questions.slice(0, limit) }
}

function loadSaved(): SavedQuestion[] {
  return JSON.parse(localStorage.getItem(SAVED_KEY) ?? '[]') as SavedQuestion[]
}

export function listSavedQuestions(): SavedQuestion[] {
  return loadSaved()
}

/** Like the real table: the same words stay one row. */
export function addSavedQuestion(question: string): SavedQuestion {
  const saved = loadSaved()
  const found = saved.find((one) => one.question === question)
  if (found !== undefined) return found
  const added = { id: crypto.randomUUID(), question, created_at: new Date().toISOString() }
  localStorage.setItem(SAVED_KEY, JSON.stringify([added, ...saved]))
  return added
}

export function removeSavedQuestion(id: string) {
  const kept = loadSaved().filter((one) => one.id !== id)
  localStorage.setItem(SAVED_KEY, JSON.stringify(kept))
}
