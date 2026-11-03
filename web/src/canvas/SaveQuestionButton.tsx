import { Bookmark, BookmarkCheck } from 'lucide-react'

import { useSavedQuestions } from '@/api/savedQuestions'
import { Button } from '@/components/ui/button'

type SaveQuestionButtonProps = {
  cellId: string
  /** The words to keep. They must make sense in a new conversation, where it will be asked. */
  question: string
}

/** Keeps a question to ask again, or stops keeping it. Absent while history is off. */
export function SaveQuestionButton({ cellId, question }: SaveQuestionButtonProps) {
  const { available, questions, pending, add, remove } = useSavedQuestions()
  if (!available) return null

  const saved = questions.find((one) => one.question === question)
  const Icon = saved === undefined ? Bookmark : BookmarkCheck

  return (
    <Button
      type="button"
      variant="outline"
      aria-pressed={saved !== undefined}
      aria-describedby={`${cellId}-question`}
      // Not `disabled`: that would drop the keyboard focus from the button in the middle of a click.
      onClick={() => {
        if (pending) return
        if (saved === undefined) add(question)
        else remove(saved.id)
      }}
    >
      <Icon aria-hidden="true" />
      {saved === undefined ? 'Save question' : 'Saved'}
    </Button>
  )
}
