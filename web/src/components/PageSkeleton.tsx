import { Skeleton } from '@/components/ui/skeleton'

/**
 * What a page shows while its own code downloads (the lazy pages): the shape of a page, never a
 * blank screen. Screen readers hear "Loading the page…" once.
 */
export function PageSkeleton() {
  return (
    <div className="mx-auto max-w-5xl px-4 py-10">
      <p role="status" className="sr-only">
        Loading the page…
      </p>
      <div aria-hidden="true">
        <Skeleton className="h-8 w-64 max-w-full motion-reduce:animate-none" />
        <Skeleton className="mt-3 h-4 w-full max-w-md motion-reduce:animate-none" />
        <Skeleton className="mt-8 h-64 w-full motion-reduce:animate-none" />
      </div>
    </div>
  )
}
