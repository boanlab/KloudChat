import { patternOf } from '@/components/slides/patterns'
import type { Slide } from '@/types'

/**
 * Changing a web slide's layout: its content carried into the new layout's fields, and what
 * the new layout cannot show kept aside on the slide (`held`), never dropped.
 */

/** Layouts made of [left, right] pairs; see `Slide.bands`. */
export const PAIRED = ['bands', 'tiles', 'timeline', 'steps', 'cards'] as const
export type Paired = (typeof PAIRED)[number]

export function pairedLayout(slide: Slide): Paired | null {
  return (PAIRED as readonly string[]).includes(slide.layout)
    ? (slide.layout as Paired)
    : null
}

/** Pairs on the field the layout names; the other paired fields cleared. */
export function pairFields(layout: Paired | null, pairs?: [string, string][]): Partial<Slide> {
  return {
    bands: layout === 'bands' ? pairs : undefined,
    tiles: layout === 'tiles' ? pairs : undefined,
    timeline: layout === 'timeline' ? pairs : undefined,
    steps: layout === 'steps' ? pairs : undefined,
    cards: layout === 'cards' ? pairs : undefined,
  }
}

/** The slide's `[left, right]` pairs, from a paired layout or a pairs pattern; `null` if it has none. */
export function pairsOf(slide: Slide): [string, string][] | null {
  const paired = pairedLayout(slide)
  if (paired) return slide[paired] ?? []
  if (patternOf(slide.layout)?.shape === 'pairs') return slide.items ?? []
  return null
}

/** `"이름: 설명"` (or `이름 — 설명`) → `['이름', '설명']`; a line with neither keeps it whole on the left. */
export function splitPair(line: string): [string, string] | null {
  const match = /^(.{1,40}?)\s*(?::\s|：|\s[—–-]\s)\s*(.+)$/.exec(line.trim())
  return match ? [match[1].trim(), match[2].trim()] : null
}

/** Every piece of text a slide carries in its content fields (not its title or notes). */
function contentTexts(slide: Slide): string[] {
  return [
    slide.body,
    ...(slide.bullets ?? []),
    ...(slide.rows ?? []).flat(),
    ...(slide.metrics ?? []).flat(),
    ...PAIRED.flatMap((field) => (slide[field] ?? []).flat()),
    ...(slide.items ?? []).flat(),
    ...(slide.columns ?? []).flatMap((column) => [column.title, ...column.items]),
  ].map((text) => (text ?? '').trim()).filter(Boolean)
}

/**
 * Re-shape one slide into another layout, carrying its content over. Nothing is dropped: what
 * the new layout cannot show (a chart on a text layout, text the conversion left out) is kept
 * in `held`, shown as a notice under the slide, and taken back by a later layout change.
 */
export function relayout(slide: Slide, layout: Slide['layout']): Slide {
  if (layout === slide.layout) return slide
  // What an earlier change set aside comes back first.
  const held = slide.held
  const source: Slide = held
    ? {
        ...slide,
        held: undefined,
        chart: slide.chart ?? held.chart,
        bullets: held.lines?.length ? [...(slide.bullets ?? []), ...held.lines] : slide.bullets,
      }
    : slide
  const shaped = reshape(source, layout)
  const kept = contentTexts(shaped).join('\n')
  const lines = [...new Set(contentTexts(source).filter((text) => !kept.includes(text)))]
  const chart = !shaped.chart && source.chart ? source.chart : undefined
  return lines.length || chart
    ? { ...shaped, held: { ...(lines.length ? { lines } : {}), ...(chart ? { chart } : {}) } }
    : { ...shaped, held: undefined }
}

/** How much a slide holds back (`relayout`): lines of text, and whether a chart. */
export function heldCount(slide: Slide): { lines: number; chart: boolean } {
  return { lines: slide.held?.lines?.length ?? 0, chart: !!slide.held?.chart }
}

