import { useT } from '@/lib/useT'
import { cn } from '@/lib/utils'

/**
 * The shape of a list while it loads: grey rows (or cards) where the content will be, so the
 * page does not jump when it arrives. Says 「불러오는 중…」 to assistive tech, as LoadingState does.
 */
export function ListSkeleton({
  rows = 6,
  variant = 'rows',
  label,
  className,
}: {
  rows?: number
  variant?: 'rows' | 'cards' | 'table'
  label?: string
  className?: string
}) {
  const t = useT()
  const bar = 'rounded bg-line/60 motion-safe:animate-pulse'
  return (
    <div role="status" aria-busy="true" data-testid="list-skeleton" className={cn('py-1', className)}>
      <span className="sr-only">{label ?? t('불러오는 중…')}</span>
      {variant === 'cards' ? (
        <div className="grid gap-3 sm:grid-cols-2" aria-hidden>
          {Array.from({ length: rows }, (_, i) => (
            <div key={i} className="rounded-card border border-line bg-panel p-4">
              <div className="flex items-start gap-3">
                <div className={cn(bar, 'size-9 shrink-0 rounded-card')} />
                <div className="min-w-0 flex-1 space-y-2">
                  <div className={cn(bar, 'h-4 w-2/3')} />
                  <div className={cn(bar, 'h-3 w-full')} />
                  <div className={cn(bar, 'h-3 w-1/3')} />
                </div>
              </div>
            </div>
          ))}
        </div>
      ) : (
        <div className="overflow-hidden rounded-card border border-line bg-panel" aria-hidden>
          {Array.from({ length: rows }, (_, i) => (
            <div key={i} className="flex items-center gap-3 border-b border-line px-3 py-3 last:border-b-0">
              {variant === 'table' ? (
                <>
                  <div className={cn(bar, 'h-3.5 w-1/4')} />
                  <div className={cn(bar, 'h-3.5 w-1/6')} />
                  <div className={cn(bar, 'h-3.5 flex-1')} />
                  <div className={cn(bar, 'h-3.5 w-16')} />
                </>
              ) : (
                <>
                  <div className={cn(bar, 'size-4 shrink-0')} />
                  <div className="min-w-0 flex-1 space-y-1.5">
                    <div className={cn(bar, 'h-3.5')} style={{ width: `${55 + ((i * 17) % 35)}%` }} />
                    <div className={cn(bar, 'h-3 w-1/3')} />
                  </div>
                  <div className={cn(bar, 'h-3 w-12')} />
                </>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
