/**
 * Reading the 서식 catalogue, and starting one.
 *
 * Their own module rather than the gallery's, because three screens start a
 * 서식 — the gallery inside a session, the catalogue on the 디자인 screen and
 * the rail at home — and a file that exports both components and hooks loses
 * fast refresh for all of them.
 */
import { useEffect, useState } from 'react'
import { designTemplatesApi, type DesignTemplateRow } from '@/lib/api'
import { useStore } from '@/store/useStore'

/**
 * The catalogue itself, wherever it is being read.
 *
 * The store's copy is what the workspace load already fetched; the request is
 * the fallback for a screen opened before that landed. `enabled` is for the
 * gallery, which sits inside every composer and has no business asking for a
 * catalogue nobody opened.
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
 * Picking one: the sentence, the chip on the composer, and the settings the
 * template implies.
 *
 * Three screens start a 서식 — the gallery inside a session, the catalogue on
 * the 디자인 screen, and the rail at home — and a shape that set the aspect
 * ratio when it was chosen in one of them but not in another would be a shape
 * that behaves differently depending on where it was found.
 *
 * Only the keys the template names: one that says nothing about duration
 * leaves whatever the person last chose, rather than resetting it to a default
 * they did not ask for.
 */
export function useStartTemplate() {
  const setDraft = useStore((s) => s.setDraft)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  const setImageOptions = useStore((s) => s.setImageOptions)
  const setAvOptions = useStore((s) => s.setAvOptions)

  return (row: DesignTemplateRow, prompt: string) => {
    setPendingTemplate(row)
    setDraft(prompt)
    const d = row.defaults ?? {}
    if (row.kind === 'image') {
      setImageOptions({
        ...(typeof d.aspect === 'string' ? { aspect: d.aspect } : {}),
        ...(typeof d.style === 'string' ? { style: d.style } : {}),
        ...(typeof d.count === 'number' ? { count: d.count } : {}),
      })
      return
    }
    if (row.kind === 'video' || row.kind === 'audio') {
      setAvOptions({
        mode: row.kind === 'audio' ? 'audio' : 'video',
        ...(typeof d.aspect === 'string' ? { aspect: d.aspect } : {}),
        ...(typeof d.seconds === 'number' ? { durationSec: d.seconds } : {}),
        ...(d.resolution === '720p' || d.resolution === '1080p'
          ? { resolution: d.resolution }
          : {}),
        ...(typeof d.audio === 'boolean' ? { withAudio: d.audio } : {}),
        ...(d.audioKind === 'narration' || d.audioKind === 'music'
          ? { audioKind: d.audioKind }
          : {}),
        // A narration template names its reader; until the composer had a
        // voice chip this was the one default that went nowhere.
        ...(typeof d.voice === 'string' && d.voice ? { voice: d.voice } : {}),
      })
    }
  }
}
