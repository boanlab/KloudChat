import { useEffect, useState } from 'react'
import { currentLang, translate } from '@/lib/i18n'

const PAGE_SIZE = 40

/** Caps how many `items` are rendered; `deps` changing resets the page. */
export function usePaged<T>(items: T[], deps: unknown[] = []) {
  const [shown, setShown] = useState(PAGE_SIZE)

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
      className="mt-2 w-full rounded-control border border-line py-2 text-base text-muted transition-colors hover:bg-elevated hover:text-fg"
    >
      {translate(currentLang(), '{n}개 더 보기').replace('{n}', hidden.toLocaleString())}
    </button>
  )
}
