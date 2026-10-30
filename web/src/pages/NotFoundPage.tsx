import { Link, useLocation } from 'react-router'

/** Any unknown path inside the app (a mistyped or old link). */
export function NotFoundPage() {
  const { pathname } = useLocation()

  return (
    <div className="mx-auto max-w-3xl px-4 py-12">
      <h1 className="text-2xl">Page not found</h1>
      <p className="mt-2 text-fg-muted">
        There is nothing at <code className="text-fg">{pathname}</code>.
      </p>
      <p className="mt-6">
        <Link to="/" className="text-info underline underline-offset-4">
          Back to the canvas
        </Link>
      </p>
    </div>
  )
}
