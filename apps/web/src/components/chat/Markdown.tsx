import { Suspense, lazy } from 'react'
import { cn } from '@/lib/utils'
import type { VideoRef } from '@/lib/media'
import type { DiagramOwner } from './MarkdownBody'

/** Lazy: react-markdown and KaTeX are most of the initial bundle. */
const Body = lazy(() =>
  import('./MarkdownBody').then((m) => ({ default: m.MarkdownBody })),
)

export function Markdown({
  children,
  className,
  owner,
  videos,
}: {
  children: string
  className?: string
  owner?: DiagramOwner
  videos?: VideoRef[]
}) {
  return (
    <Suspense
      fallback={
        /* Plain text while the renderer loads. */
        <div className={cn('text-md leading-[1.7] break-words whitespace-pre-wrap', className)}>
          {children}
        </div>
      }
    >
      <Body className={className} owner={owner} videos={videos}>
        {children}
      </Body>
    </Suspense>
  )
}
