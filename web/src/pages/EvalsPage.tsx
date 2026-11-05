import { Skeleton } from '@/components/ui/skeleton'
import { Scoreboard } from '@/evals/Scoreboard'
import { useEvals } from '@/evals/useEvals'
import { LoadError } from '@/explorer/LoadError'

/** The evals lab: how well the agent really does, with where each number came from. */
export default function EvalsPage() {
  const { data, error, isPending, refetch } = useEvals()
  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <h1 className="text-2xl">Evals lab</h1>
      <p className="mt-2 text-fg-muted">
        How well the agent does on questions with known answers: scores, costs, and what each number
        counted.
      </p>

      <div className="mt-6">
        {isPending ? (
          <Skeleton aria-hidden="true" className="h-64 w-full motion-reduce:animate-none" />
        ) : data === undefined ? (
          <LoadError
            error={error}
            onRetry={() => void refetch()}
            message="The saved results can't be read on this server. The rest of the app still works."
          />
        ) : (
          <Scoreboard data={data} />
        )}
      </div>
    </div>
  )
}
