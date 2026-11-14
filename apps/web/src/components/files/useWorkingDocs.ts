import { useCallback, useEffect, useRef, useState } from 'react'
import { workingDocsApi, type WorkingDocRow, type WorkingKind, type WorkingSort } from '@/lib/api'
import { FILE_PAGE } from './fileBox'

/** The kinds 「작업 문서」 lists, in the order its format chips show them. */
export const WORKING_KINDS: { kind: WorkingKind; label: string }[] = [
  { kind: 'hwp', label: '한글' },
  { kind: 'docx', label: 'Word' },
  { kind: 'pptx', label: 'PowerPoint' },
  { kind: 'report', label: '웹 보고서' },
  { kind: 'deck', label: '웹 슬라이드' },
]

type Counts = Partial<Record<WorkingKind, number>>

/**
 * The account's working documents, a page at a time: one row per document, filtered by kind,
 * searched by title and sorted on the server, so every filter reaches every document.
 */
export function useWorkingDocs(enabled: boolean, kind: WorkingKind | null, query: string, sort: WorkingSort) {
  const [rows, setRows] = useState<WorkingDocRow[]>([])
  const [counts, setCounts] = useState<Counts | null>(null)
  const [total, setTotal] = useState(0)
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [failed, setFailed] = useState(false)
  const [moreFailed, setMoreFailed] = useState(false)
  // Which filter the held rows answer; another one shows as loading, not as old rows.
  const [heldFor, setHeldFor] = useState<string | null>(null)
  const asked = useRef(0)
  const q = query.trim()
  const key = `${kind ?? ''}|${q}|${sort}`

  const load = useCallback(async () => {
    const ticket = ++asked.current
    setLoading(true)
    setFailed(false)
    setMoreFailed(false)
    try {
      const page = await workingDocsApi.page({ kind, q, sort, limit: FILE_PAGE })
      if (ticket !== asked.current) return
      setRows(page.items)
      setCounts(page.counts)
      setTotal(page.total)
      setNext(page.next)
      setHeldFor(`${kind ?? ''}|${q}|${sort}`)
    } catch {
      if (ticket === asked.current) setFailed(true)
    } finally {
      if (ticket === asked.current) setLoading(false)
    }
  }, [kind, q, sort])

  const loadMore = useCallback(async () => {
    if (!next || loadingMore) return
    const ticket = asked.current
    setLoadingMore(true)
    setMoreFailed(false)
    try {
      const page = await workingDocsApi.page({ kind, q, sort, cursor: next, limit: FILE_PAGE })
      if (ticket !== asked.current) return
      setRows((held) => [...held, ...page.items.filter((row) => !held.some((h) => h.id === row.id))])
      setCounts(page.counts)
      setTotal(page.total)
      setNext(page.next)
    } catch {
      if (ticket === asked.current) setMoreFailed(true)
    } finally {
      setLoadingMore(false)
    }
  }, [next, loadingMore, kind, q, sort])

  useEffect(() => {
    if (!enabled) return
    // A pause while typing: one request per settled query, not per key.
    const timer = setTimeout(() => void load(), q ? 250 : 0)
    return () => clearTimeout(timer)
  }, [enabled, load, q])

  /** Drops documents deleted here, keeping the counts in step. */
  const removeRows = (ids: string[]) => {
    const gone = rows.filter((row) => ids.includes(row.id))
    if (!gone.length) return
    setRows((held) => held.filter((row) => !ids.includes(row.id)))
    setCounts((c) => {
      if (!c) return c
      const out = { ...c }
      for (const row of gone) out[row.kind] = Math.max(0, (out[row.kind] ?? 0) - 1)
      return out
    })
    setTotal((n) => Math.max(0, n - gone.length))
  }

  /** A renamed document, in place. */
  const updateRow = (row: WorkingDocRow) => setRows((held) => held.map((h) => (h.id === row.id ? row : h)))

  const fresh = heldFor === key
  // every kind over the search: the tab's badge once this list has answered
  const all = counts ? Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0) : null
  return {
    rows: fresh ? rows : [],
    loading: loading || (!fresh && !failed),
    counts,
    all,
    total,
    hasMore: fresh && next !== null,
    loadingMore,
    failed,
    moreFailed,
    load,
    loadMore,
    removeRows,
    updateRow,
  }
}
