import { galleryApi, type GallerySection, type GallerySort, type SortDir } from '@/lib/api'
import { useServerPages } from '@/lib/useServerPages'
import { toArtifact } from '@/store/useStore'
import type { Artifact } from '@/types'

type Counts = Partial<Record<GallerySection, number>>

/**
 * The 아티팩트 page's rows, a numbered page at a time: HTML pages, code files, charts and diagrams,
 * filtered by section, searched by title and sorted on the server, so every filter reaches
 * every artifact.
 */
export function useGallery(section: GallerySection | null, query: string, sort: GallerySort, dir: SortDir) {
  const q = query.trim()
  const list = useServerPages<Artifact, Counts>({
    keys: [section ?? '', q, sort, dir],
    debounce: Boolean(q),
    fetch: async (offset, limit) => {
      const page = await galleryApi.page({ section, q, sort, dir, limit, offset })
      return { items: page.items.map(toArtifact), total: page.total, counts: page.counts }
    },
  })
  const { rows, load, setRows, setCounts, setTotal } = list
  const counts = list.counts ?? null

  /** Drops artifacts deleted here, keeping the counts in step; the page fills up again behind them. */
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
    void load(true)
  }

  const all = counts ? Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0) : undefined
  return {
    rows,
    loading: list.loading,
    counts,
    all,
    total: list.total,
    paging: list.paging,
    failed: list.failed,
    load: () => load(),
    removeRows,
  }
}
