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
 * Picking one: the settings the template implies, the sentence where the
 * sentence is the prompt, and the chip where the id still has a turn to ride
 * on. Three different templates get three different halves of that, and which
 * half is decided here.
 *
 * Three screens start a 서식 — the gallery inside a session, the catalogue on
 * the 디자인 screen, and the rail at home — and a shape that set the aspect
 * ratio when it was chosen in one of them but not in another would be a shape
 * that behaves differently depending on where it was found. The same is true
 * of the sentence, which is why the distinction lives here rather than in any
 * of the three.
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
  const setOptionTemplate = useStore((s) => s.setOptionTemplate)

  return (row: DesignTemplateRow, prompt: string) => {
    // For a picture or a clip the filled-in sentence *is* the prompt — the
    // person edits it and sends it, and without it there is nothing to send.
    // A deck or a document is the other way round: the chip already names the
    // shape, and typing the example into the box put the product's words in
    // the transcript under the person's name.
    if (row.kind === 'image' || row.kind === 'video' || row.kind === 'audio') {
      setDraft(prompt)
    }
    const d = row.defaults ?? {}
    if (row.kind === 'video' || row.kind === 'audio') {
      // Spent the moment it is picked, so nothing is held for the turn. An
      // a/v 서식 carries no clause for the model — only the image ones do —
      // and the endpoints that make a clip take a prompt and these chips and
      // nothing besides. What it changed is on screen where the person can
      // still read and edit it; a chip left on the composer afterwards would
      // name a shape that goes nowhere at submit.
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
      // After the write, never before: turning a chip by hand is what tells the
      // store the values stopped being the template's, and the write above is
      // that same setter. The bar reads this to say whose values it is showing
      // — a media 서식 leaves no chip, and these settings outlive the session
      // it was picked in, so without a name on them a clip made next week comes
      // out in this shape for no reason anyone can see.
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
      // Same reason, and the same order — but only where it actually set
      // something. The chip above names the shape while the pick is still
      // waiting for a turn; the bar takes over once the picture has been asked
      // for and the pick is spent, since the chips it set stay behind. An a/v
      // 서식 needs no such test: its write always carries the mode.
      if (Object.keys(chips).length) setOptionTemplate(row)
    }
  }
}
