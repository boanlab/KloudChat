import { useEffect, useState } from 'react'
import { currentLang, translate } from '@/lib/i18n'

/** How many rows a workspace list renders before asking. */
export const PAGE_SIZE = 40

/**
 * Caps how much is rendered, not the list itself.
 *
 * Filters and counts still see everything; only the rows that are off-screen
 * are not built yet.
 */
export function usePaged<T>(items: T[], deps: unknown[] = []) {
  const [shown, setShown] = useState(PAGE_SIZE)

  // A new filter starts a new list, and carrying the old offset into it would
  // show a hundred rows of something the user just narrowed down.
  useEffect(() => {
    setShown(PAGE_SIZE)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps)

  return {
    visible: items.slice(0, shown),
    hidden: Math.max(0, items.length - shown),
    more: () => setShown((n) => n + PAGE_SIZE),
  }
}

export function ShowMore({ hidden, onMore }: { hidden: number; onMore: () => void }) {
  if (hidden <= 0) return null
  return (
    <button
      onClick={onMore}
      className="mt-2 w-full rounded-lg border border-line py-2 text-base text-muted transition-colors hover:bg-elevated hover:text-fg"
    >
      {translate(currentLang(), '{n}개 더 보기').replace('{n}', hidden.toLocaleString())}
    </button>
  )
}
