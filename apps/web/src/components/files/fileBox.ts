import { useCallback, useEffect, useRef, useState } from 'react'
import { filesApi, type FileRow, type OfficeKind } from '@/lib/api'

export type FileOrigin = 'upload' | 'generated'

/** Rows per file-box page. */
export const FILE_PAGE = 50

/**
 * The account's files of one origin, a page at a time, uploaded and made in conversations alike;
 * agents' knowledge left out. The name search runs on the server, so it reaches every file.
 */
export function useFileBox(enabled = true, origin: FileOrigin = 'upload', query = '') {
  const [rows, setRows] = useState<FileRow[]>([])
  const [counts, setCounts] = useState<Record<FileOrigin, number>>({ upload: 0, generated: 0 })
  const [total, setTotal] = useState(0)
  const [next, setNext] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [failed, setFailed] = useState(false)
  // Which origin and search the held rows answer; another one shows as loading, not as old rows.
  const [heldFor, setHeldFor] = useState<string | null>(null)
  const [moreFailed, setMoreFailed] = useState(false)
  // Typing outruns the network: only the latest request's answer is shown.
  const asked = useRef(0)
  const q = query.trim()

  const load = useCallback(async () => {
    const ticket = ++asked.current
    setLoading(true)
    setFailed(false)
    setMoreFailed(false)
    try {
      const page = await filesApi.page({ box: true, origin, q, limit: FILE_PAGE })
      if (ticket !== asked.current) return
      setRows(page.items)
      setHeldFor(`${origin}|${q}`)
      setCounts(page.counts)
      setTotal(page.total)
      setNext(page.next)
    } catch {
      if (ticket === asked.current) setFailed(true)
    } finally {
      if (ticket === asked.current) setLoading(false)
    }
  }, [origin, q])

  const loadMore = useCallback(async () => {
    if (!next || loadingMore) return
    const ticket = asked.current
    setLoadingMore(true)
    setMoreFailed(false)
    try {
      const page = await filesApi.page({ box: true, origin, q, cursor: next, limit: FILE_PAGE })
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
  }, [next, loadingMore, origin, q])

  useEffect(() => {
    if (!enabled) return
    // A pause while typing: one request per settled query, not per key.
    const timer = setTimeout(() => void load(), q ? 250 : 0)
    return () => clearTimeout(timer)
  }, [enabled, load, q])

  /** Drops rows deleted here, keeping the counts in step. */
  const removeRows = (ids: string[]) => {
    const gone = rows.filter((row) => ids.includes(row.id))
    if (!gone.length) return
    setRows((held) => held.filter((row) => !ids.includes(row.id)))
    setCounts((c) => {
      const out = { ...c }
      for (const row of gone) out[originOf(row)] = Math.max(0, out[originOf(row)] - 1)
      return out
    })
    setTotal((n) => Math.max(0, n - gone.length))
  }

  /** A file uploaded here, on top; counted under its origin. */
  const addRow = (row: FileRow) => {
    setRows((held) => [row, ...held.filter((h) => h.id !== row.id)])
    setCounts((c) => ({ ...c, [originOf(row)]: c[originOf(row)] + 1 }))
    setTotal((n) => n + 1)
  }

  /** A fresher copy of a row (its scan or video was read meanwhile). */
  const updateRow = (row: FileRow) => setRows((held) => held.map((h) => (h.id === row.id ? row : h)))

  const fresh = heldFor === `${origin}|${q}`
  return { rows: fresh ? rows : [], loading: loading || (!fresh && !failed), counts, total, hasMore: fresh && next !== null, loadingMore, failed, moreFailed, load, loadMore, removeRows, addRow, updateRow }
}

export const originOf = (row: FileRow): FileOrigin => (row.origin === 'generated' ? 'generated' : 'upload')

export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** The office editor a file opens in, by its name: HWP (`.hwp`, `.hwpx`), Word, PowerPoint.
 *  Legacy `.doc`/`.ppt` have none. */
export function officeKindOf(name: string): OfficeKind | null {
  if (/\.hwpx?$/i.test(name)) return 'hwp'
  if (/\.docx$/i.test(name)) return 'docx'
  if (/\.pptx$/i.test(name)) return 'pptx'
  return null
}
