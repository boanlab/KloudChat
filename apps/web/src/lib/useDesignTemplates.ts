/** Reading the 서식 catalogue and starting one. Hooks only, so the module keeps fast refresh. */
import { useEffect, useState } from 'react'
import { designTemplatesApi, type DesignTemplateRow } from '@/lib/api'
import { useStore } from '@/store/useStore'

/**
 * The catalogue: the store's copy from the workspace load, or a request when
 * that has not landed yet. `enabled` skips the fetch for a gallery nobody opened.
 */
export function useDesignTemplates(enabled = true) {
  const cached = useStore((s) => s.designTemplates)
  const [rows, setRows] = useState<DesignTemplateRow[]>(cached)
  useEffect(() => {
    if (!enabled) return
    if (cached.length) {
      setRows(cached)
      return
    }
    void designTemplatesApi.list().then(setRows).catch(() => setRows([]))
  }, [enabled, cached])
  return rows
}

/**
 * Applies a picked template: a deck or document keeps only the chip, so the example never
 * lands in the transcript.
 */
export function useStartTemplate() {
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  return (row: DesignTemplateRow) => setPendingTemplate(row)
}
