import { useServerPages } from '@/lib/useServerPages'
import { workingDocsApi, type SortDir, type WorkingDocRow, type WorkingKind, type WorkingSort } from '@/lib/api'

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
export function useWorkingDocs(enabled: boolean, kind: WorkingKind | null, query: string, sort: WorkingSort, dir: SortDir) {
  const q = query.trim()
  const list = useServerPages<WorkingDocRow, Counts>({
    enabled,
    keys: [kind ?? '', q, sort, dir],
    debounce: Boolean(q),
    fetch: async (offset, limit) => {
      const page = await workingDocsApi.page({ kind, q, sort, dir, limit, offset })
      return { items: page.items, total: page.total, counts: page.counts }
    },
  })
  const { rows, load, setRows, setCounts, setTotal } = list
  const counts = list.counts ?? null

  /** Drops documents deleted here, keeping the counts in step; the page fills up again behind them. */
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
    void load(true)
  }

  /** A renamed document, in place. */
  const updateRow = (row: WorkingDocRow) => setRows((held) => held.map((h) => (h.id === row.id ? row : h)))

  // every kind over the search: the tab's badge once this list has answered
  const all = counts ? Object.values(counts).reduce((sum, n) => sum + (n ?? 0), 0) : null
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
    updateRow,
  }
}
