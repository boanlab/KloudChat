import { useCallback, useState, useSyncExternalStore } from 'react'
import { pageCount } from '@/lib/paging'

/** Rows per page of a card or row list: fewer on a narrow screen, where each row is taller. */
export const LIST_PAGE_DESKTOP = 15
export const LIST_PAGE_PHONE = 10
const NARROW = '(max-width: 639px)'

function subscribe(onChange: () => void) {
  const query = window.matchMedia(NARROW)
  query.addEventListener('change', onChange)
  return () => query.removeEventListener('change', onChange)
}

/** The page size for the screen's width now. */
export function useListPageSize(): number {
  return useSyncExternalStore(
    subscribe,
    () => (window.matchMedia(NARROW).matches ? LIST_PAGE_PHONE : LIST_PAGE_DESKTOP),
    () => LIST_PAGE_DESKTOP,
  )
}

/**
 * The one way a long list pages. It remembers the first row on screen, not a page number, so a
 * change of page size (a window resized, a phone turned) keeps that row in view. The page is kept
 * inside 1..pages (deleting the last row of the last page lands on the page before) and goes
 * back to the start when `resetKeys` change (a search, a filter, a sort). `size` fixes the page
 * size of a dense table; left out, it follows the screen width.
 */
export function usePaging(total: number, resetKeys: unknown[] = [], size?: number) {
  const responsive = useListPageSize()
  const rows = size ?? responsive
  const key = JSON.stringify(resetKeys)
  const [state, set] = useState({ key, first: 0 })
  // A changed key goes back to the start while rendering, so no frame shows the old page under it
  // (and coming back to an earlier key does not revive its old page).
  if (state.key !== key) set({ key, first: 0 })
  const first = state.key === key ? state.first : 0
  const pages = pageCount(total, rows)
  const page = Math.min(Math.floor(first / rows) + 1, pages)
  const setPage = useCallback((next: number) => set({ key, first: (next - 1) * rows }), [key, rows])
  return { page, size: rows, pages, offset: (page - 1) * rows, setPage, paged: total > rows }
}

/** The rows of the current page of a list held in memory. */
export function pageOf<T>(items: T[], paging: { offset: number; size: number }): T[] {
  return items.slice(paging.offset, paging.offset + paging.size)
}
