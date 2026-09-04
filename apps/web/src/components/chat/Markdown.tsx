import { Suspense, lazy } from 'react'
import { cn } from '@/lib/utils'
import type { DiagramOwner } from './MarkdownBody'

/** Lazy: react-markdown and KaTeX are most of the initial bundle. */
const Body = lazy(() =>
  import('./MarkdownBody').then((m) => ({ default: m.MarkdownBody })),
)

export function Markdown({
  children,
  className,
  owner,
}: {
  children: string
  className?: string
  owner?: DiagramOwner
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
      <Body className={className} owner={owner}>
        {children}
      </Body>
    </Suspense>
  )
}
