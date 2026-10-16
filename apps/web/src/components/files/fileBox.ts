import { useCallback, useEffect, useState } from 'react'
import { filesApi, type FileRow } from '@/lib/api'

export type FileOrigin = 'upload' | 'generated'

/** The account's files, uploaded and made in conversations alike; agents' knowledge left out. */
export function useFileBox(enabled = true) {
  const [rows, setRows] = useState<FileRow[]>([])
  const [loading, setLoading] = useState(true)
  const [failed, setFailed] = useState(false)
  const load = useCallback(async () => {
    setLoading(true)
    setFailed(false)
    try {
      // An agent's knowledge belongs to the agent's page, not the file box.
      setRows((await filesApi.list()).filter((row) => !row.agentId))
    } catch {
      setFailed(true)
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    if (enabled) void load()
  }, [enabled, load])
  return { rows, setRows, loading, failed, load }
}

export const originOf = (row: FileRow): FileOrigin => (row.origin === 'generated' ? 'generated' : 'upload')

/** Rows of one origin whose name holds the query (case and spaces aside). */
export function shownFiles(rows: FileRow[], origin: FileOrigin, query: string) {
  const q = query.trim().toLowerCase()
  return rows.filter((row) => originOf(row) === origin && (!q || row.name.toLowerCase().includes(q)))
}

export function fileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