/** The layout change itself; `relayout` keeps what it leaves out. */
function reshape(slide: Slide, layout: Slide['layout']): Slide {
  const columns = slide.columns?.filter((column) => column.title || column.items.length) ?? []
  const pairs: [string, string][] =
    pairsOf(slide) ??
    (columns.length
      ? columns.flatMap((column) => column.items.map((item) => [column.title, item] as [string, string]))
      : null) ??
    slide.metrics ??
    slide.rows?.map((row) => [row[0] ?? '', row.slice(1).join(' · ')] as [string, string]) ??
    []
  const target = patternOf(layout)
  // The slide's items: its bullets, else its pairs as lines; its body after them.
  const items = slide.bullets?.length
    ? slide.bullets
    : pairs.map(([left, right]) => [left, right].filter(Boolean).join(columns.length ? ': ' : ' — '))
  // A pattern takes back the lines a one-line layout joined with ` · `.
  const bodyParts = slide.body ? (target ? slide.body.split(' · ').filter(Boolean) : [slide.body]) : []
  const lines = [...items, ...bodyParts]
  const keepsChart = layout === 'chart' || target?.shape === 'chart'
  const clean: Slide = {
    ...slide,
    layout,
    body: undefined,
    bullets: undefined,
    rows: undefined,
    metrics: undefined,
    items: undefined,
    columns: undefined,
    chart: keepsChart && slide.chart
      ? {
          ...slide.chart,
          // A chart pattern fixes its kind; back on the plain chart, a kind it cannot draw becomes bars.
          kind: (target?.params.kind as NonNullable<Slide['chart']>['kind'] | undefined)
            ?? (slide.chart.kind === 'line' ? 'line' : 'bar'),
        }
      : undefined,
    ...pairFields(null),
  }
  // Pairs a new pairs layout carries: the old pairs, else lines split at「:」or numbered.
  const carried = (number: boolean): [string, string][] =>
    pairs.length
      ? pairs
      : lines.map((line, i) => splitPair(line) ?? (number ? [String(i + 1).padStart(2, '0'), line] : [line, '']))
  // Every pair as metrics, none cut: a layout that shows fewer says so (`overflowRisk`).
  const asMetrics = (): [string, string][] => pairs.length
    ? pairs
    : lines.map((line, i) => splitPair(line) ?? ([String(i + 1), line] as [string, string]))
  if (target) {
    if (target.shape === 'pairs') return { ...clean, items: carried(false) }
    if (target.shape === 'text') return { ...clean, body: lines.join(' · ') || undefined }
    if (target.shape === 'chart') return clean
    if (target.shape === 'metrics') return { ...clean, metrics: asMetrics() }
    const [, most] = target.count
    // columns: kept as they were, else pairs grouped by their left side (the inverse of flattening).
    if (columns.length) return { ...clean, columns }
    const grouped: { title: string; items: string[] }[] = []
    for (const [left, right] of carried(false)) {
      const into = grouped.find((column) => column.title === left)
      if (into) {
        if (right) into.items.push(right)
      } else grouped.push({ title: left, items: right ? [right] : [] })
    }
    // More groups than the pattern has panels: the rest join the last panel, labelled.
    const kept = grouped.slice(0, most)
    for (const extra of grouped.slice(most)) {
      kept[kept.length - 1].items.push(...(extra.items.length ? extra.items.map((item) => `${extra.title}: ${item}`) : [extra.title]))
    }
    return { ...clean, columns: kept }
  }
  if (layout === 'title' || layout === 'section' || layout === 'quote' || layout === 'statement') {
    return { ...clean, body: lines.join(' · ') || undefined }
  }
  if (layout === 'big-number') {
    const [first, ...rest] = asMetrics()
    const others = pairs.length
      ? [...rest.map(([value, label]) => [value, label].filter(Boolean).join(' — ')), ...bodyParts]
      : lines.slice(1)
    return { ...clean, metrics: first ? [first] : [], body: others.join(' · ') || undefined }
  }
  if (layout === 'closing') {
    return { ...clean, bullets: items.length ? items : undefined, body: slide.body || undefined }
  }
  if (layout === 'table') {
    const rows = slide.rows
      ?? (columns.length
        ? [columns.map((column) => column.title), ...Array.from({ length: Math.max(...columns.map((column) => column.items.length)) }, (_, r) => columns.map((column) => column.items[r] ?? ''))]
        : pairs.map(([left, right]) => [left, right]))
    const extra = slide.bullets?.length && rows.length ? slide.bullets : []
    return {
      ...clean,
      rows: rows.length ? rows : lines.map((line) => [line]),
      bullets: extra.length ? extra : undefined,
    }
  }
  if (layout === 'metrics') return { ...clean, metrics: asMetrics() }
  if ((PAIRED as readonly string[]).includes(layout)) {
    return { ...clean, ...pairFields(layout as Paired, carried(true)) }
  }
  return { ...clean, bullets: lines }
}
