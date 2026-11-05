import { EvalsFrame } from '@/evals/EvalsFrame'
import { Scoreboard } from '@/evals/Scoreboard'

/** The evals lab: how well the agent really does, with where each number came from. */
export default function EvalsPage() {
  return (
    <EvalsFrame
      title="Evals lab"
      intro="How well the agent does on questions with known answers: scores, costs, and what each number counted."
    >
      {(data) => <Scoreboard data={data} />}
    </EvalsFrame>
  )
}
