import { Suspense, lazy } from 'react'
import { cn } from '@/lib/utils'

/**
 * The renderer, fetched the first time something needs rendering.
 *
 * `react-markdown` and KaTeX together are most of the first megabyte this app
 * asks for, and neither is needed to sign in, read the home screen or open
 * settings — the three things somebody does before they have any prose to
 * render. Split out, they arrive with the first answer instead of with the
 * login form.
 */
const Body = lazy(() =>
  import('./MarkdownBody').then((m) => ({ default: m.MarkdownBody })),
)

export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <Suspense
      fallback={
        /* The text itself, unstyled, rather than a spinner: it is already here,
           and a paragraph that appears twice — once plain, once set — reads
           better than one that is withheld until the fonts arrive. */
        <div className={cn('text-md leading-[1.7] break-words whitespace-pre-wrap', className)}>
          {children}
        </div>
      }
    >
      <Body className={className}>{children}</Body>
    </Suspense>
  )
}
