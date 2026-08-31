/**
 * Reading the 서식 catalogue, and starting one.
 *
 * Its own module rather than the gallery's: three screens start a 서식, and a
 * file exporting both components and hooks loses fast refresh.
 */
import { useEffect, useState } from 'react'
import {
  designTemplatesApi,
  type DesignTemplateRow,
  type DesignTemplateUsage,
} from '@/lib/api'
import { useStore } from '@/store/useStore'

/**
 * The catalogue itself, wherever it is being read.
 *
 * The store's copy comes from the workspace load; the request is the fallback
 * for a screen opened before that landed. `enabled` keeps the composer's
 * gallery from fetching a catalogue nobody opened.
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
 * The catalogue ordered by what people actually pick.
 *
 * Its own order is by id, which is the order the files happen to sit in and
 * means nothing to anybody — so the front door led with whatever sorted first
 * and the shapes people reach for most were as likely to be two screens away.
 *
 * This person's own habit first, everyone's after it. Both matter and neither
 * alone is enough: `mine` is empty on somebody's first day, and `popular` goes
 * on describing the average user long after this one has their own way of
 * working. Ordering by `mine` and breaking ties on `popular` gives the new
 * person a sensible catalogue and then gets out of the way.
 *
 * The counts are a *sort key*, never a filter. A template nobody has used is
 * last, not hidden — a catalogue that only shows what is already popular is a
 * catalogue where nothing new is ever found.
 *
 * A failed request sorts by nothing rather than showing nothing.
 */
export function useTemplateUsage(enabled = true): DesignTemplateUsage {
  const [usage, setUsage] = useState<DesignTemplateUsage>({ mine: {}, popular: {} })
  useEffect(() => {
    if (!enabled) return
    let live = true
    void designTemplatesApi
      .usage()
      .then((next) => live && setUsage(next))
      .catch(() => undefined)
    return () => {
      live = false
    }
  }, [enabled])
  return usage
}

/**
 * Picking one: the settings the template implies, the sentence where the
 * sentence is the prompt, and the chip where the id still has a turn to ride
 * on. Which half applies depends on the template, and is decided here rather
 * than in any of the three screens that start one — otherwise a shape would
 * behave differently depending on where it was found.
 *
 * Only the keys the template names: one that says nothing about duration
 * leaves whatever the person last chose.
 */
export function useStartTemplate() {
  const setDraft = useStore((s) => s.setDraft)
  const setPendingTemplate = useStore((s) => s.setPendingTemplate)
  const setImageOptions = useStore((s) => s.setImageOptions)
  const setAvOptions = useStore((s) => s.setAvOptions)
  const setOptionTemplate = useStore((s) => s.setOptionTemplate)

  return (row: DesignTemplateRow, prompt: string) => {
        // For a picture or a clip the filled-in sentence *is* the prompt. A deck
        // or a document is the other way round — the chip names the shape, and
        // typing the example into the box would put the product's words in the
        // transcript under the person's name.
    if (row.kind === 'image' || row.kind === 'video' || row.kind === 'audio') {
      setDraft(prompt)
    }
    const d = row.defaults ?? {}
    if (row.kind === 'video' || row.kind === 'audio') {
            // Spent the moment it is picked: an a/v 서식 carries no clause for the
            // model, and the clip endpoints take a prompt and these chips and
            // nothing else. A chip left on the composer would name a shape that
            // goes nowhere at submit.
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
            // After the write, never before: turning a chip by hand is what tells
            // the store the values stopped being the template's, and the write above
            // is that same setter. The bar reads this to say whose values it shows.
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
            // Same reason and the same order, but only where it set something. The
            // chip names the shape while the pick awaits a turn; the bar takes over
            // once it is spent. An a/v 서식 needs no such test — its write always
            // carries the mode.
      if (Object.keys(chips).length) setOptionTemplate(row)
    }
  }
}
