import { useCallback, useState } from 'react'
import { pageCount } from '@/lib/paging'

/** Rows per page on every long list unless a screen says otherwise. */
export const LIST_PAGE_SIZE = 25

/**
 * The one way a long list pages: the page number, kept inside 1..pages (so deleting the last
 * row of the last page lands on the page before) and sent back to 1 when `resetKeys` change
 * (a search, a filter, a sort).
 */
export function usePaging(total: number, resetKeys: unknown[] = [], size = LIST_PAGE_SIZE) {
  const key = JSON.stringify(resetKeys)
  const [state, set] = useState({ key, page: 1 })
  // A changed key goes back to page 1 while rendering, so no frame shows the old page under it
  // (and coming back to an earlier key does not revive its old page).
  if (state.key !== key) set({ key, page: 1 })
  const wanted = state.key === key ? state.page : 1
  const pages = pageCount(total, size)
  const page = Math.min(Math.max(1, wanted), pages)
  const setPage = useCallback((next: number) => set({ key, page: next }), [key])
  return { page, size, pages, offset: (page - 1) * size, setPage, paged: total > size }
}

/** The rows of the current page of a list held in memory. */
export function pageOf<T>(items: T[], paging: { offset: number; size: number }): T[] {
  return items.slice(paging.offset, paging.offset + paging.size)
}
