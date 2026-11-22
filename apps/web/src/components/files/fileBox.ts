import { useServerPages } from '@/lib/useServerPages'
import { filesApi, type FileRow, type FileSort, type OfficeKind, type SortDir } from '@/lib/api'

export type FileOrigin = 'upload' | 'generated'
/** The file box's tabs: files by origin, and the documents worked on (「작업 문서」). */
export type FileBoxTab = FileOrigin | 'working'
export type FileBoxCounts = Record<FileOrigin, number> & { working?: number }

/** Rows per file-box page. */

/** Paged files of one origin (uploaded and conversation-made; agent knowledge excluded); name search runs server-side. */
export function useFileBox(
  enabled = true,
  origin: FileOrigin = 'upload',
  query = '',
  sort: FileSort = 'created',
  dir: SortDir = 'desc',
) {
  const q = query.trim()
  const list = useServerPages<FileRow, FileBoxCounts>({
    enabled,
    keys: [origin, q, sort, dir],
    debounce: Boolean(q),
    fetch: async (offset, limit) => {
      const page = await filesApi.page({ box: true, origin, q, sort, dir, limit, offset })
      return { items: page.items, total: page.total, counts: page.counts }
    },
  })
  const { rows, load, setRows, setCounts, setTotal } = list
  const counts = list.counts ?? { upload: 0, generated: 0 }

  /** Drops rows deleted here, keeping the counts in step; the page fills up again behind them. */
  const removeRows = (ids: string[]) => {
    const gone = rows.filter((row) => ids.includes(row.id))
    if (!gone.length) return
    setRows((held) => held.filter((row) => !ids.includes(row.id)))
    setCounts((c) => {
      const out = { ...(c ?? { upload: 0, generated: 0 }) }
      for (const row of gone) out[originOf(row)] = Math.max(0, out[originOf(row)] - 1)
      return out
    })
    setTotal((n) => Math.max(0, n - gone.length))
    void load(true)
  }

  /** A file uploaded here, on top; counted under its origin. */
  const addRow = (row: FileRow) => {
    setRows((held) => [row, ...held.filter((h) => h.id !== row.id)].slice(0, list.paging.size))
    setCounts((c) => {
      const base = c ?? { upload: 0, generated: 0 }
      return { ...base, [originOf(row)]: base[originOf(row)] + 1 }
    })
    setTotal((n) => n + 1)
  }

  const updateRow = (row: FileRow) => setRows((held) => held.map((h) => (h.id === row.id ? row : h)))

  return {
    rows,
    loading: list.loading,
    counts,
    total: list.total,
    paging: list.paging,
    failed: list.failed,
    load: () => load(),
    removeRows,
    addRow,
    updateRow,
  }
}

export const originOf = (row: FileRow): FileOrigin => (row.origin === 'generated' ? 'generated' : 'upload')

export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}

/** Office editor a file opens in, by name; legacy `.doc`/`.ppt` have none. */
export function officeKindOf(name: string): OfficeKind | null {
  if (/\.hwpx?$/i.test(name)) return 'hwp'
  if (/\.docx$/i.test(name)) return 'docx'
  if (/\.pptx$/i.test(name)) return 'pptx'
  return null
}
