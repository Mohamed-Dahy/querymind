/** The evals lab's views, in sub-menu order. Its own file, so the command palette can list them
 * without pulling in the evals pages. */
export const EVAL_VIEWS = [
  { to: '/evals', label: 'Scoreboard', end: true },
  { to: '/evals/ablations', label: 'What each part adds', end: false },
  { to: '/evals/models', label: 'Models', end: false },
  { to: '/evals/questions', label: 'Questions', end: false },
] as const
