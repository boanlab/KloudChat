/** 템플릿 catalogue and starting one. Hooks only (fast refresh). */
import { useEffect, useState } from 'react'
import { designTemplatesApi, type DesignTemplateRow } from '@/lib/api'
import { useStore } from '@/store/useStore'

/** The store's catalogue copy, else a request; `enabled` skips the fetch. */
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

/** Applies a picked template; decks and documents keep only the chip. */
export function useStartTemplate() {
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  return (row: DesignTemplateRow) => setPendingTemplate(row)
}
