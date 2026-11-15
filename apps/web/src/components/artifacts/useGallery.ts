import { useCallback, useEffect, useRef, useState } from 'react'
import { galleryApi, type GallerySection, type GallerySort, type SortDir } from '@/lib/api'
import { toArtifact } from '@/store/useStore'
import type { Artifact } from '@/types'

/** Rows per page of the 아티팩트 page. */
const PAGE = 50

type Counts = Partial<Record<GallerySection, number>>

/**
 * The 아티팩트 page's rows, a page at a time: HTML pages, code files, charts and diagrams,
 * filtered by section, searched by title and sorted on the server, so every filter reaches
 * every artifact.
 */
export function useGallery(section: GallerySection | null, query: string, sort: GallerySort, dir: SortDir) {
  const [rows, setRows] = useState<Artifact[]>([])
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
  const key = `${section ?? ''}|${q}|${sort}|${dir}`

  const load = useCallback(async () => {
    const ticket = ++asked.current
    setLoading(true)
    setFailed(false)
    setMoreFailed(false)
    try {
      const page = await galleryApi.page({ section, q, sort, dir, limit: PAGE })
      if (ticket !== asked.current) return
      setRows(page.items.map(toArtifact))
      setCounts(page.counts)
      setTotal(page.total)
      setNext(page.next)
      setHeldFor(`${section ?? ''}|${q}|${sort}|${dir}`)
    } catch {
      if (ticket === asked.current) setFailed(true)
    } finally {
      if (ticket === asked.current) setLoading(false)
    }
  }, [section, q, sort, dir])

  const loadMore = useCallback(async () => {
    if (!next || loadingMore) return
    const ticket = asked.current
    setLoadingMore(true)
    setMoreFailed(false)
    try {
      const page = await galleryApi.page({ section, q, sort, dir, cursor: next, limit: PAGE })
      if (ticket !== asked.current) return
      const more = page.items.map(toArtifact)
      setRows((held) => [...held, ...more.filter((row) => !held.some((h) => h.id === row.id))])
      setCounts(page.counts)
      setTotal(page.total)
      setNext(page.next)
    } catch {
      if (ticket === asked.current) setMoreFailed(true)
    } finally {
      setLoadingMore(false)
    }
  }, [next, loadingMore, section, q, sort, dir])

  useEffect(() => {
    // A pause while typing: one request per settled query, not per key.
    const timer = setTimeout(() => void load(), q ? 250 : 0)
    return () => clearTimeout(timer)
  }, [load, q])

  /** Drops artifacts deleted here, keeping the counts in step. */
  const removeRows = (ids: string[]) => {
    const gone = rows.filter((row) => ids.includes(row.id))
    if (!gone.length) return
    setRows((held) => held.filter((row) => !ids.includes(row.id)))
    setCounts((c) => {
      if (!c) return c
      const out = { ...c }
      for (const row of gone) {
        const at = row.section as GallerySection | undefined
        if (at) out[at] = Math.max(0, (out[at] ?? 0) - 1)
      }
      return out
    })
    setTotal((n) => Math.max(0, n - gone.length))
  }

  const fresh = heldFor === key
  const all = counts ? Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0) : undefined
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
  }
}
