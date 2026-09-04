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
 * Applies a picked template. For image/video/audio the filled-in sentence is
 * the prompt; a deck or document keeps only the chip, so the example never
 * lands in the transcript. Only the keys the template names are set.
 */
export function useStartTemplate() {
  const setDraft = useStore((s) => s.setDraft)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  const setImageOptions = useStore((s) => s.setImageOptions)
  const setAvOptions = useStore((s) => s.setAvOptions)
  const setOptionTemplate = useStore((s) => s.setOptionTemplate)

  return (row: DesignTemplateRow, prompt: string) => {
    if (row.kind === 'image' || row.kind === 'video' || row.kind === 'audio') {
      setDraft(prompt)
    }
    const d = row.defaults ?? {}
    if (row.kind === 'video' || row.kind === 'audio') {
      // An a/v 서식 is spent when picked: the clip endpoints take a prompt and these chips only.
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
        ...(typeof d.voice === 'string' && d.voice ? { voice: d.voice } : {}),
      })
      // After the write: the same setter is what marks values as hand-edited.
      setOptionTemplate(row)
      return
    }
    setPendingTemplate(row)
    if (row.kind === 'image') {
      const chips = {
        ...(typeof d.aspect === 'string' ? { aspect: d.aspect } : {}),
        ...(typeof d.style === 'string' ? { style: d.style } : {}),
        ...(typeof d.count === 'number' ? { count: d.count } : {}),
      }
      setImageOptions(chips)
      // Only where something was set, and after the write for the same reason.
      if (Object.keys(chips).length) setOptionTemplate(row)
    }
  }
}
