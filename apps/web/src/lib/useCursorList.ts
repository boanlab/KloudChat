import { useCallback, useEffect, useRef, useState } from 'react'
import type { CursorPage } from './api'

/**
 * A server-paged list: the first page on `deps`, more on demand. `failed` is the first page's
 * failure, `moreFailed` a later one's; both are retried by calling again.
 */
export function useCursorList<T extends { id: string }>(
  fetchPage: (cursor: string | null) => Promise<CursorPage<T>>,
  deps: unknown[],
  enabled = true,
) {
  const [rows, setRows] = useState<T[] | null>(null)
  const [total, setTotal] = useState(0)
  const [next, setNext] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)
  const [loadingMore, setLoadingMore] = useState(false)
  const [moreFailed, setMoreFailed] = useState(false)
  // Only the latest request's answer is shown.
  const asked = useRef(0)
  const fetchRef = useRef(fetchPage)
  fetchRef.current = fetchPage

  const load = useCallback(async () => {
    const ticket = ++asked.current
    setRows(null)
    setFailed(false)
    setMoreFailed(false)
    try {
      const page = await fetchRef.current(null)
      if (ticket !== asked.current) return
      setRows(page.items)
      setTotal(page.total)
      setNext(page.next)
    } catch {
      if (ticket === asked.current) setFailed(true)
    }
  }, [])

  const loadMore = useCallback(async () => {
    if (!next || loadingMore) return
    const ticket = asked.current
    setLoadingMore(true)
    setMoreFailed(false)
    try {
      const page = await fetchRef.current(next)
      if (ticket !== asked.current) return
      setRows((held) => [...(held ?? []), ...page.items.filter((row) => !held?.some((h) => h.id === row.id))])
      setTotal(page.total)
      setNext(page.next)
    } catch {
      if (ticket === asked.current) setMoreFailed(true)
    } finally {
      setLoadingMore(false)
    }
  }, [next, loadingMore])

  useEffect(() => {
    if (enabled) void load()
    // The caller's deps decide when page one is asked again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [enabled, load, ...deps])

  return { rows, setRows, total, hasMore: next !== null, failed, loadingMore, moreFailed, load, loadMore }
}
