/** Questions kept to ask again: list, save, remove. Only their words are stored. */
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { ApiError, api, call } from './client'
import { errorTitle } from './errors'
import type { components } from './schema'

export type SavedQuestion = components['schemas']['SavedQuestion']

const SAVED_QUESTIONS_KEY = ['saved-questions'] as const
const TIMEOUT_MS = 10_000

function listSavedQuestions(signal?: AbortSignal): Promise<SavedQuestion[]> {
  return call(TIMEOUT_MS, signal, (s) => api.GET('/saved-questions', { signal: s })).then(
    ({ questions }) => questions,
  )
}

function saveQuestion(question: string): Promise<SavedQuestion> {
  return call(TIMEOUT_MS, undefined, (s) =>
    api.POST('/saved-questions', { body: { question }, signal: s }),
  )
}

async function removeSavedQuestion(id: string): Promise<void> {
  await call(TIMEOUT_MS, undefined, (s) =>
    api.DELETE('/saved-questions/{saved_id}', { params: { path: { saved_id: id } }, signal: s }),
  )
}

function showFailure(error: unknown) {
  toast.error(errorTitle(error instanceof ApiError ? error.code : 'unexpected'))
}

/**
 * The saved questions, shared by the rail and every answer's button. `available` is false while they
 * load and when they can't be read, for example with history off.
 */
export function useSavedQuestions() {
  const queryClient = useQueryClient()
  const saved = useQuery({
    queryKey: SAVED_QUESTIONS_KEY,
    queryFn: ({ signal }) => listSavedQuestions(signal),
  })
  const refresh = () => queryClient.invalidateQueries({ queryKey: SAVED_QUESTIONS_KEY })

  const add = useMutation({
    mutationFn: saveQuestion,
    onSuccess: refresh,
    onError: showFailure,
  })
  const drop = useMutation({
    mutationFn: removeSavedQuestion,
    onSuccess: refresh,
    onError: showFailure,
  })

  return {
    available: saved.isSuccess,
    questions: saved.data ?? [],
    pending: add.isPending || drop.isPending,
    add: add.mutate,
    remove: drop.mutate,
  }
}
