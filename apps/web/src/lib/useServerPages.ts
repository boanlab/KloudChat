import { useCallback, useEffect, useRef, useState } from 'react'
import { usePaging } from '@/lib/usePaging'

/** What a numbered-page endpoint answers. */
export type PageAnswer<T, C> = { items: T[]; total: number; counts?: C }

/**
 * Server-paged list (`offset` + `limit`). Changing `keys` (filter, search, sort) resets to page 1
 * and shows loading, never old rows. Only the latest request's answer is kept.
 */
export function useServerPages<T extends { id: string }, C = undefined>({
  enabled = true,
  keys,
  debounce = false,
  size,
  fetch,
}: {
  enabled?: boolean
  keys: unknown[]
  /** Wait for typing to settle before asking. */
  debounce?: boolean
  size?: number
  fetch: (offset: number, limit: number) => Promise<PageAnswer<T, C>>
}) {
  const [rows, setRows] = useState<T[]>([])
  const [counts, setCounts] = useState<C | undefined>(undefined)
  const [total, setTotal] = useState(0)
  const [failed, setFailed] = useState(false)
  const [heldFor, setHeldFor] = useState<string | null>(null)
  const paging = usePaging(total, keys, size)
  const ask = useRef(fetch)
  ask.current = fetch
  const asked = useRef(0)
  const filterKey = JSON.stringify(keys)

  const load = useCallback(
    async (_quiet = false) => {
      const ticket = ++asked.current
      setFailed(false)
      try {
        const answer = await ask.current(paging.offset, paging.size)
        if (ticket !== asked.current) return
        setRows(answer.items)
        setCounts(answer.counts)
        setTotal(answer.total)
        setHeldFor(filterKey)
      } catch {
        if (ticket === asked.current) setFailed(true)
      }
    },
    [paging.offset, paging.size, filterKey],
  )

  useEffect(() => {
    if (!enabled) return
    const timer = setTimeout(() => void load(), debounce ? 250 : 0)
    return () => clearTimeout(timer)
  }, [enabled, load, debounce])

  // Turning a page keeps the old rows up until the new ones arrive; another filter shows loading.
  const fresh = heldFor === filterKey
  return {
    rows: fresh ? rows : ([] as T[]),
    loading: !fresh && !failed,
    failed,
    total,
    counts,
    paging,
    load,
    setRows,
    setCounts,
    setTotal,
  }
}
