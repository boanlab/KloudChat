import {
  ArrowDown,
  ArrowUp,
  Bold,
  ChevronLeft,
  ChevronRight,
  Copy,
  Download,
  Eraser,
  Grid2x2,
  ImagePlus,
  Italic,
  LayoutTemplate,
  ListPlus,
  Loader2,
  Maximize,
  MessageSquare,
  PanelLeft,
  PanelRight,
  Palette,
  Pencil,
  Play,
  Presentation,
  Rows3,
  Save,
  RefreshCw,
  Redo2,
  ShieldQuestion,
  StickyNote,
  Trash2,
  TriangleAlert,
  Underline,
  Undo2,
  X,
} from 'lucide-react'
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import {
  PanelControls,
  usePanelWidth,
  type PanelMode,
} from '@/components/artifacts/PanelControls'
import { usePanelNarrow } from '@/lib/usePanelNarrow'
import { Button, ConfirmDialog, Dropdown, Input, MenuItem, MenuLabel, Modal, Textarea } from '@/components/ui'
import { artifactsApi, downloadArtifact as download, errorMessage } from '@/lib/api'
import { cn } from '@/lib/utils'
import type { DeckArtifact, LintFinding, Slide } from '@/types'
import { FactCheckResults } from '@/components/artifacts/FactCheckResults'
import { LintFindings, byWhere, fixNote } from '@/components/artifacts/LintFindings'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { useStore } from '@/store/useStore'
import { SlideChart } from '@/components/slides/SlideChart'
import { useT } from '@/lib/useT'
import { PicturePicker } from '@/components/artifacts/PicturePicker'
import { ArtifactRibbon, QuickAccess, RibbonGroup } from '@/components/artifacts/ArtifactRibbon'
import { copyText } from '@/lib/clipboard'


/**
 * Is there anything on this slide yet?
 *
 * Every field a slide can carry content in, not the two it could carry when
 * this was written. `bullets` and `body` were the whole list; a table's `rows`,
 * a strip's `metrics` and a chart's numbers live nowhere near them — so a
 * finished table slide was indistinguishable from one the model had not
 * written yet, and a deck containing one was "still being written" forever.
 * 내보내기, 발표 and 텍스트 수정 stayed disabled on a deck that was complete.
 *
 * Mirrors `deck.has_content` on the server, which drops contentless slides
 * from the stored deck and was dropping the same three layouts.
 */
export function hasContent(slide: Slide): boolean {
  // A divider says the name of the part and nothing else, which is all a
  // divider is for — asked to fill one it would be a table of contents.
  if (STRUCTURAL.includes(slide.layout)) return true
  return Boolean(
    slide.bullets?.length ||
      slide.body?.trim() ||
      slide.rows?.length ||
      slide.metrics?.length ||
      slide.chart ||
      slide.bands?.length ||
      slide.tiles?.length ||
      slide.timeline?.length ||
      slide.steps?.length ||
      slide.cards?.length,
  )
}

/** A cheap whole-deck preflight; the selected slide is then measured in pixels. */
function overflowRisk(slide: Slide): boolean {
  const titleLoad = Math.ceil((slide.title?.length ?? 0) / 34)
  const bulletLoad = (slide.bullets ?? []).reduce((sum, row) => sum + Math.max(1, Math.ceil(row.length / 38)), 0)
  const tableLoad = (slide.rows ?? []).length * 1.35
  const pairLoad = Math.max(slide.metrics?.length ?? 0, slide.bands?.length ?? 0, slide.tiles?.length ?? 0, slide.timeline?.length ?? 0, slide.steps?.length ?? 0, slide.cards?.length ?? 0) * 1.6
  const chartLoad = (slide.chart?.categories.length ?? 0) * 0.8 + (slide.chart?.series.length ?? 0) * 0.8
  const scale = slide.textScale ?? 1
  return (titleLoad * 1.5 + bulletLoad + tableLoad + pairLoad + chartLoad) * scale > 10
}

function splitStructuredSlide(slide: Slide, continuation: string): [Slide, Slide] | null {
  const make = (firstPatch: Partial<Slide>, secondPatch: Partial<Slide>): [Slide, Slide] => {
    const common = { ...slide, textScale: undefined, factCheck: undefined, richText: undefined }
    return [
      { ...common, ...firstPatch },
      { ...common, id: `sl${Date.now().toString(36)}`, title: `${slide.title} ${continuation}`, ...secondPatch },
    ]
  }
  if ((slide.bullets?.length ?? 0) >= 2) {
    const at = Math.ceil(slide.bullets!.length / 2)
    return make({ bullets: slide.bullets!.slice(0, at) }, { bullets: slide.bullets!.slice(at) })
  }
  if ((slide.rows?.length ?? 0) >= 3) {
    const [head, ...body] = slide.rows!
    const at = Math.ceil(body.length / 2)
    return make({ rows: [head, ...body.slice(0, at)] }, { rows: [head, ...body.slice(at)] })
  }
  for (const field of PAIRED) {
    const values = slide[field]
    if ((values?.length ?? 0) >= 2) {
      const at = Math.ceil(values!.length / 2)
      return make({ [field]: values!.slice(0, at) }, { [field]: values!.slice(at) })
    }
  }
  if ((slide.metrics?.length ?? 0) >= 2) {
    const at = Math.ceil(slide.metrics!.length / 2)
    return make({ metrics: slide.metrics!.slice(0, at) }, { metrics: slide.metrics!.slice(at) })
  }
  if ((slide.chart?.categories.length ?? 0) >= 2) {
    const at = Math.ceil(slide.chart!.categories.length / 2)
    const chartPart = (start: number, end?: number) => ({
      ...slide.chart!,
      categories: slide.chart!.categories.slice(start, end),
      series: slide.chart!.series.map((series) => ({ ...series, values: series.values.slice(start, end) })),
    })
    return make({ chart: chartPart(0, at) }, { chart: chartPart(at) })
  }
  const sentences = (slide.body ?? '').split(/(?<=[.!?。])\s+/).filter(Boolean)
  if (sentences.length >= 2) {
    const at = Math.ceil(sentences.length / 2)
    return make({ body: sentences.slice(0, at).join(' ') }, { body: sentences.slice(at).join(' ') })
  }
  return null
}

/**
 * The three layouts that are a left thing and a right thing — see `Slide.bands`.
 *
 * Named once because three places have to agree about them: the drawing, the
 * editable text, and the save that reads that text back. They did not, and the
 * save was the one that lost.
 */
const PAIRED = ['bands', 'tiles', 'timeline', 'steps', 'cards'] as const

/** Slides that say where the deck is: the cover, the dividers, the 목차. */
const STRUCTURAL: Slide['layout'][] = ['title', 'section', 'agenda']
/** Drawn reversed out of the accent, like the cover. */
const COVERS: Slide['layout'][] = ['title', 'section', 'closing']

/**
 * How Korean breaks across lines, set once on each slide root.
 *
 * The default splits a Korean line anywhere at all, so a cover reading
 * 「전교생 AI 기초 교육 의무화 추진 전략」 came out as 「… 교육 의무」 / 「화 추진
 * 전략」 — a word cut in half at the widest type on the deck. `keep-all` moves
 * the break to a space, and `break-word` keeps the one case `keep-all` cannot
 * help — a single token wider than the slide — from running off the edge.
 *
 * Both properties inherit, so the root is the whole of it; the exporters' seed
 * (`_deck/seed.html`) says the same thing for the file that leaves.
 */
const KOREAN_WRAP = { wordBreak: 'keep-all', overflowWrap: 'break-word' } as const
type Paired = (typeof PAIRED)[number]
type SlideElement = 'title' | 'content' | 'image' | 'table' | 'chart' | 'metrics' | 'cards'
const LAYOUTS: { id: Slide['layout']; label: string }[] = [
  { id: 'title', label: '표지' },
  { id: 'agenda', label: '목차' },
  { id: 'section', label: '구분 장' },
  { id: 'bullets', label: '글머리표' },
  { id: 'two-column', label: '두 단' },
  { id: 'quote', label: '인용문' },
  { id: 'statement', label: '핵심 메시지' },
  { id: 'table', label: '표' },
  { id: 'metrics', label: '핵심 수치' },
  { id: 'big-number', label: '큰 숫자' },
  { id: 'chart', label: '차트' },
  { id: 'bands', label: '항목과 설명' },
  { id: 'cards', label: '카드' },
  { id: 'steps', label: '단계' },
  { id: 'tiles', label: '표식' },
  { id: 'timeline', label: '연표' },
  { id: 'closing', label: '마무리' },
]

/** Keep only the inline markup the toolbar can create. */
function cleanInlineHtml(html: string): string {
  const document = new DOMParser().parseFromString(`<div>${html}</div>`, 'text/html')
  const root = document.body.firstElementChild as HTMLElement
  const allowed = new Set(['B', 'STRONG', 'I', 'EM', 'U', 'SPAN', 'FONT', 'BR'])
  const clean = (node: Node): Node | null => {
    if (node.nodeType === Node.TEXT_NODE) return document.createTextNode(node.textContent ?? '')
    if (!(node instanceof HTMLElement)) return null
    if (!allowed.has(node.tagName)) {
      const fragment = document.createDocumentFragment()
      Array.from(node.childNodes).forEach((child) => { const next = clean(child); if (next) fragment.append(next) })
      return fragment
    }
    // Browser `execCommand(fontSize)` emits the 1990s `<font size="5">`.
    // That means an absolute 24px, which can be *smaller* than a scaled slide's
    // 32px body. Convert it to a relative size so "크게" means larger than the
    // text around the selection at every preview scale.
    const element = document.createElement(node.tagName === 'FONT' ? 'span' : node.tagName.toLowerCase())
    if (node.tagName === 'FONT') {
      const size = node.getAttribute('size')
      const color = node.getAttribute('color')
      const relative: Record<string, string> = { '1': '0.65em', '2': '0.8em', '3': '1em', '4': '1.15em', '5': '1.35em', '6': '1.65em', '7': '2em' }
      const styles: string[] = []
      if (size && relative[size]) styles.push(`font-size:${relative[size]}`)
      if (color && /^(#[0-9a-f]{3,8}|rgb\([\d ,.]+\))$/i.test(color)) styles.push(`color:${color}`)
      if (styles.length) element.setAttribute('style', styles.join(';'))
    }
    if (node.tagName === 'SPAN') {
      const safe = ['font-size', 'font-weight', 'font-style', 'text-decoration', 'color']
        .map((name) => [name, node.style.getPropertyValue(name)] as const)
        .filter(([, value]) => value && !/[();]|url/i.test(value))
      if (safe.length) element.setAttribute('style', safe.map(([name, value]) => `${name}:${value}`).join(';'))
    }
    Array.from(node.childNodes).forEach((child) => { const next = clean(child); if (next) element.append(next) })
    return element
  }
  const output = document.createElement('div')
  Array.from(root.childNodes).forEach((child) => { const next = clean(child); if (next) output.append(next) })
  return output.innerHTML
}

function inlineText(html: string): string {
  return new DOMParser().parseFromString(html, 'text/html').body.textContent ?? ''
}

/** Which of the three this slide is, or `null`. */
function pairedLayout(slide: Slide): Paired | null {
  return (PAIRED as readonly string[]).includes(slide.layout)
    ? (slide.layout as Paired)
    : null
}

/**
 * The pairs written onto the field the layout names, and the other two cleared.
 *
 * One slide carries one shape. A `bands` slide that kept a `timeline` array
 * from an earlier edit counts as having content on a layout that never draws
 * it, which is how an empty slide reads as a full one.
 */
function pairFields(layout: Paired | null, pairs?: [string, string][]): Partial<Slide> {
  return {
    bands: layout === 'bands' ? pairs : undefined,
    tiles: layout === 'tiles' ? pairs : undefined,
    timeline: layout === 'timeline' ? pairs : undefined,
    steps: layout === 'steps' ? pairs : undefined,
    cards: layout === 'cards' ? pairs : undefined,
  }
}

/** Re-shape one slide without leaving invisible content from its old layout. */
function relayout(slide: Slide, layout: Slide['layout']): Slide {
  if (layout === slide.layout) return slide
  const oldPairs = pairedLayout(slide)
  const pairs = oldPairs
    ? slide[oldPairs] ?? []
    : slide.metrics ?? slide.rows?.map((row) => [row[0] ?? '', row.slice(1).join(' · ')] as [string, string]) ?? []
  const lines = slide.bullets?.length
    ? slide.bullets
    : slide.body
      ? [slide.body]
      : pairs.map(([left, right]) => [left, right].filter(Boolean).join(' — '))
  const clean: Slide = {
    ...slide,
    layout,
    body: undefined,
    bullets: undefined,
    rows: undefined,
    metrics: undefined,
    chart: layout === 'chart' ? slide.chart : undefined,
    ...pairFields(null),
  }
  if (layout === 'title' || layout === 'section' || layout === 'quote' || layout === 'statement') {
    return { ...clean, body: lines.join(' · ') || undefined }
  }
  if (layout === 'big-number') {
    const [first] = pairs.length ? pairs : lines.slice(0, 1).map((line) => ['1', line] as [string, string])
    return { ...clean, metrics: first ? [first] : [], body: lines.slice(1).join(' · ') || undefined }
  }
  if (layout === 'closing') {
    return { ...clean, bullets: lines.slice(0, 3), body: slide.body || undefined }
  }
  if (layout === 'table') {
    const rows = slide.rows ?? pairs.map(([left, right]) => [left, right])
    return { ...clean, rows: rows.length ? rows : lines.map((line) => [line]) }
  }
  if (layout === 'metrics') {
    const metrics = pairs.length
      ? pairs.slice(0, 4)
      : lines.slice(0, 4).map((line, i) => [String(i + 1), line] as [string, string])
    return { ...clean, metrics }
  }
  if ((PAIRED as readonly string[]).includes(layout)) {
    const next = pairs.length
      ? pairs
      : lines.map((line, i) => [String(i + 1).padStart(2, '0'), line] as [string, string])
    return { ...clean, ...pairFields(layout as Paired, next) }
  }
  return { ...clean, bullets: lines }
}

/**
 * The slide a finding was found on, or `undefined`.
 *
 * Matched on the title, which is all a finding carries. Exact first, then
 * ignoring whitespace — a title somebody has retyped differs from the one the
 * checks ran against by exactly that much, and refusing to fix a slide because
 * its title gained a space is a worse answer than fixing the one it obviously
 * means.
 */
function slideFor(slides: Slide[], where: string): Slide | undefined {
  if (!where) return undefined
  const exact = slides.find((s) => s.title === where)
  if (exact) return exact
  const loose = (text: string) => text.replace(/\s+/g, '')
  return slides.find((s) => loose(s.title) === loose(where))
}

/**
 * One slide, drawn in the same rectangle the exporter uses. The geometry is
 * kept in step with `deck_export.py` — a preview that differs from the .pptx
 * is discovered in the room.
 */
/**
 * One slide, drawn at whatever scale the caller has room for.
 *
 * Exported because the artifact gallery draws the first slide as a deck's
 * thumbnail — the same drawing, so a deck looks in the gallery like the deck
 * it opens as.
 */
/**
 * The fields the caller owns, when a slide is being typed over.
 *
 * A new `slide` prop arrives whenever the deck reloads, and the working copy
 * must not lose what is half-typed — but it must pick up a slide that is
 * genuinely different (somebody moved to the next one).
 */
function pick(next: Slide, working: Slide): Slide {
  return next.id === working.id ? working : next
}


export function SlideView({
  slide,
  scale = 1,
  writing = true,
  deckTitle = '',
  brand,
  index,
  total,
  editable = false,
  onEdit,
  selectedElement,
  onSelectElement,
  onOverflow,
}: {
  slide: Slide
  scale?: number
  /**
   * Whether the run that fills this deck is still going. Defaults to true so a
   * caller that cannot know says the softer of the two things.
   *
   * An empty slide means one of two opposite things and they must not read the
   * same. While the deck is being written it has not been reached yet, and the
   * answer is to wait. Once the run has ended it came back unusable, and
   * "쓰는 중…" on a deck that finished ten minutes ago is a screen telling
   * somebody to keep waiting for something that is never coming.
   */
  writing?: boolean
  /**
   * The deck's name and where this slide falls in it, for the footer.
   *
   * Optional because a thumbnail 400px wide draws a footer nobody can read;
   * the rail passes neither and gets a slide without one.
   */
  deckTitle?: string
  index?: number
  total?: number
  /**
   * The design system's marks: a line at the foot saying whose deck this is,
   * and the picture beside it.
   *
   * Here rather than on the slide because they belong to the deck, not to one
   * 장 of it. `deck_export` draws the same two in the same corner, so the panel
   * and the file agree — a preview that omits the logo is a preview that lies
   * about what will be handed round the room.
   */
  brand?: { accent?: string; footer?: string; logo?: string; visualStyle?: 'editorial' | 'poster' | 'minimal' }
  /**
   * Whether the words on this slide can be typed over.
   *
   * The panel's editor was a textarea with a syntax: first line the title, one
   * line per bullet, and `|` between cells for a table row. So somebody looking
   * at a comparison table on screen, wanting to change one cell, had to find
   * that cell inside `| 기존 | 개선 | 적용 시기 |` and count pipes. The slide
   * was right there and could not be touched.
   *
   * Edits are handed back as a whole slide, and the panel turns that back into
   * the same lines the textarea holds — so the two are one draft and `save()`
   * did not have to learn anything new.
   */
  editable?: boolean
  onEdit?: (next: Slide) => void
  selectedElement?: SlideElement | null
  onSelectElement?: (element: SlideElement) => void
  onOverflow?: (overflowing: boolean) => void
}) {
  const t = useT()
  /*
   * The slide as it is being typed.
   *
   * Held in a ref rather than in state: re-rendering a `contentEditable` while
   * somebody is inside it moves the caret to the front, and the browser is
   * already holding the characters. What this accumulates is the *other*
   * fields — edit the title, then a bullet, and the second edit has to carry
   * the first or it would hand back a slide with the old title.
   */
  const working = useRef(slide)
  const canvas = useRef<HTMLDivElement>(null)
  working.current = editable ? { ...working.current, ...pick(slide, working.current) } : slide
  const edit = (patch: Partial<Slide>) => {
    working.current = { ...working.current, ...patch }
    onEdit?.(working.current)
  }
  const selectable = (element: SlideElement) => editable
    ? ({
        onPointerDown: (event: React.PointerEvent<HTMLElement>) => {
          event.stopPropagation()
          onSelectElement?.(element)
        },
        'data-slide-element': element,
        'data-selected': selectedElement === element ? 'true' : undefined,
      } as const)
    : {}
  /** What a `contentEditable` needs to be one, and nothing when it is not. */
  const typed = (key: string, read: (text: string) => Partial<Slide>) =>
    editable
      ? ({
          contentEditable: true,
          suppressContentEditableWarning: true,
          spellCheck: false,
          onBlur: (e: React.FocusEvent<HTMLElement>) => {
            const html = cleanInlineHtml(e.currentTarget.innerHTML)
            const richText = { ...(working.current.richText ?? {}) }
            if (html === (e.currentTarget.textContent ?? '')) delete richText[key]
            else richText[key] = html
            edit({ ...read(e.currentTarget.textContent ?? ''), richText: Object.keys(richText).length ? richText : undefined })
          },
          className: 'outline-none focus:bg-accent-soft/40',
        } as const)
      : {}
  const rich = (key: string, text: string) => {
    const html = slide.richText?.[key]
    return html && inlineText(html) === text
      ? <span dangerouslySetInnerHTML={{ __html: cleanInlineHtml(html) }} />
      : text
  }
  const visualStyle = brand?.visualStyle ?? 'editorial'
  const accent = slide.accent ?? brand?.accent ?? 'var(--accent)'
  const px = (n: number) => `${n * scale}px`
  /**
   * Type, which a person can make bigger or smaller on one slide.
   *
   * Separate from `px` on purpose: the ask was for the words, and growing the
   * padding and the gaps with them would only push the same amount of text off
   * the same edge. The gutter is the 서식's decision and stays where it is.
   *
   * `deck_export` multiplies its own sizes by the same number, so the `.pptx`
   * and the `.pdf` come out the size the screen showed. A control that only
   * changed the preview would be worse than no control.
   */
  const type = (n: number) => `${n * scale * (slide.textScale ?? 1)}px`
  /*
   * Every surface is a mix of the slide's own accent, so one deck in green and
   * one in navy are the same design rather than the same design plus a blue
   * table. `deck_export` computes the identical mixes in Python and draws them
   * into the .pptx and .pdf — see `_mix` there. Change a percentage here and
   * change it there, or the room sees a different deck from the panel.
   */
  const tint = `color-mix(in srgb, ${accent} 7%, #fff)`
  const hair = '#e6e6e6'
  const rows = slide.rows ?? []
  const metrics = slide.metrics ?? []
  /* The three shapes that are a left thing and a right thing. Read as one
     because they are one — see `Slide.bands`. `deck_export` draws the same
     three at the same measurements, so the room sees what the panel showed. */
  const paired = pairedLayout(slide)
  const pairs = (paired ? (slide[paired] ?? []) : []).filter(
    ([left, right]) => left?.trim() && right?.trim(),
  )
  const chart = slide.chart
  const contentElement: SlideElement = chart
    ? 'chart'
    : rows.length
      ? 'table'
      : metrics.length
        ? 'metrics'
        : pairs.length
          ? 'cards'
          : 'content'
  const pending = !hasContent(slide)
  useLayoutEffect(() => {
    if (!onOverflow || !canvas.current) return
    const root = canvas.current
    const measure = () => {
      const boxes = [root, ...Array.from(root.querySelectorAll<HTMLElement>('[data-overflow-box]'))]
      onOverflow(boxes.some((box) => box.scrollHeight > box.clientHeight + 2 || box.scrollWidth > box.clientWidth + 2))
    }
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(root)
    return () => observer.disconnect()
  }, [onOverflow, slide])
  // Two columns are only two columns when there is enough to fill them; four
  // bullets split in half reads as a mistake.
  const twoColumn = slide.layout === 'two-column' && (slide.bullets?.length ?? 0) >= 5
  /*
   * How tight the table has to be to stay on the slide.
   *
   * A slide is 225 units tall in this drawing and the body gets about 125 of
   * them. Seven rows at one comfortable size is 190, so the table ran off the
   * bottom edge and through the footer — which is exactly what a filled head
   * row makes obvious, because the overflow now has a colour. The row count is
   * known before anything is drawn, so the size follows it rather than the
   * slide losing its last row. `deck_export` scales the same way.
   */
  const dense = (() => {
    // What is left under the title once the head band, the title, the tab and
    // the foot have taken theirs, in this drawing's 225 units.
    const body = 122
    // One row in reserve for the cell that wraps to two lines — 시스템 전역
    // 또는 프로젝트 does, in a column sized for 도구.
    const perRow = body / (rows.length + 1.2)
    const size = Math.max(7.5, Math.min(12, perRow / 2.05))
    return { size, pad: Math.max(2, (perRow - size * 1.4) / 2) }
  })()
  /*
   * The same arithmetic for the two paired shapes that stack down the slide.
   *
   * They were drawn at one size whatever they held: a band was three lines of
   * padding and type tall and there could be six of them, so a four-band slide
   * ran through the footer and a seven-entry timeline lost its last two
   * entries off the bottom edge. Nothing said so — the panel clips, and the
   * .pptx is where somebody finds out.
   *
   * `deck_export` divides the same way (`min(72, room/n)` for a band,
   * `min(56, room/n)` for a timeline step, in its 540-unit slide); these are
   * those two numbers in this drawing's 225, so the room sees the slide the
   * panel showed. `tiles` is not here: it lays its marks across rather than
   * down, and flex already shares the width between them.
   */
  const stack = (() => {
    const body = 122
    const count = Math.max(pairs.length, 1)
    // A band and the 10-unit gap under it, capped where the export caps them.
    const gap = 4
    const height = Math.min(30, (body - gap * (count - 1)) / count)
    const band = Math.max(7, Math.min(10, height / 3))
    // A timeline entry is a line of type and the air under it, no box.
    const step = Math.min(23, body / count)
    const line = Math.max(7, Math.min(10, step / 2.3))
    /**
     * 왼쪽 이름표의 폭 — 글자 수에서 잰다.
     *
     * It was 52 units, fixed, and the shape was designed for a date. The
     * outline prompt then learned to reach for `timeline` on 절차·단계·로드맵
     * too, where the left column holds a phrase — 「개편 방향 확정」 — and a
     * phrase in a date's width wraps to three lines inside a row that is
     * `overflow-hidden` and 23 units tall. The label came out sliced in half
     * horizontally, which is what a person sees as 「개편 방향 확」.
     *
     * A Korean glyph is about one em wide, so the characters say what the
     * column needs. Bounded at both ends: never narrower than the date it was
     * built for, never wider than a third of the slide, because past that the
     * label has taken the space the entry was supposed to explain itself in.
     */
    const widest = (index: number, floor: number, size: number) =>
      Math.round(
        Math.max(
          floor,
          Math.min(
            96,
            Math.max(0, ...pairs.map(([...pair]) => (pair[index] ?? '').length)) * size * 0.95,
          ),
        ),
      )

    return {
      gap,
      height,
      band,
      pad: Math.max(1.5, (height - band * 1.5) / 2),
      step,
      line,
      bandLabel: widest(0, 62, band),
      stepLabel: widest(0, 52, line),
    }
  })()

  /**
   * The cover, and every 장 that opens a section.
   *
   * Reversed out of the accent rather than set on white. A title slide has one
   * job — say what this is before anybody reads a word of it — and the deck
   * that came before this one opened on a white rectangle with a 4px stripe
   * down the edge, which is the same rectangle the seventeen slides behind it
   * were on. The block is the only thing here that is not type, and it is what
   * makes a deck look like a deck at a glance.
   */
  if (COVERS.includes(slide.layout)) {
    const closing = slide.layout === 'closing'
    return (
      <div
        ref={canvas}
        className="relative flex size-full flex-col justify-center overflow-hidden"
        style={{
          /* A wash rather than a flat field. One accent across a whole slide is
             a printed rectangle; the same accent falling half a step is what a
             deck made by somebody with a template looks like — and it is mixed
             from the accent, so it follows whatever hue is set rather than
             pinning a second colour beside it. `deck_export` mixes the same
             62% onto the ink. */
          background: visualStyle === 'minimal'
              ? `linear-gradient(145deg, color-mix(in srgb, ${accent} 10%, #fff), #fff 70%)`
              : `linear-gradient(135deg, ${accent}, color-mix(in srgb, ${accent} ${visualStyle === 'poster' ? 48 : 62}%, #111827))`,
          padding: px(34),
          ...KOREAN_WRAP,
        }}
      >
        {visualStyle === 'poster' && <div className="absolute rounded-full border border-white/20" style={{ width: px(150), height: px(150), right: px(-35), top: px(-45) }} />}
        {slide.layout === 'section' && slide.number ? (
          /* `01.` over the title. A divider that only names the part leaves
             the reader counting backwards to place it. */
          <div
            style={{
              fontSize: type(15),
              fontWeight: 700,
              color: visualStyle === 'minimal' ? accent : 'rgba(255,255,255,0.7)',
              marginBottom: px(6),
            }}
          >
            {slide.number}
          </div>
        ) : (
          <div
            style={{
              width: px(44),
              height: px(3),
              background: visualStyle === 'minimal' ? accent : 'rgba(255,255,255,0.9)',
              marginBottom: px(18),
            }}
          />
        )}
        <h3
          style={{ fontSize: type(closing ? 24 : visualStyle === 'poster' ? 30 : 27), fontWeight: visualStyle === 'minimal' ? 600 : 750, lineHeight: 1.2, color: visualStyle === 'minimal' ? '#1a1a1a' : '#fff', maxWidth: visualStyle === 'editorial' ? '78%' : undefined }}
          {...typed('title', (text) => ({ title: text }))}
          {...selectable('title')}
        >
          {rich('title', slide.title || (closing ? t('마무리') : ''))}
        </h3>
        {closing && slide.bullets && slide.bullets.length > 0 && (
          /* What to remember, under the title: a dash and a line each. The
             farewell goes at the foot, below, set larger. */
          <ul style={{ marginTop: px(12), fontSize: type(12), lineHeight: 1.6, color: visualStyle === 'minimal' ? '#333' : 'rgba(255,255,255,0.92)' }}>
            {slide.bullets.slice(0, 3).map((b, i) => (
              <li key={i} className="flex gap-2">
                <span style={{ color: visualStyle === 'minimal' ? accent : 'rgba(255,255,255,0.6)' }}>—</span>
                <span
                  {...typed(`bullets.${i}`, (text) => ({
                    bullets: (working.current.bullets ?? []).map((old, at) => (at === i ? text : old)),
                  }))}
                >
                  {rich(`bullets.${i}`, b)}
                </span>
              </li>
            ))}
          </ul>
        )}
        {closing && slide.body ? (
          <p
            className="absolute"
            style={{ left: px(34), bottom: px(30), fontSize: type(15), fontWeight: 700, color: visualStyle === 'minimal' ? accent : '#fff' }}
            {...typed('body', (text) => ({ body: text }))}
          >
            {rich('body', slide.body)}
          </p>
        ) : slide.body && (
          <p
            style={{
              fontSize: type(13),
              marginTop: px(12),
              lineHeight: 1.5,
              color: visualStyle === 'minimal' ? '#666' : 'rgba(255,255,255,0.8)',
            }}
            {...typed('body', (text) => ({ body: text }))}
          >
            {rich('body', slide.body)}
          </p>
        )}
      </div>
    )
  }

  return (
    <div
      ref={canvas}
      className="relative flex size-full flex-col overflow-hidden text-[#1a1a1a]"
      style={{
        background: visualStyle === 'poster' ? '#f7f3ed' : '#fff',
        paddingTop: px(24),
        paddingLeft: px(28),
        paddingRight: px(28),
        // Room for the footer, which is drawn against the bottom edge.
        paddingBottom: px(28),
        ...KOREAN_WRAP,
      }}
    >
      {/* The band across the head. Where the 4px stripe down the left edge
          used to be: a rule that stands up is read as a margin mark, and one
          that lies across the top is read as the top of a slide. */}
      {visualStyle === 'editorial' && <div className="absolute inset-x-0 top-0" style={{ height: px(6), background: accent }} />}
      {visualStyle === 'poster' && <div className="absolute inset-y-0 left-0" style={{ width: px(8), background: accent }} />}
      {visualStyle === 'minimal' && <div className="absolute rounded-full" style={{ width: px(70), height: px(70), right: px(-30), top: px(-35), background: `color-mix(in srgb, ${accent} 12%, transparent)` }} />}
      {visualStyle === 'poster' && index !== undefined && <span className="absolute font-black tabular-nums" style={{ right: px(22), top: px(13), fontSize: type(28), color: `color-mix(in srgb, ${accent} 14%, transparent)` }}>{String(index + 1).padStart(2, '0')}</span>}

      {slide.layout === 'statement' ? (
        /* One phrase set large in the middle, a short rule over it and the
           sentence that unpacks it under — the presenter's own conclusion. */
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <div style={{ width: px(26), height: px(2), background: accent, marginBottom: px(14) }} />
          <p
            style={{ fontSize: type(26), fontWeight: 750, lineHeight: 1.25, color: accent, maxWidth: '86%' }}
            {...typed('title', (text) => ({ title: text }))}
            {...selectable('title')}
          >
            {rich('title', slide.title)}
          </p>
          {slide.body && (
            <p
              style={{ fontSize: type(11), marginTop: px(10), color: '#555', lineHeight: 1.5, maxWidth: '74%' }}
              {...typed('body', (text) => ({ body: text }))}
            >
              {rich('body', slide.body)}
            </p>
          )}
        </div>
      ) : slide.layout === 'quote' && slide.body ? (
        <div className="flex flex-1 flex-col justify-center">
          <p style={{ fontSize: type(20), fontWeight: 600, lineHeight: 1.4, color: accent }}>
            “<span {...typed('body', (text) => ({ body: text }))}>{rich('body', slide.body)}</span>”
          </p>
          <p
            style={{ fontSize: type(12), marginTop: px(10), color: '#666' }}
            {...typed('title', (text) => ({ title: text }))}
          >
            {rich('title', slide.title)}
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <h3
            style={{ fontSize: type(18), fontWeight: 700, lineHeight: 1.25 }}
            {...typed('title', (text) => ({ title: text }))}
            {...selectable('title')}
          >
            {rich('title', slide.title)}
          </h3>
          {/* The tab under the title. Two by twenty-six, and the only accent
              on a slide of prose — enough that the eye finds the same corner
              on every 장, not enough to compete with the words. */}
          <div
            style={{
              width: px(26),
              height: px(2),
              background: accent,
              marginTop: px(8),
              marginBottom: px(14),
            }}
          />
          {/* Words left, picture right — the geometry `deck_export` uses, so
              the preview and the .pptx put them in the same places. */}
          <div className="flex min-h-0 flex-1" style={{ gap: px(16) }}>
            <div
              className={cn(
                'flex min-w-0 flex-1 flex-col',
                // Centred only for the three paired shapes. `stack` divides the
                // room between their entries, so they cannot overflow — and a
                // two-band slide otherwise sat in the top half with the bottom
                // half blank, which reads as a slide that failed to finish
                // rather than one with two things to say. Bullets are left
                // alone: they can outgrow the box, and content that overflows a
                // centred column is clipped at both ends instead of the bottom.
                PAIRED.includes(slide.layout as (typeof PAIRED)[number]) && 'justify-center',
              )}
              data-overflow-box
              style={{ order: slide.image?.position === 'left' ? 2 : 1 }}
              {...selectable(contentElement)}
            >
              {pairs.length > 0 && slide.layout === 'bands' && (
                /* A filled name on the left against a tinted band on the right.
                   Bullets have nowhere to put the name of what they are, which
                   is the whole of why this shape exists. */
                <div className="flex flex-col" style={{ gap: px(stack.gap) }}>
                  {pairs.map(([name, text], i) => (
                    <div
                      key={i}
                      className="flex items-stretch overflow-hidden"
                      style={{ gap: px(5), height: px(stack.height) }}
                    >
                      <div
                        className="grid shrink-0 place-items-center text-center"
                        style={{
                          width: px(stack.bandLabel),
                          background: accent,
                          color: '#fff',
                          fontSize: type(stack.band),
                          fontWeight: 700,
                          padding: `${px(stack.pad)} ${px(4)}`,
                        }}
                      >
                        {name}
                      </div>
                      <div
                        className="flex min-w-0 flex-1 items-center"
                        style={{
                          background: tint,
                          fontSize: type(stack.band),
                          lineHeight: 1.5,
                          padding: `${px(stack.pad)} ${px(12)}`,
                        }}
                      >
                        {text}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'tiles' && (
                /* The mark set large in a filled square, its name under it. */
                <div className="flex" style={{ gap: px(11), marginTop: px(8) }}>
                  {pairs.map(([mark, name], i) => (
                    <div key={i} className="flex min-w-0 flex-1 flex-col items-center">
                      <div
                        className="grid aspect-square w-full place-items-center"
                        style={{
                          maxWidth: px(62),
                          background: accent,
                          color: '#fff',
                          fontSize: type(26),
                          fontWeight: 700,
                        }}
                      >
                        {mark}
                      </div>
                      <div
                        className="text-center"
                        style={{ fontSize: type(9), marginTop: px(7), color: '#666' }}
                      >
                        {name}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'steps' && (
                /* Numbered by position, across the slide: a filled square with
                   the number, the step's name under, what it does under that,
                   and one tinted rule joining the squares left to right. */
                <div className="relative flex" style={{ gap: px(8), marginTop: px(8) }}>
                  {pairs.length > 1 && (
                    <div className="absolute" style={{ left: px(11), right: `calc(${100 / pairs.length}% - ${px(11)})`, top: px(10), height: px(1), background: tint }} />
                  )}
                  {pairs.map(([name, text], i) => (
                    <div key={i} className="relative flex min-w-0 flex-1 flex-col">
                      <div
                        className="grid place-items-center"
                        style={{ width: px(22), height: px(22), background: accent, color: '#fff', fontSize: type(9), fontWeight: 700 }}
                      >
                        {String(i + 1).padStart(2, '0')}
                      </div>
                      <div style={{ fontSize: type(10), fontWeight: 700, marginTop: px(7), lineHeight: 1.3 }}>{name}</div>
                      <div style={{ fontSize: type(8), marginTop: px(3), color: '#666', lineHeight: 1.5 }}>{text}</div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'cards' && (
                /* Titled boxes side by side: a tinted card with an accent rule
                   over it, the name at the top and the text under it. */
                <div className="flex" style={{ gap: px(8), marginTop: px(4), height: px(100) }}>
                  {pairs.map(([name, text], i) => (
                    <div
                      key={i}
                      className="flex min-w-0 flex-1 flex-col overflow-hidden"
                      style={{ background: tint, borderTop: `${px(2)} solid ${accent}`, padding: `${px(8)} ${px(7)}` }}
                    >
                      <div style={{ fontSize: type(10.5), fontWeight: 700, color: accent, lineHeight: 1.3 }}>{name}</div>
                      <div style={{ fontSize: type(8.5), marginTop: px(5), lineHeight: 1.5 }}>{text}</div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'timeline' && (
                /* Hung off one rule: the date to its left, what happened to its
                   right. Order is the point, so nothing here reflows it. */
                <div className="flex flex-col">
                  {pairs.map(([when, what], i) => (
                    <div
                      key={i}
                      className="flex overflow-hidden"
                      style={{ gap: px(9), height: px(stack.step) }}
                    >
                      <div
                        className="shrink-0 text-right"
                        style={{
                          width: px(stack.stepLabel),
                          color: accent,
                          fontSize: type(stack.line),
                          fontWeight: 700,
                          paddingTop: px(2),
                          // Tight, so a two-line label still clears the row.
                          lineHeight: 1.2,
                        }}
                      >
                        {when}
                      </div>
                      <div className="relative flex shrink-0 flex-col items-center">
                        <div
                          style={{
                            width: px(5),
                            height: px(5),
                            marginTop: px(4),
                            background: accent,
                            borderRadius: '50%',
                          }}
                        />
                        {i < pairs.length - 1 && (
                          <div className="flex-1" style={{ width: px(1), background: tint }} />
                        )}
                      </div>
                      <div
                        className="min-w-0 flex-1"
                        style={{
                          fontSize: type(stack.line),
                          lineHeight: 1.5,
                          paddingBottom: px(6),
                        }}
                      >
                        {what}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {chart && <SlideChart chart={chart} accent={accent} scale={scale} />}
              {metrics.length > 0 && slide.layout === 'big-number' && (
                /* One figure, very large, its name beside it, and the line
                   that says what it means under both. */
                <div className="flex flex-1 flex-col justify-center">
                  <div className="flex items-baseline" style={{ gap: px(8) }}>
                    <span
                      style={{ fontSize: type(46), fontWeight: 750, lineHeight: 1, color: accent }}
                      {...typed('metrics.0.0', (text) => ({ metrics: [[text, working.current.metrics?.[0]?.[1] ?? '']] }))}
                    >
                      {rich('metrics.0.0', metrics[0][0])}
                    </span>
                    <span
                      style={{ fontSize: type(11), color: '#666' }}
                      {...typed('metrics.0.1', (text) => ({ metrics: [[working.current.metrics?.[0]?.[0] ?? '', text]] }))}
                    >
                      {rich('metrics.0.1', metrics[0][1])}
                    </span>
                  </div>
                  {slide.body && (
                    <p style={{ fontSize: type(11), marginTop: px(10), lineHeight: 1.5 }} {...typed('body', (text) => ({ body: text }))}>
                      {rich('body', slide.body)}
                    </p>
                  )}
                </div>
              )}
              {metrics.length > 0 && slide.layout !== 'big-number' && (
                /* One card each: the figure large, what it counts under it, and
                   a rule over the top in the accent. Set on the open slide they
                   were three numbers floating in a white field; carded, the eye
                   reads them as one row of comparable things. The same shape
                   `deck_export` draws into the .pptx and .pdf. */
                <div className="flex" style={{ gap: px(12), marginTop: px(6) }}>
                  {metrics.map(([figure, label], i) => (
                    <div
                      key={i}
                      className="min-w-0 flex-1"
                      style={{
                        background: tint,
                        borderTop: `${px(2)} solid ${accent}`,
                        padding: `${px(14)} ${px(14)} ${px(16)}`,
                      }}
                    >
                      <div
                        style={{
                          fontSize: type(30),
                          fontWeight: 700,
                          lineHeight: 1.1,
                          color: accent,
                        }}
                        {...typed(`metrics.${i}.0`, (text) => ({
                          metrics: (working.current.metrics ?? []).map((m, at) =>
                            at === i ? ([text, m[1]] as [string, string]) : m,
                          ),
                        }))}
                      >
                        {rich(`metrics.${i}.0`, figure)}
                      </div>
                      <div
                        style={{ fontSize: type(11), marginTop: px(5), color: '#666' }}
                        {...typed(`metrics.${i}.1`, (text) => ({
                          metrics: (working.current.metrics ?? []).map((m, at) =>
                            at === i ? ([m[0], text] as [string, string]) : m,
                          ),
                        }))}
                      >
                        {rich(`metrics.${i}.1`, label)}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {rows.length > 0 && (
                /* Clipped rather than allowed to run: a table one row too long
                   used to draw straight through the foot, and a slide whose
                   page number has a table row across it reads as a broken
                   export rather than a long table. */
                <div className="min-h-0 overflow-hidden">
                {/* The head row filled and reversed out, the body banded in the
                    faintest tint of the same accent, hairlines between and
                    nothing round the outside. A slide table is read at eight
                    metres: the head has to be a block of colour rather than
                    coloured words, and a full grid at that distance is a grey
                    blur. Kept in step with `deck_export`. */}
                <table
                  style={{
                    fontSize: type(dense.size),
                    lineHeight: 1.4,
                    width: '100%',
                    borderCollapse: 'collapse',
                    tableLayout: 'fixed',
                  }}
                >
                  <tbody>
                    {rows.map((row, r) => (
                      <tr
                        key={r}
                        style={{
                          background: r === 0 ? accent : r % 2 === 0 ? tint : 'transparent',
                          borderBottom: r === 0 ? 'none' : `1px solid ${hair}`,
                        }}
                      >
                        {row.map((cell, c) => (
                          <td
                            key={c}
                            style={{
                              padding: `${px(dense.pad)} ${px(9)}`,
                              verticalAlign: 'top',
                              wordBreak: 'keep-all',
                              fontWeight: r === 0 || c === 0 ? 600 : 400,
                              color: r === 0 ? '#fff' : '#1a1a1a',
                            }}
                            {...typed(`rows.${r}.${c}`, (text) => ({
                              rows: (working.current.rows ?? []).map((row2, ri) =>
                                ri === r ? row2.map((cell2, ci) => (ci === c ? text : cell2)) : row2,
                              ),
                            }))}
                          >
                            {rich(`rows.${r}.${c}`, cell)}
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
                </div>
              )}
              {slide.bullets && slide.layout === 'agenda' && (
                /* The 목차: `01` in the accent beside each name, down one
                   column, or two when there are more than four. */
                <ol
                  style={{
                    fontSize: type(11),
                    ...(slide.bullets.length > 4 ? { columnCount: 2, columnGap: px(20) } : null),
                  }}
                >
                  {slide.bullets.map((b, i) => (
                    <li
                      key={i}
                      className="flex items-baseline"
                      style={{ gap: px(10), padding: `${px(5)} 0`, borderBottom: `1px solid ${hair}`, breakInside: 'avoid' }}
                    >
                      <span className="tabular-nums" style={{ color: accent, fontWeight: 700, fontSize: type(13) }}>
                        {String(i + 1).padStart(2, '0')}
                      </span>
                      <span
                        {...typed(`bullets.${i}`, (text) => ({
                          bullets: (working.current.bullets ?? []).map((old, at) => (at === i ? text : old)),
                        }))}
                      >
                        {rich(`bullets.${i}`, b)}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
              {slide.bullets &&
                slide.layout !== 'agenda' &&
                rows.length === 0 &&
                metrics.length === 0 &&
                pairs.length === 0 &&
                !chart && (
                <ul
                  style={{
                    fontSize: type(11.5),
                    lineHeight: 1.65,
                    // A long list down one edge wastes the right half of the
                    // rectangle and pushes the last item off the bottom.
                    // Splitting it is the same content, read in the shape it
                    // fits.
                    ...(twoColumn ? { columnCount: 2, columnGap: px(20) } : null),
                  }}
                >
                  {slide.bullets.map((b, i) => (
                    <li key={i} className="flex gap-2" style={{ breakInside: 'avoid' }}>
                      <span style={{ color: accent }}>•</span>
                      <span
                        {...typed(`bullets.${i}`, (text) => ({
                          bullets: (working.current.bullets ?? []).map((old, at) =>
                            at === i ? text : old,
                          ),
                        }))}
                      >
                        {rich(`bullets.${i}`, b)}
                      </span>
                    </li>
                  ))}
                </ul>
              )}
              {slide.body &&
                !slide.bullets?.length &&
                /* Not on top of a slide that has something on it. A `bands`
                   slide whose prose failed still has four filled bands, and
                   the sentence saying it was not written was drawn under
                   them. */
                pairs.length === 0 &&
                rows.length === 0 &&
                metrics.length === 0 &&
                !chart && (
                <p
                  style={{ fontSize: type(11), color: '#555', marginTop: px(2), lineHeight: 1.6 }}
                  {...typed('body', (text) => ({ body: text }))}
                >
                  {rich('body', slide.body)}
                </p>
              )}
              {/* 빈 장. 흰 화면만 두면 다 만들어진 것처럼 보인다 */}
              {pending && !slide.image && (
                <p style={{ fontSize: type(12), color: '#aaa', marginTop: px(6) }}>
                  {writing ? t('쓰는 중…') : t('내용이 비었습니다 — 텍스트 수정으로 채워 주세요.')}
                </p>
              )}
            </div>
            {slide.image?.src && (
              <div
                className={cn('flex min-h-0 shrink-0 flex-col justify-center overflow-hidden', selectedElement === 'image' && 'ring-2 ring-accent ring-offset-2')}
                style={{ width: pending ? '100%' : ({ small: '32%', medium: '42%', large: '54%' }[slide.image.size ?? 'medium']), order: slide.image.position === 'left' ? 1 : 2 }}
                {...selectable('image')}
              >
                <img
                  src={slide.image.src}
                  alt={slide.image.caption || t('그림')}
                  className={cn(
                    'min-h-0 w-full',
                    slide.image.fit === 'cover' ? 'flex-1 object-cover' : 'max-h-full object-contain',
                  )}
                />
                {slide.image.caption && (
                  <p style={{ fontSize: type(10), color: '#666', marginTop: px(4) }}>
                    {slide.image.caption}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

      {/* The foot. What deck this is on the left, where you are in it on the
          right — the two things somebody asks about from the floor. The rail
          passes the same values, so its preview is a scaled copy rather than a
          different, footerless slide. */}
      {index !== undefined && total !== undefined && (
        <div
          className="absolute flex items-center justify-between"
          style={{
            left: px(28),
            right: px(28),
            bottom: px(10),
            paddingTop: px(7),
            borderTop: `1px solid ${hair}`,
          }}
        >
          <span className="flex min-w-0 items-center" style={{ gap: px(6) }}>
            {brand?.logo && (
              <img
                src={brand.logo}
                alt=""
                className="shrink-0 object-contain"
                style={{ height: px(9), maxWidth: px(50) }}
              />
            )}
            <span
              className="min-w-0 truncate"
              style={{ fontSize: type(8), letterSpacing: px(0.3), color: '#8a8a8a' }}
            >
              {deckTitle}
            </span>
          </span>
          {brand?.footer && (
            /* Whose deck it is, opposite its name. A deck presented outside the
               room it was made in is read as belonging to whoever made it. */
            <span
              className="min-w-0 truncate"
              style={{
                fontSize: type(8),
                letterSpacing: px(0.3),
                color: '#8a8a8a',
                marginLeft: px(8),
                marginRight: px(8),
              }}
            >
              {brand.footer}
            </span>
          )}
          <span
            className="grid shrink-0 place-items-center"
            style={{
              minWidth: px(15),
              height: px(15),
              padding: `0 ${px(4)}`,
              background: accent,
              color: '#fff',
              fontSize: type(8),
              fontWeight: 700,
            }}
          >
            {index + 1}
          </span>
        </div>
      )}
    </div>
  )
}

/**
 * Putting a picture on a slide of a JSON deck.
 *
 * The same path an HTML document has, on the track that never was HTML: the
 * picture was made on the image surface and the server embeds it as a `data:`
 * URI, so the deck stays one thing that previews, presents and exports with
 * the picture in it.
 */
function SlidePicture({ deck, slide }: { deck: DeckArtifact; slide: Slide }) {
  const t = useT()
  const [open, setOpen] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [caption, setCaption] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const loadArtifacts = useStore((s) => s.loadArtifacts)
  const refreshArtifact = useStore((s) => s.refreshArtifact)

  const insert = async () => {
    if (!picked) return
    setBusy(true)
    setError(null)
    try {
      await artifactsApi.addSlideImage(deck.id, slide.id, picked, caption.trim())
      await refreshArtifact(deck.id)
      await loadArtifacts()
      setOpen(false)
      setPicked(null)
      setCaption('')
    } catch (err) {
      setError(errorMessage(err, t('그림을 넣지 못했습니다.')))
    } finally {
      setBusy(false)
    }
  }

  return (
    <>
      <Button
        variant="ghost"
        size="sm"
        onClick={() => {
          setPicked(null)
          setCaption('')
          setError(null)
          setOpen(true)
        }}
      >
        <ImagePlus size={13} />
        {slide.image ? t('그림 바꾸기') : t('그림 넣기')}
      </Button>

      <Modal
        open={open}
        onClose={() => setOpen(false)}
        title={t('{name}에 그림 넣기').replace('{name}', slide.title || t('이 장'))}
        description={t('여기서 바로 만들거나 이미 만든 그림을 고를 수 있습니다. 링크가 아니라 파일 안에 담기므로 인쇄와 공유에서도 함께 보입니다.')}
        footer={
          <>
            <Button onClick={() => setOpen(false)} disabled={busy}>
              {t('취소')}
            </Button>
            <Button variant="primary" onClick={() => void insert()} disabled={busy || !picked}>
              {busy ? t('넣는 중…') : t('넣기')}
            </Button>
          </>
        }
      >
        <PicturePicker
          sessionId={deck.sessionId}
          aspect="16:9"
          picked={picked}
          onPick={setPicked}
          caption={caption}
          onCaption={setCaption}
          about={slide.title || t('이 장')}
          title={deck.title}
          /* What this 장 already says, so the suggestion draws what the words
             cannot rather than illustrating them a second time. */
          context={[
            slide.body,
            ...(slide.bullets ?? []),
            ...(slide.rows ?? []).map((row) => row.join(' · ')),
            ...(slide.metrics ?? []).map(([value, label]) => `${value} — ${label}`),
          ]
            .filter(Boolean)
            .join('\n')}
        />
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/**
 * The editable text of a slide: the title, then whatever the slide is made of.
 *
 * A table row comes out as `| 구분 | 탐지 |`, the shape anybody who has written
 * Markdown already knows, and goes back in the same way. Before this, a slide's
 * table was simply absent from the box: somebody opened 텍스트 수정 on a table
 * slide, saw a title and nothing else, typed the lines they wanted, and saved.
 * The save turned the slide into `bullets` — and `SlideView` draws bullets only
 * where `rows` is empty, so the table stayed and every word they typed was
 * swallowed with no error and no trace.
 *
 * Metrics go out the same way, as `| 99.5% | 대응률 |`, so a KPI strip is
 * editable for the first time as well.
 *
 * So do the three paired layouts, one pair a line: a band's name and its text,
 * a tile's mark and its caption, a date and what happened. They were the same
 * bug the table had and worse — absent from the box, and `save` then rebuilt
 * the slide as `bullets`, so opening 텍스트 수정 on a `bands` slide and pressing
 * 저장 without typing a character emptied it and PATCHed that over the deck.
 *
 * A paired slide's `bullets` and `body` are left out on purpose: `SlideView`
 * draws neither while there are pairs, and putting invisible text in the box
 * invites somebody to edit words that will never appear.
 */
function toLines(slide: Slide): string {
  const rows = (slide.rows ?? []).map((row) => `| ${row.join(' | ')} |`)
  const metrics = (slide.metrics ?? []).map(([figure, label]) => `| ${figure} | ${label} |`)
  const paired = pairedLayout(slide)
  const pairs = paired
    ? (slide[paired] ?? []).map(([left, right]) => `| ${left} | ${right} |`)
    : []
  return [
    slide.title,
    ...(paired ? [] : (slide.bullets ?? [])),
    paired ? '' : (slide.body ?? ''),
    ...rows,
    ...metrics,
    ...pairs,
  ]
    .filter(Boolean)
    .join('\n')
}

/** `| a | b |` → `['a', 'b']`, or `null` for a line that is not a row. */
function toCells(line: string): string[] | null {
  const trimmed = line.trim()
  if (!trimmed.startsWith('|')) return null
  const cells = trimmed.replace(/^\|/, '').replace(/\|$/, '').split('|').map((c) => c.trim())
  // `| --- | --- |` is a Markdown rule, not data. Somebody pasting a table
  // from elsewhere brings one with them.
  if (cells.every((c) => /^:?-{2,}:?$/.test(c))) return null
  return cells.length > 1 ? cells : null
}

function SlideDataEditor({ slide, onChange }: { slide: Slide; onChange: (next: Slide) => void }) {
  const t = useT()
  const paired = pairedLayout(slide)
  const pairs = paired ? slide[paired] ?? [] : slide.metrics ?? []
  const pairLabels = paired === 'timeline'
    ? ['시점', '내용']
    : slide.layout === 'metrics' || slide.layout === 'big-number'
      ? ['수치', '설명']
      : paired === 'tiles'
        ? ['표식', '설명']
        : paired === 'steps'
          ? ['단계', '내용']
          : ['항목', '설명']

  if (slide.layout === 'table') {
    const rows = slide.rows ?? []
    const columns = Math.max(1, ...rows.map((row) => row.length))
    const write = (next: string[][]) => onChange({ ...slide, rows: next })
    return (
      <div className="space-y-2 rounded-card border border-line bg-panel p-3">
        <div className="flex items-center justify-between gap-2">
          <p className="text-xs font-semibold text-muted">{t('표 데이터')}</p>
          <div className="flex gap-1">
            <Button size="sm" onClick={() => write([...rows, Array(columns).fill('')])}>{t('행 추가')}</Button>
            <Button size="sm" onClick={() => write(rows.map((row) => [...row, '']))}>{t('열 추가')}</Button>
          </div>
        </div>
        {rows.map((row, rowIndex) => (
          <div key={rowIndex} className="flex items-center gap-1">
            <span className="w-5 shrink-0 text-right text-2xs text-faint">{rowIndex + 1}</span>
            {Array.from({ length: columns }, (_, columnIndex) => (
              <Input
                key={columnIndex}
                aria-label={t('{row}행 {column}열').replace('{row}', String(rowIndex + 1)).replace('{column}', String(columnIndex + 1))}
                value={row[columnIndex] ?? ''}
                onChange={(event) => write(rows.map((old, r) => r === rowIndex
                  ? Array.from({ length: columns }, (_, c) => c === columnIndex ? event.target.value : old[c] ?? '')
                  : old))}
              />
            ))}
            <Button size="sm" disabled={rows.length <= 1} onClick={() => write(rows.filter((_, r) => r !== rowIndex))}>
              {t('삭제')}
            </Button>
            <Button aria-label={t('{n}행 위로').replace('{n}', String(rowIndex + 1))} size="sm" disabled={rowIndex === 0} onClick={() => { const next = [...rows]; [next[rowIndex - 1], next[rowIndex]] = [next[rowIndex], next[rowIndex - 1]]; write(next) }}><ArrowUp size={13} /></Button>
            <Button aria-label={t('{n}행 아래로').replace('{n}', String(rowIndex + 1))} size="sm" disabled={rowIndex === rows.length - 1} onClick={() => { const next = [...rows]; [next[rowIndex], next[rowIndex + 1]] = [next[rowIndex + 1], next[rowIndex]]; write(next) }}><ArrowDown size={13} /></Button>
          </div>
        ))}
        <Button
          size="sm"
          disabled={columns <= 1}
          onClick={() => write(rows.map((row) => row.slice(0, -1)))}
        >
          {t('마지막 열 삭제')}
        </Button>
      </div>
    )
  }

  if (!paired && slide.layout !== 'metrics') return null
  const writePairs = (next: [string, string][]) => onChange({
    ...slide,
    ...(paired ? pairFields(paired, next) : { metrics: next }),
  })
  return (
    <div className="space-y-2 rounded-card border border-line bg-panel p-3">
      <p className="text-xs font-semibold text-muted">{t('항목 편집')}</p>
      {pairs.map(([left, right], index) => (
        <div key={index} className="grid gap-2 sm:grid-cols-[minmax(6rem,0.35fr)_1fr_auto_auto]">
          <Input
            aria-label={t('{n}번째 {label}').replace('{n}', String(index + 1)).replace('{label}', t(pairLabels[0]))}
            value={left}
            onChange={(event) => writePairs(pairs.map((row, i) => i === index ? [event.target.value, row[1]] : row))}
          />
          <Input
            aria-label={t('{n}번째 {label}').replace('{n}', String(index + 1)).replace('{label}', t(pairLabels[1]))}
            value={right}
            onChange={(event) => writePairs(pairs.map((row, i) => i === index ? [row[0], event.target.value] : row))}
          />
          <Button size="sm" disabled={pairs.length <= 1} onClick={() => writePairs(pairs.filter((_, i) => i !== index))}>
            {t('삭제')}
          </Button>
          <div className="flex gap-1">
            <Button aria-label={t('{n}번째 항목 위로').replace('{n}', String(index + 1))} size="sm" disabled={index === 0} onClick={() => { const next = [...pairs] as [string, string][]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; writePairs(next) }}><ArrowUp size={13} /></Button>
            <Button aria-label={t('{n}번째 항목 아래로').replace('{n}', String(index + 1))} size="sm" disabled={index === pairs.length - 1} onClick={() => { const next = [...pairs] as [string, string][]; [next[index], next[index + 1]] = [next[index + 1], next[index]]; writePairs(next) }}><ArrowDown size={13} /></Button>
          </div>
        </div>
      ))}
      <Button size="sm" onClick={() => writePairs([...pairs, ['', '']])}>{t('항목 추가')}</Button>
    </div>
  )
}

/**
 * The room a deck is shown in: black, full-screen, and holding the keyboard.
 *
 * Shared with the HTML deck panel, where a deck is markup rather than slide
 * objects. What stands on the stage differs; the counter, the keys and the way
 * out are the same job.
 */
export function PresentStage({
  title,
  index,
  count,
  onIndex,
  onClose,
  notes,
  outline,
  children,
}: {
  title: string
  index: number
  count: number
  onIndex: (i: number) => void
  onClose: () => void
  /** The presenter's own screen. Left out by a deck that carries no notes. */
  notes?: ReactNode
  /** Slide titles, so a long deck can be jumped through rather than stepped. */
  outline?: string[]
  children: ReactNode
}) {
  const t = useT()
  const [showNotes, setShowNotes] = useState(true)
  const [showList, setShowList] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [fullscreen, setFullscreen] = useState(false)
  const stageRef = useRef<HTMLDivElement>(null)
  const timerStart = useRef(0)

  useEffect(() => {
    timerStart.current = Date.now()
    const timer = window.setInterval(() => setElapsed(Math.floor((Date.now() - timerStart.current) / 1000)), 1000)
    return () => window.clearInterval(timer)
  }, [])

  useEffect(() => {
    const changed = () => setFullscreen(document.fullscreenElement === stageRef.current)
    document.addEventListener('fullscreenchange', changed)
    return () => document.removeEventListener('fullscreenchange', changed)
  }, [])

  const toggleFullscreen = async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen()
      else await stageRef.current?.requestFullscreen()
    } catch {
      // Browsers can refuse fullscreen (embedded previews and managed devices
      // commonly do). Presentation still works as a viewport-sized overlay.
    }
  }

  const close = () => {
    if (document.fullscreenElement === stageRef.current) void document.exitFullscreen()
    onClose()
  }

  const clock = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`

  /**
   * Keyboard, owned while presenting. Capture phase and stopped here: an
   * Escape left to bubble would also close the dialog the deck opened from.
   */
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const keys = ['Escape', 'ArrowRight', 'ArrowLeft', ' ', 'n', 'N']
      if (!keys.includes(e.key)) return
      e.preventDefault()
      e.stopPropagation()
      if (e.key === 'Escape') {
        if (document.fullscreenElement === stageRef.current) void document.exitFullscreen()
        onClose()
      }
      if (e.key === 'ArrowRight' || e.key === ' ') onIndex(Math.min(index + 1, count - 1))
      if (e.key === 'ArrowLeft') onIndex(Math.max(index - 1, 0))
      if (e.key.toLowerCase() === 'n') setShowNotes((s) => !s)
    }
    document.addEventListener('keydown', onKey, true)
    return () => document.removeEventListener('keydown', onKey, true)
  }, [index, count, onIndex, onClose])

  /* Portalled to the body rather than left where the panel sits. The deck can
     be opened inside a dialog, and an animated ancestor makes `fixed` resolve
     against *that box* — which turns full-screen rehearsal into a slide shown
     in a 500px window. */
  return createPortal(
    <div ref={stageRef} role="dialog" aria-label={t('발표 모드')} className="fixed inset-0 z-50 flex flex-col bg-black">
      <div className="flex items-center gap-2 px-4 py-2 text-white/70">
        <Presentation size={14} />
        <span className="text-base">{title}</span>
        <span className="ml-auto text-base tabular-nums">
          {index + 1} / {count}
        </span>
        <button
          onClick={() => {
            timerStart.current = Date.now()
            setElapsed(0)
          }}
          aria-label={t('발표 시간 다시 시작')}
          title={t('누르면 발표 시간을 0으로 되돌립니다')}
          className="rounded-control px-2 py-1 font-mono text-sm tabular-nums transition-colors hover:bg-white/10"
        >
          {clock}
        </button>
        {outline && (
          <button
            onClick={() => setShowList((s) => !s)}
            aria-pressed={showList}
            className="rounded-control px-2 py-1 text-sm transition-colors hover:bg-white/10"
          >
            {t('장 목록')}
          </button>
        )}
        {notes !== undefined && (
          <button
            onClick={() => setShowNotes((s) => !s)}
            className="rounded-control px-2 py-1 text-sm transition-colors hover:bg-white/10"
          >
            {t('노트')} (N)
          </button>
        )}
        <button
          onClick={() => void toggleFullscreen()}
          aria-label={fullscreen ? t('전체 화면 끝내기') : t('전체 화면')}
          aria-pressed={fullscreen}
          className="rounded-control p-1.5 transition-colors hover:bg-white/10"
        >
          <Maximize size={16} />
        </button>
        <button
          onClick={close}
          aria-label={t('발표 끝내기')}
          className="rounded-control p-1.5 transition-colors hover:bg-white/10"
        >
          <X size={16} />
        </button>
      </div>
      <div className="flex min-h-0 flex-1">
        {/* 어디까지 왔고 다음이 무엇인지. 스무 장짜리 덱을 한 장씩 넘겨
            찾는 것은 방에 사람이 앉아 있을 때 할 일이 아니다. */}
        {outline && showList && (
          <nav
            aria-label={t('장 목록')}
            className="w-52 shrink-0 overflow-y-auto border-r border-white/10 py-2"
          >
            {outline.map((name, i) => (
              <button
                key={i}
                onClick={() => onIndex(i)}
                aria-current={i === index}
                aria-label={t('{n}번 장').replace('{n}', String(i + 1))}
                className={cn(
                  'flex w-full items-start gap-2 px-3 py-1.5 text-left text-sm leading-snug transition-colors',
                  i === index
                    ? 'bg-white/15 text-white'
                    : 'text-white/55 hover:bg-white/10 hover:text-white',
                )}
              >
                <span className="shrink-0 text-white/40 tabular-nums">{i + 1}</span>
                <span className="min-w-0 flex-1 line-clamp-2">{name || t('제목 없음')}</span>
              </button>
            ))}
          </nav>
        )}
        <div className="flex min-h-0 flex-1 items-center justify-center px-6 pb-4">{children}</div>
      </div>
      {notes !== undefined && showNotes && (
        <div className="max-h-40 overflow-y-auto border-t border-white/10 px-6 py-3 text-base leading-relaxed text-white/75">
          {notes}
        </div>
      )}
      <div className="flex items-center justify-center gap-2 pb-4 text-white/70">
        <button
          onClick={() => onIndex(Math.max(index - 1, 0))}
          disabled={index === 0}
          aria-label={t('이전 장')}
          className="rounded-control p-2 transition-colors hover:bg-white/10 disabled:opacity-30"
        >
          <ChevronLeft size={18} />
        </button>
        <span className="text-sm">{t('← → 로 넘기고 Esc 로 끝냅니다')}</span>
        <button
          onClick={() => onIndex(Math.min(index + 1, count - 1))}
          disabled={index >= count - 1}
          aria-label={t('다음 장')}
          className="rounded-control p-2 transition-colors hover:bg-white/10 disabled:opacity-30"
        >
          <ChevronRight size={18} />
        </button>
      </div>
    </div>,
    document.body,
  )
}

/**
 * Full-screen rehearsal. A deck is checked by walking it at the size it will
 * be shown at — text too small to read from the back of the room is legible
 * in a 400px thumbnail.
 */
function PresentMode({
  deck,
  index,
  onIndex,
  onClose,
}: {
  deck: DeckArtifact
  index: number
  onIndex: (i: number) => void
  onClose: () => void
}) {
  const t = useT()
  // Measured, not chosen. `SlideView` sizes every rule, every gap and every
  // type size as `n * scale`, so the drawing only looks like itself when
  // `scale` is the stage's own width over 400 — the relationship
  // `useStageScale` exists to keep. This screen had a hard 2.4 instead, tuned
  // for some window nobody wrote down, and on a 1152px stage the right answer
  // is 2.88: every word, the accent bar and all the padding came out 17%
  // small. Presenting a deck and reading it in the panel showed two different
  // designs of the same slide, which also meant rehearsing at a size the room
  // would never see.
  const stage = useStageScale()
  const slide = deck.slides[index]
  if (!slide) return null
  return (
    // `outline` is what draws the slide list in the presentation header. The
    // HTML artifact panel has always passed it and this one never did, so the
    // same deck presented from here had no way to jump to a slide — the one
    // thing a presenter reaches for when a question comes from the floor.
    <PresentStage
      title={deck.title}
      index={index}
      count={deck.slides.length}
      onIndex={onIndex}
      onClose={onClose}
            outline={deck.slides.map((s) => s.title)}
      notes={slide.notes || <span className="text-white/35">{t('노트 없음')}</span>}
    >
      <div
        ref={stage.ref}
        className="aspect-video max-h-full w-full max-w-6xl overflow-hidden rounded-control shadow-float"
      >
        <SlideView
          slide={slide}
          scale={stage.scale}
          writing={false}
          deckTitle={deck.title}
          brand={deck.design ?? undefined}
          index={index}
          total={deck.slides.length}
        />
      </div>
    </PresentStage>
  )
}

/**
 * Stage width → `scale`. `SlideView` sizes everything off it, so a fixed value
 * tuned for a 460px desktop stage overflows a 210px phone one and the preview
 * stops matching the `.pptx`.
 */
/** The widest a 16:9 box can be inside the measured element without
 *  overflowing its height — so a slide shrinks to fit the row it sits in
 *  rather than taking the full width and pushing the notes off screen. */
function useFitWidth() {
  const ref = useRef<HTMLDivElement>(null)
  const [width, setWidth] = useState<number | null>(null)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const { width: w, height: h } = entry.contentRect
      if (w > 0 && h > 0) setWidth(Math.floor(Math.min(w, (h * 16) / 9)))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [])
  return { ref, width }
}

function useStageScale(minScale = 0.45) {
  const ref = useRef<HTMLDivElement>(null)
  const [scale, setScale] = useState(1.15)
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const observer = new ResizeObserver(([entry]) => {
      const width = entry.contentRect.width
      if (width > 0) setScale(Math.max(minScale, width / 400))
    })
    observer.observe(el)
    return () => observer.disconnect()
  }, [minScale])
  return { ref, scale }
}

/** A rail preview drawn by the exact same renderer and width rule as the stage. */
function SlideThumbnail({ deck, slide, index, writing }: { deck: DeckArtifact; slide: Slide; index: number; writing: boolean }) {
  // A rail card is about 116px wide. The stage's 45% minimum was intended to
  // keep the main editor usable, but here it enlarged a 29% preview and then
  // clipped its right and bottom edges. Let the shared renderer reach the
  // thumbnail's real scale instead.
  const stage = useStageScale(0.2)
  return (
    <div ref={stage.ref} className="size-full">
      <SlideView
        slide={slide}
        scale={stage.scale}
        writing={writing}
        deckTitle={deck.title}
        brand={deck.design ?? undefined}
        index={index}
        total={deck.slides.length}
      />
    </div>
  )
}

export function DeckPanel({
  deck,
  onClose,
  onModeChange,
  onDirtyChange,
}: {
  deck: DeckArtifact
  onClose?: () => void
  /** Fires when the reader asks for room. A deck is checked by looking at it,
   *  and the stage beside a transcript is about 330px wide. */
  onModeChange?: (mode: PanelMode) => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const t = useT()
  const width = usePanelWidth(onModeChange)

  /**
   * One finding from the checks, fixed.
   *
   * Rewrites the slide it was found on, through the endpoint that mirrors the
   * report's — so the deck changes, a snapshot is kept, and a rewrite that
   * reads worse is one press of 되돌리기 from undone.
   *
   * The deck could not do this until now. `deck.rewrite_slide` existed and was
   * reachable only by asking in the conversation, which is a request rather
   * than an action: the deck does not change, and the reader has to watch the
   * transcript and work out for themselves whether anything happened.
   *
   * A finding about the deck as a whole names no slide and has nowhere to go,
   * so it keeps the button hidden rather than pretending.
   */
  const fixFinding = async (finding: LintFinding) => {
    const slide = slideFor(deck.slides, finding.where)
    if (!slide) throw new Error(t('어느 장을 고쳐야 하는지 알 수 없습니다.'))
    const row = await artifactsApi.rewriteSlide(
      deck.id,
      slide.id,
      t('검사에서 지적된 문제를 고쳐 주세요: {message}').replace('{message}', finding.message),
    )
    const data = (row.data ?? {}) as { slides?: Slide[] }
    // Written onto the object this panel holds as well as into the store — the
    // artifacts screen opens its modal on a copy it took when the card was
    // clicked, so a store refresh alone leaves the new slide invisible exactly
    // where it was asked for.
    if (data.slides) deck.slides = data.slides
    deck.version = row.version
  }
  /**
   * Every finding at once, one rewrite per slide.
   *
   * Not a loop over `fixFinding`: two findings about one slide would be two
   * rewrites of it, and the second lands on what the first produced — asked to
   * fix a line that is no longer there, it writes the first fix back out.
   * Grouped, a slide is rewritten once and told everything found in it.
   *
   * One after another, not together: the slides share a deck and a version, so
   * two rewrites in flight means the second saves over the first.
   */
  const fixAllFindings = async (findings: LintFinding[]) => {
    const failed: string[] = []
    for (const [where, group] of byWhere(findings)) {
      const slide = where ? slideFor(deck.slides, where) : undefined
      if (!slide) {
        // A deck has no conversation path of its own for a finding about the
        // whole thing, so it is named rather than silently dropped.
        failed.push(where || t('덱 전체'))
        continue
      }
      try {
        const row = await artifactsApi.rewriteSlide(
          deck.id,
          slide.id,
          fixNote(
            group,
            t('검사에서 지적된 문제를 고쳐 주세요: {message}'),
            t('검사에서 지적된 문제를 모두 고쳐 주세요:\n{list}'),
          ),
        )
        const data = (row.data ?? {}) as { slides?: Slide[] }
        if (data.slides) deck.slides = data.slides
        deck.version = row.version
      } catch {
        failed.push(where)
      }
    }
    if (failed.length > 0) {
      throw new Error(
        t('고치지 못한 장이 있습니다: {list}').replace('{list}', failed.join(', ')),
      )
    }
  }

  const panel = usePanelNarrow<HTMLDivElement>()
  const stage = useStageScale()
  const fit = useFitWidth()
  const [selected, setSelected] = useState(0)
  const [ribbon, setRibbon] = useState<'home' | 'insert' | 'review' | 'view' | 'show' | 'file'>('home')
  const [editing, setEditing] = useState(false)
  //: 리본의 편집 칸. 없으면 도구는 제자리에 그려진다.
  const [editToolbarSlot, setEditToolbarSlot] = useState<HTMLElement | null>(null)
  const [discardAction, setDiscardAction] = useState<'cancel' | 'close' | null>(null)
  const [draft, setDraft] = useState('')
  // Structured fields such as chart series cannot make a safe round trip
  // through the line-based text box. This is the live slide shown on stage.
  const [slideDraft, setSlideDraft] = useState<Slide | null>(null)
  const [editHistory, setEditHistory] = useState<Slide[]>([])
  const [editFuture, setEditFuture] = useState<Slide[]>([])
  const applyingHistory = useRef(false)
  const [notes, setNotes] = useState('')
  const [saving, setSaving] = useState(false)
  const savingLock = useRef(false)
  const [rewritingSlide, setRewritingSlide] = useState(false)
  const [selectionFormat, setSelectionFormat] = useState({ bold: false, italic: false, underline: false, size: 100 })
  const [selectedElement, setSelectedElement] = useState<SlideElement | null>(null)
  const [overflowing, setOverflowing] = useState(false)
  const [autoFitting, setAutoFitting] = useState(false)
  const [fitLimitReached, setFitLimitReached] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [recoveryCopied, setRecoveryCopied] = useState(false)
  const [checking, setChecking] = useState(false)
  const [presenting, setPresenting] = useState(false)
  const [reviewOpen, setReviewOpen] = useState(false)
  const [reviewDraft, setReviewDraft] = useState('')
  const [reviewComments, setReviewComments] = useState(deck.reviewComments ?? [])
  const [reviewSaving, setReviewSaving] = useState(false)
  //: The rail shows the deck either as pictures or as an outline. Both answer
  //: different questions — "which slide was the chart on" and "does the
  //: argument run in the right order".
  const [rail, setRail] = useState<'thumbs' | 'outline'>('thumbs')
  //: In a panel narrower than the deck asks for, the rail becomes a drawer,
  //: the same way the report's contents do. Beside the stage it is 132px of a
  //: 390px panel, which leaves the slide 119px — a picture of a slide rather
  //: than the slide.
  /**
   * 장 목록을 접었는가.
   *
   * The two widths start opposite ways round: on a narrow panel the list is a
   * drawer that starts closed, on a wide one it is a rail that starts open. One
   * flag for "the person has pressed the toggle" keeps both honest — what the
   * screen shows, and what `aria-pressed` says, are both read off `railShown`
   * below rather than off the flag.
   */
  const [railToggled, setRailToggled] = useState(false)
  //: 좁으면 서랍이라 닫힌 채로 시작하고, 넓으면 옆줄이라 펼친 채로 시작한다.
  //: 누르면 어느 쪽이든 반대가 된다.
  const railShown = panel.narrow ? railToggled : !railToggled
  const [bulkMode, setBulkMode] = useState(false)
  const [bulkSelected, setBulkSelected] = useState<Set<string>>(new Set())
  const [bulkAccent, setBulkAccent] = useState(deck.design?.accent ?? '#5b5bd6')
  const [visualStyle, setVisualStyle] = useState(deck.design?.visualStyle ?? 'editorial')
  const [bulkScale, setBulkScale] = useState('1')

  useEffect(() => {
    const present = new Set(deck.slides.map((candidate) => candidate.id))
    setBulkSelected((current) => new Set([...current].filter((id) => present.has(id))))
  }, [deck.slides.length])

  const toggleBulkSlide = (id: string) => setBulkSelected((current) => {
    const next = new Set(current)
    if (next.has(id)) next.delete(id)
    else next.add(id)
    return next
  })

  const runFactCheck = async (slideId: string) => {
    setChecking(true)
    setError(null)
    try {
      const row = await artifactsApi.factcheckSlide(deck.id, slideId)
      const next = (row.data as { slides?: Slide[] } | null)?.slides
      if (next) deck.slides = next
      deck.version = row.version
    } catch (err) {
      setError(errorMessage(err, t('확인하지 못했습니다.')))
    } finally {
      setChecking(false)
    }
  }
  // Slides arrive one at a time, so the selection can point past the end.
  const index = Math.min(selected, Math.max(deck.slides.length - 1, 0))
  const slide = deck.slides[index] as Slide | undefined
  const weakSlides = deck.slides
    .map((s, i) => (s.factCheck?.claims.some((c) => c.verdict !== 'supported') ? i : -1))
    .filter((i) => i >= 0)
  const overflowRisks = deck.slides
    .map((candidate, i) => (overflowRisk(i === index && slideDraft ? slideDraft : candidate) ? i : -1))
    .filter((i) => i >= 0)
  const unresolvedReviews = reviewComments.filter((comment) => comment.status === 'open')
  const exportWarningCount = weakSlides.length + overflowRisks.length + unresolvedReviews.length
  // Still being written, which is the only thing these controls need to wait
  // for: export would 404 on a document the server does not have yet, and an
  // edit would be overwritten by the next slide event of a run still going.
  //
  // This asked whether every slide had content, which answers the same
  // question almost always and answers it wrong in the one case that matters.
  // A slide whose model call came back unusable stays empty — the writer falls
  // back to bullets and salvages what it can, and sometimes there is nothing
  // to salvage — and the deck was then locked for good: no export, no
  // 발표, and no 텍스트 수정, which is the control that exists to fix exactly
  // this. The deck had finished writing; only its result was disappointing,
  // and a disappointing result is the reader's to repair.
  const writing = deck.draft === true || deck.slides.length === 0

  useEffect(() => {
    if (writing) setEditing(false)
  }, [writing])

  useEffect(() => {
    setOverflowing(false)
    setAutoFitting(false)
    setFitLimitReached(false)
  }, [index])

  useEffect(() => {
    if (!autoFitting) return
    if (!overflowing) {
      setAutoFitting(false)
      return
    }
    const scale = slideDraft?.textScale ?? 1
    if (scale <= 0.65) {
      setAutoFitting(false)
      setFitLimitReached(true)
      return
    }
    const timer = window.setTimeout(() => setSlideDraft((current) => current ? ({
      ...current,
      textScale: Math.max(0.65, Math.round(((current.textScale ?? 1) - 0.05) * 100) / 100),
    }) : current), 100)
    return () => window.clearTimeout(timer)
  }, [autoFitting, overflowing, slideDraft?.textScale])

  useEffect(() => {
    if (!editing) {
      setSelectedElement(null)
      return
    }
    const key = (event: KeyboardEvent) => {
      const target = event.target as HTMLElement | null
      if (target?.closest('input, textarea, select, [contenteditable="true"]')) return
      if (event.key === 'Escape') setSelectedElement(null)
      if ((event.key === 'Delete' || event.key === 'Backspace') && selectedElement === 'image') {
        event.preventDefault()
        setSlideDraft((current) => current ? ({ ...current, image: undefined }) : current)
        setSelectedElement(null)
      }
      if (selectedElement === 'image' && (event.key === 'ArrowLeft' || event.key === 'ArrowRight')) {
        event.preventDefault()
        setSlideDraft((current) => current?.image ? ({
          ...current,
          image: { ...current.image, position: event.key === 'ArrowLeft' ? 'left' : 'right' },
        }) : current)
      }
    }
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [editing, selectedElement])

  useEffect(() => {
    if (!editing || !slideDraft) return
    if (applyingHistory.current) {
      applyingHistory.current = false
      return
    }
    setEditHistory((current) => {
      if (JSON.stringify(current.at(-1)) === JSON.stringify(slideDraft)) return current
      return [...current.slice(-39), structuredClone(slideDraft)]
    })
    setEditFuture([])
  }, [editing, slideDraft])

  //: The deck as it stood when this edit began, for the same comparison the
  //: report panel makes.
  const baseline = useRef('')
  const formattingRange = useRef<Range | null>(null)

  const rememberFormattingRange = () => {
    const selection = window.getSelection()
    if (!selection?.rangeCount) return
    const range = selection.getRangeAt(0)
    const parent = range.commonAncestorContainer.nodeType === Node.ELEMENT_NODE
      ? range.commonAncestorContainer as Element
      : range.commonAncestorContainer.parentElement
    const editable = parent?.closest('[contenteditable="true"]') as HTMLElement | null
    if (editable) {
      formattingRange.current = range.cloneRange()
      const anchor = (range.startContainer.nodeType === Node.ELEMENT_NODE ? range.startContainer : range.startContainer.parentElement) as HTMLElement | null
      const base = Number.parseFloat(getComputedStyle(editable).fontSize) || 16
      const current = anchor ? Number.parseFloat(getComputedStyle(anchor).fontSize) || base : base
      setSelectionFormat({
        bold: document.queryCommandState('bold'),
        italic: document.queryCommandState('italic'),
        underline: document.queryCommandState('underline'),
        size: Math.round((current / base) * 100),
      })
    }
  }

  const formatSelection = (command: string, value?: string) => {
    const selection = window.getSelection()
    if (formattingRange.current && selection) {
      selection.removeAllRanges()
      selection.addRange(formattingRange.current)
    }
    document.execCommand(command, false, value)
    rememberFormattingRange()
  }

  const sizeSelection = (percent: number) => {
    const selection = window.getSelection()
    const range = formattingRange.current
    if (!selection || !range || range.collapsed) return
    selection.removeAllRanges()
    selection.addRange(range)
    if (percent === 100) {
      document.execCommand('fontSize', false, '3')
    } else {
      const span = document.createElement('span')
      span.style.fontSize = `${percent / 100}em`
      span.append(range.extractContents())
      range.insertNode(span)
      range.selectNodeContents(span)
      selection.removeAllRanges()
      selection.addRange(range)
    }
    formattingRange.current = range.cloneRange()
    rememberFormattingRange()
  }

  useEffect(() => {
    if (!editing) return
    const changed = () => rememberFormattingRange()
    document.addEventListener('selectionchange', changed)
    return () => document.removeEventListener('selectionchange', changed)
  }, [editing])

  /**
   * Refetch before editing. The panel opens ahead of the store's refresh, and
   * an editor opened in that gap would baseline on older text and save into a
   * phantom conflict.
   */
  const startEditing = async () => {
    if (!slide) return
    setError(null)
    // Editing has its own focused command surface. Returning to Home prevents
    // an empty Insert/Review panel from lingering above it.
    setRibbon('home')
    const latest = await artifactsApi.get(deck.id).catch(() => null)
    const onServer = (latest?.data as { slides?: Slide[] } | null)?.slides
    if (latest && onServer) {
      deck.slides = onServer
      deck.version = latest.version
    }
    const current = deck.slides[index]
    if (!current) return
    baseline.current = JSON.stringify(deck.slides)
    setDraft(toLines(current))
    setSlideDraft(structuredClone(current))
    setEditHistory([structuredClone(current)])
    setEditFuture([])
    setSelectedElement(null)
    setNotes(current.notes ?? '')
    setEditing(true)
  }

  const undoEdit = () => {
    if (editHistory.length < 2) return
    const current = editHistory.at(-1)!
    const previous = structuredClone(editHistory.at(-2)!)
    applyingHistory.current = true
    setEditHistory((rows) => rows.slice(0, -1))
    setEditFuture((rows) => [current, ...rows])
    setSlideDraft(previous)
    setDraft(toLines(previous))
    setNotes(previous.notes ?? '')
  }

  const redoEdit = () => {
    if (!editFuture.length) return
    const next = structuredClone(editFuture[0])
    applyingHistory.current = true
    setEditFuture((rows) => rows.slice(1))
    setEditHistory((rows) => [...rows, next])
    setSlideDraft(next)
    setDraft(toLines(next))
    setNotes(next.notes ?? '')
  }

  /** Rebuild only the selected slide, preserving the rest of the deck. */
  const regenerateSlide = async () => {
    if (!slide || rewritingSlide) return
    setRewritingSlide(true)
    setError(null)
    try {
      const row = await artifactsApi.rewriteSlide(deck.id, slide.id, '')
      const data = (row.data ?? {}) as { slides?: Slide[] }
      if (!data.slides) throw new Error(t('다시 쓰지 못했습니다.'))
      deck.slides = data.slides
      deck.version = row.version
      baseline.current = JSON.stringify(data.slides)
    } catch (err) {
      setError(errorMessage(err, t('다시 쓰지 못했습니다.')))
    } finally {
      setRewritingSlide(false)
    }
  }

  const save = async () => {
    if (!slide) return
    if (savingLock.current) return
    const lines = draft
      .split('\n')
      .map((l) => l.replace(/^\s*[-*•]\s*/, '').trim())
      .filter(Boolean)
    if (lines.length === 0) {
      setError(t('내용이 비어 있습니다. 저장하지 않았습니다.'))
      return
    }

    const [title, ...rest] = lines
    const table = rest.map(toCells).filter(Boolean) as string[][]
    const words = rest.filter((line) => toCells(line) === null)
    /*
     * The pairs of a `bands`, `tiles` or `timeline` slide, read out of the same
     * `|` lines a table row uses — the syntax `toLines` sent them out in.
     *
     * Read before `table` is taken for a table, or the three shapes would be
     * saved as one. Anything past the second cell is rejoined rather than
     * dropped, so a pair whose text contains a pipe survives the round trip
     * unchanged instead of losing its tail.
     */
    const sourceSlide = slideDraft ?? slide
    const paired = pairedLayout(sourceSlide)
    const pairs: [string, string][] = paired
      ? table.map((cells) => [cells[0], cells.slice(1).join(' | ')])
      : []

    // Layout follows the shape that arrived: on a quote slide one line is a
    // quotation and several are bullets, since quote renders only the first.
    //
    // The rows a person typed replace the rows that were there, and typing none
    // on a slide that had a table removes it. Both are the same rule — what is
    // in the box is what the slide becomes — and it is the rule that was
    // missing: the table used to survive an edit that never mentioned it and
    // then hide the bullets that did get typed.
    const shaped: Slide =
      table.length > 0 && !paired
        ? // `metrics` is a table of exactly two columns whose left side is a
          // figure. Kept as metrics only if that is what the slide already was;
          // otherwise two columns are two columns.
          sourceSlide.metrics?.length && table.every((row) => row.length === 2)
          ? { ...sourceSlide, metrics: table.map(([f, l]) => [f, l] as [string, string]), rows: undefined }
          : { ...sourceSlide, layout: 'table', rows: table, metrics: undefined }
        : // Emptying the box of its pairs empties the slide of them, the same
          // rule the table follows — and the array has to go with them or the
          // slide still counts as having content nothing draws.
          { ...sourceSlide, rows: undefined, metrics: undefined, ...(paired ? pairFields(null) : null) }

    const edited: Slide =
      sourceSlide.layout === 'chart' && sourceSlide.chart
        ? { ...sourceSlide, title, notes, body: undefined, bullets: undefined }
      : paired && pairs.length > 0
        ? // The layout is kept. It used to be forced to `bullets` here, so a
          // typo fixed in the title of a bands slide saved four bands as
          // nothing and PATCHed that over the deck.
          {
            ...sourceSlide,
            ...pairFields(paired, pairs),
            title,
            rows: undefined,
            metrics: undefined,
            bullets: words.length ? words : undefined,
            body: undefined,
            notes,
          }
        : sourceSlide.layout === 'quote' && words.length <= 1
          ? { ...shaped, title, body: words[0] ?? '', bullets: undefined, notes }
          : // A cover and a 장 divider are a title over a line of prose. `section`
            // is here rather than falling through because the fall-through
            // rewrote it as `bullets`, and a divider that is no longer a divider
            // takes the deck's numbering with it — `number` is on the slide and
            // nowhere else.
            sourceSlide.layout === 'title' || sourceSlide.layout === 'section'
            ? { ...shaped, title, body: words.join(' ') || undefined, notes }
            : table.length > 0
              ? {
                  ...shaped,
                  title,
                  bullets: words.length ? words : undefined,
                  body: undefined,
                  notes,
                }
              : {
                  ...shaped,
                  // A two-column slide is still written as ordinary lines.
                  // Saving those lines used to silently turn it back into a
                  // bullet slide, undoing a layout choice made moments before.
                  layout: sourceSlide.layout === 'two-column' ? 'two-column' : 'bullets',
                  title,
                  bullets: words,
                  body: undefined,
                  notes,
                }

    const slides = deck.slides.map((s, i) => (i === index ? edited : s))
    savingLock.current = true
    setSaving(true)
    setError(null)
    try {
      // Same check the report panel makes, and for the same reason: this PATCH
      // carries every slide, so saving over somebody else's edit throws their
      // work away silently. Compared by content — a version number from a
      // list fetched minutes ago says nothing about who edited what.
      const latest = await artifactsApi.get(deck.id).catch(() => null)
      const latestData = latest?.data as Partial<DeckArtifact> | null
      const onServer = latestData?.slides
      const savedDesign = latestData?.design ?? deck.design
      const expected = baseline.current || JSON.stringify(deck.slides)
      if (onServer && JSON.stringify(onServer) !== expected) {
        setError(
          t('이 덱은 다른 곳에서 이미 수정되었습니다. 새로고침해 최신 내용을 받은 뒤 다시 저장하세요.'),
        )
        return
      }
      // PATCHing `data` as one deck is what snapshots the previous revision
      // server-side, which is the way back from a bad edit.
      const row = await artifactsApi.update(deck.id, {
        data: { ...latestData, kind: 'deck', theme: deck.theme, slides, ...(savedDesign ? { design: savedDesign } : {}) },
        summary: t('{n}장 편집').replace('{n}', String(index + 1)),
        // The version the check above read. See ReportPanel for why.
        expectedVersion: latest?.version ?? deck.version,
      })
      deck.slides = slides
      if (savedDesign) deck.design = savedDesign
      // Kept in step with the server, or the next save on this panel sends a
      // version that is one behind and is refused as somebody else's edit.
      deck.version = row.version
      baseline.current = JSON.stringify(slides)
      setSlideDraft(null)
      setEditing(false)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      savingLock.current = false
      setSaving(false)
    }
  }

  const copySlideRecovery = async () => {
    const current = slideDraft ?? slide
    if (!current) return
    await copyText([toLines(current), notes ? `${t('발표 노트')}: ${notes}` : ''].filter(Boolean).join('\n\n'))
    setRecoveryCopied(true)
    window.setTimeout(() => setRecoveryCopied(false), 1800)
  }

  const reloadLatestDeck = async () => {
    setSaving(true)
    try {
      const latest = await artifactsApi.get(deck.id)
      const data = latest.data as Partial<DeckArtifact>
      const slides = data.slides ?? []
      deck.slides = slides
      deck.version = latest.version
      if (data.design) deck.design = data.design
      baseline.current = JSON.stringify(slides)
      const current = slides[Math.min(index, Math.max(slides.length - 1, 0))]
      if (current) {
        setDraft(toLines(current))
        setSlideDraft(structuredClone(current))
        setNotes(current.notes ?? '')
        setEditHistory([structuredClone(current)])
        setEditFuture([])
      } else {
        setEditing(false)
        setSlideDraft(null)
      }
      setError(null)
    } catch (err) {
      setError(errorMessage(err, t('최신 내용을 불러오지 못했습니다.')))
    } finally {
      setSaving(false)
    }
  }

  /**
   * Adding, removing and reordering slides.
   *
   * A deck arrives with the shape the model chose and there was no way to
   * change it: not one control on either surface added a slide, removed one, or
   * moved one. Everything a person could do to the structure of a document they
   * had to do by asking for the whole thing again, which throws away every edit
   * they had made to the slides they were keeping.
   *
   * Saved through the same door `save` uses — the whole deck as one PATCH,
   * checked against the server first — so a structural edit is snapshotted and
   * one click from undone like any other.
   */
  const restructure = async (next: Slide[], summary: string, land: number) => {
    if (savingLock.current) return
    savingLock.current = true
    setSaving(true)
    setError(null)
    try {
      const latest = await artifactsApi.get(deck.id).catch(() => null)
      const latestData = latest?.data as Partial<DeckArtifact> | null
      const onServer = latestData?.slides
      const savedDesign = latestData?.design ?? deck.design
      // 구조 편집은 텍스트 편집기를 먼저 열 필요가 없다. 그 전에는 baseline
      // 이 비어 있으므로 패널이 들고 있는 현재 덱을 기준으로 삼는다. 빈 문자열과
      // 서버 덱을 비교하면 첫 장 추가·이동·삭제가 언제나 충돌로 거절됐다.
      const expected = baseline.current || JSON.stringify(deck.slides)
      if (onServer && JSON.stringify(onServer) !== expected) {
        setError(
          t('이 덱은 다른 곳에서 이미 수정되었습니다. 새로고침해 최신 내용을 받은 뒤 다시 저장하세요.'),
        )
        return
      }
      const row = await artifactsApi.update(deck.id, {
        data: { ...latestData, kind: 'deck', theme: deck.theme, slides: next, ...(savedDesign ? { design: savedDesign } : {}) },
        summary,
        expectedVersion: latest?.version ?? deck.version,
      })
      deck.slides = next
      if (savedDesign) deck.design = savedDesign
      deck.version = row.version
      baseline.current = JSON.stringify(next)
      setEditing(false)
      setSlideDraft(null)
      // Follow the slide, not the number. After a move the thing somebody was
      // looking at is somewhere else, and a panel that stayed on the index
      // would show them a different slide as though nothing had happened.
      setSelected(Math.max(0, Math.min(land, next.length - 1)))
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      savingLock.current = false
      setSaving(false)
    }
  }

  const splitOverflow = () => {
    const source = slideDraft ?? slide
    if (!source) return
    const parts = splitStructuredSlide(source, t('(계속)'))
    if (!parts) {
      setError(t('자동으로 나눌 항목을 찾지 못했습니다. 문장을 줄이거나 새 장을 추가해 주세요.'))
      return
    }
    const next = [...deck.slides.slice(0, index), ...parts, ...deck.slides.slice(index + 1)]
    void restructure(next, t('{n}장을 두 장으로 나눔').replace('{n}', String(index + 1)), index + 1)
  }

  const applyBulkSlides = async (kind: 'accent' | 'scale' | 'reset') => {
    if (!bulkSelected.size || savingLock.current) return
    savingLock.current = true
    setSaving(true)
    setError(null)
    try {
      const latest = await artifactsApi.get(deck.id)
      const latestData = (latest.data ?? {}) as Partial<DeckArtifact>
      const scale = Number(bulkScale)
      const next = (latestData.slides ?? deck.slides).map((candidate) => {
        if (!bulkSelected.has(candidate.id)) return candidate
        if (kind === 'accent') return { ...candidate, accent: bulkAccent }
        if (kind === 'scale') return { ...candidate, textScale: scale === 1 ? undefined : scale }
        const { accent: _accent, textScale: _textScale, ...withoutOverrides } = candidate
        return withoutOverrides as Slide
      })
      const label = kind === 'accent' ? t('선택 장 강조색 변경') : kind === 'scale' ? t('선택 장 글자 크기 변경') : t('선택 장 서식 초기화')
      const design = latestData.design ?? deck.design
      const row = await artifactsApi.update(deck.id, {
        data: { ...latestData, kind: 'deck', theme: deck.theme, slides: next, ...(design ? { design } : {}) },
        summary: label,
        expectedVersion: latest.version,
      })
      deck.slides = next
      if (design) deck.design = design
      deck.version = row.version
      baseline.current = JSON.stringify(next)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      savingLock.current = false
      setSaving(false)
    }
  }

  const saveDeckAccent = async (nextAccent = bulkAccent) => {
    if (savingLock.current) return
    savingLock.current = true
    setSaving(true)
    setError(null)
    try {
      const latest = await artifactsApi.get(deck.id)
      const latestData = (latest.data ?? {}) as Partial<DeckArtifact>
      const latestSlides = latestData.slides ?? deck.slides
      const currentDesign = latestData.design ?? deck.design
      const design = {
        accent: nextAccent,
        ink: currentDesign?.ink ?? '#1a1a1a',
        muted: currentDesign?.muted ?? '#666666',
        font: currentDesign?.font ?? 'gothic' as const,
        ...(currentDesign?.footer ? { footer: currentDesign.footer } : {}),
        ...(currentDesign?.logo ? { logo: currentDesign.logo } : {}),
        visualStyle: currentDesign?.visualStyle ?? visualStyle,
      }
      const row = await artifactsApi.update(deck.id, {
        data: { ...latestData, kind: 'deck', theme: deck.theme, slides: latestSlides, design },
        summary: t('덱 기본 강조색 변경'),
        expectedVersion: latest.version,
      })
      deck.slides = latestSlides
      deck.design = design
      setBulkAccent(nextAccent)
      deck.version = row.version
      baseline.current = JSON.stringify(deck.slides)
    } catch (err) {
      setError(errorMessage(err, t('저장하지 못했습니다.')))
    } finally {
      savingLock.current = false
      setSaving(false)
    }
  }

  const saveDeckVisualStyle = async (nextStyle: NonNullable<DeckArtifact['design']>['visualStyle']) => {
    if (!nextStyle || savingLock.current || nextStyle === visualStyle) return
    savingLock.current = true
    setSaving(true)
    setError(null)
    try {
      const latest = await artifactsApi.get(deck.id)
      const latestData = (latest.data ?? {}) as Partial<DeckArtifact>
      const current = latestData.design ?? deck.design
      const design = {
        accent: current?.accent ?? bulkAccent,
        ink: current?.ink ?? '#1a1a1a',
        muted: current?.muted ?? '#666666',
        font: current?.font ?? 'gothic' as const,
        ...(current?.footer ? { footer: current.footer } : {}),
        ...(current?.logo ? { logo: current.logo } : {}),
        visualStyle: nextStyle,
      }
      const row = await artifactsApi.update(deck.id, {
        data: { ...latestData, kind: 'deck', theme: deck.theme, slides: latestData.slides ?? deck.slides, design },
        summary: t('덱 디자인 변경'),
        expectedVersion: latest.version,
      })
      deck.design = design
      deck.version = row.version
      setVisualStyle(nextStyle)
    } catch (err) {
      setError(errorMessage(err, t('디자인을 바꾸지 못했습니다.')))
    } finally {
      savingLock.current = false
      setSaving(false)
    }
  }

  const addSlide = (after: boolean) => {
    const at = after ? index + 1 : index
    const blank: Slide = {
      id: `sl${Date.now().toString(36)}`,
      layout: 'bullets',
      title: t('새 장'),
      bullets: [],
      notes: '',
    }
    const next = [...deck.slides.slice(0, at), blank, ...deck.slides.slice(at)]
    void restructure(next, t('{n}장 추가').replace('{n}', String(at + 1)), at)
  }

  const moveSlide = (by: -1 | 1) => {
    const to = index + by
    if (to < 0 || to >= deck.slides.length) return
    const next = [...deck.slides]
    ;[next[index], next[to]] = [next[to], next[index]]
    void restructure(next, t('{n}장 옮김').replace('{n}', String(index + 1)), to)
  }

  const duplicateSlide = () => {
    if (!slide) return
    const copy: Slide = {
      ...structuredClone(slide),
      id: `sl${Date.now().toString(36)}`,
      title: t('{name} 사본').replace('{name}', slide.title || t('제목 없음')),
      // 판정은 원본 문장과 검색 시점에 묶여 있으므로 새 장에서는 다시 한다.
      factCheck: undefined,
    }
    const next = [...deck.slides.slice(0, index + 1), copy, ...deck.slides.slice(index + 1)]
    void restructure(next, t('{n}장 복제').replace('{n}', String(index + 1)), index + 1)
  }

  const changeLayout = (layout: Slide['layout']) => {
    if (!slide || layout === slide.layout) return
    const next = deck.slides.map((row, i) => (i === index ? relayout(row, layout) : row))
    void restructure(next, t('{n}장 레이아웃 변경').replace('{n}', String(index + 1)), index)
  }

  /** 크게 / 보통 / 작게, on this slide only. */
  const setTextScale = (value: number) => {
    const next = deck.slides.map((row, i) =>
      i === index ? { ...row, textScale: value === 1 ? undefined : value } : row,
    )
    void restructure(next, t('{n}장 글자 크기').replace('{n}', String(index + 1)), index)
  }

  const removeSlide = () => {
    // The last one is not removable: a deck of no slides has nothing to open,
    // nothing to present and nothing to export, and the way to get rid of it is
    // to delete the deck.
    if (deck.slides.length <= 1) {
      setError(t('마지막 한 장은 지울 수 없습니다. 덱 자체를 지우려면 결과물 목록에서 지우세요.'))
      return
    }
    const next = deck.slides.filter((_, i) => i !== index)
    void restructure(next, t('{n}장 지움').replace('{n}', String(index + 1)), Math.max(0, index - 1))
  }

  const go = (i: number) => {
    setSelected(Math.max(0, Math.min(i, deck.slides.length - 1)))
    setEditing(false)
    setSlideDraft(null)
    // Picking one is the end of the errand: what you wanted to see is the
    // stage the drawer is covering.
    setRailToggled(false)
  }

  const saveReviewComments = async (comments: typeof reviewComments, summary: string) => {
    setReviewSaving(true)
    setError(null)
    try {
      const latest = await artifactsApi.get(deck.id)
      const latestData = (latest.data ?? {}) as Partial<DeckArtifact>
      const row = await artifactsApi.update(deck.id, {
        data: {
          ...latestData,
          kind: 'deck',
          theme: latestData.theme ?? deck.theme,
          slides: latestData.slides ?? deck.slides,
          reviewComments: comments,
        },
        summary,
        expectedVersion: latest.version,
      })
      deck.reviewComments = comments
      deck.version = row.version
      setReviewComments(comments)
    } catch (err) {
      setError(errorMessage(err, t('검토 메모를 저장하지 못했습니다.')))
    } finally {
      setReviewSaving(false)
    }
  }

  const addReviewComment = () => {
    const body = reviewDraft.trim()
    if (!slide || !body) return
    const next = [...reviewComments, {
      id: crypto.randomUUID(),
      slideId: slide.id,
      body,
      status: 'open' as const,
      createdAt: new Date().toISOString(),
    }]
    setReviewDraft('')
    void saveReviewComments(next, t('{n}번 장 검토 메모 추가').replace('{n}', String(index + 1)))
  }

  const toggleReviewComment = (id: string) => {
    const next = reviewComments.map((comment) => comment.id === id ? {
      ...comment,
      status: comment.status === 'open' ? 'resolved' as const : 'open' as const,
    } : comment)
    void saveReviewComments(next, t('검토 메모 상태 변경'))
  }

  const deleteReviewComment = (id: string) => {
    void saveReviewComments(reviewComments.filter((comment) => comment.id !== id), t('검토 메모 삭제'))
  }

  const loadLocalPicture = (file?: File) => {
    if (!file) return
    if (!/^image\/(?:png|jpeg|gif|webp)$/.test(file.type)) {
      setError(t('PNG, JPG, GIF, WebP 그림만 넣을 수 있습니다.'))
      return
    }
    if (file.size > 5 * 1024 * 1024) {
      setError(t('그림 파일은 5MB 이하여야 합니다.'))
      return
    }
    const reader = new FileReader()
    reader.onload = () => {
      const src = String(reader.result ?? '')
      if (!src.startsWith('data:image/')) return
      setSlideDraft((current) => current ? ({
        ...current,
        image: {
          src,
          caption: current.image?.caption ?? '',
          fit: current.image?.fit ?? 'contain',
          position: current.image?.position ?? 'right',
        },
      }) : current)
      setError(null)
    }
    reader.readAsDataURL(file)
  }

  const editedDeckSnapshot = slideDraft
    ? JSON.stringify(deck.slides.map((candidate, at) => at === index ? { ...slideDraft, notes } : candidate))
    : baseline.current
  // The bulk text box is intentionally parsed only when saving, so its value
  // can differ from `slideDraft` while the stage still shows the last
  // structured shape. That difference is nevertheless an unsaved edit: close,
  // restore and Escape must protect it just like a direct canvas edit.
  const textDraftChanged = Boolean(slideDraft && draft !== toLines(slideDraft))
  const hasUnsavedEdit = editing && (textDraftChanged || editedDeckSnapshot !== baseline.current)
  useEffect(() => {
    onDirtyChange?.(hasUnsavedEdit)
    return () => onDirtyChange?.(false)
  }, [hasUnsavedEdit, onDirtyChange])
  useEffect(() => {
    if (!hasUnsavedEdit) return
    const protect = (event: BeforeUnloadEvent) => {
      event.preventDefault()
      event.returnValue = ''
    }
    window.addEventListener('beforeunload', protect)
    return () => window.removeEventListener('beforeunload', protect)
  }, [hasUnsavedEdit])
  const discardOr = (action: 'cancel' | 'close') => {
    if (hasUnsavedEdit) return setDiscardAction(action)
    if (action === 'close') onClose?.()
    else { setEditing(false); setSlideDraft(null) }
  }

  /**
   * 편집 도구는 리본 안으로 들어간다.
   *
   * 보고서와 슬라이드의 메뉴가 서로 달랐다. Editing a slide added a second
   * sticky bar under a ribbon whose 편집 tab was empty — the same fourth-row
   * stacking the report had, and the reason the two panels did not look like
   * one product. The tab that says 편집 now holds the editing tools.
   */
  const editBar = !slide ? null : (
            <div
              aria-label={t('슬라이드 편집 도구')}
              className={editToolbarSlot
                ? 'flex items-center gap-1'
                : 'sticky top-0 z-10 flex min-h-12 items-center gap-1 overflow-x-auto border-b border-line bg-panel/95 px-3 py-1.5 shadow-sm backdrop-blur'}
            >
              <span className="mr-2 shrink-0 text-xs font-medium text-muted">
                {t('{n}번 장').replace('{n}', String(index + 1))}
              </span>
              {bulkMode && (
                <div className="flex shrink-0 items-center gap-1 rounded-control border border-accent/30 bg-accent-soft p-1" role="group" aria-label={t('여러 장 서식')}>
                  <span className="whitespace-nowrap px-1 text-2xs font-semibold text-accent">{t('{n}장 선택').replace('{n}', String(bulkSelected.size))}</span>
                  <Button size="sm" onClick={() => setBulkSelected(new Set(bulkSelected.size === deck.slides.length ? [] : deck.slides.map((candidate) => candidate.id)))}>
                    {bulkSelected.size === deck.slides.length ? t('전체 해제') : t('전체 선택')}
                  </Button>
                  <label className="relative grid size-8 cursor-pointer place-items-center rounded-control" title={t('일괄 강조색')}>
                    <Palette size={15} />
                    <input type="color" aria-label={t('일괄 강조색')} value={bulkAccent} onChange={(event) => setBulkAccent(event.target.value)} className="absolute inset-0 opacity-0" />
                    <span className="absolute bottom-1 h-0.5 w-4" style={{ background: bulkAccent }} />
                  </label>
                  <Button size="sm" disabled={!bulkSelected.size || saving} onClick={() => applyBulkSlides('accent')}>{t('선택 장에 색 적용')}</Button>
                  <select aria-label={t('일괄 글자 크기')} value={bulkScale} onChange={(event) => setBulkScale(event.target.value)} className="h-8 rounded-control border border-line bg-panel px-1 text-xs">
                    <option value="0.8">80%</option><option value="0.9">90%</option><option value="1">100%</option><option value="1.1">110%</option><option value="1.2">120%</option>
                  </select>
                  <Button size="sm" disabled={!bulkSelected.size || saving} onClick={() => applyBulkSlides('scale')}>{t('크기 적용')}</Button>
                  <Button size="sm" disabled={!bulkSelected.size || saving} onClick={() => applyBulkSlides('reset')}>{t('장별 서식 초기화')}</Button>
                  <Button size="sm" disabled={saving} onClick={() => void saveDeckAccent()}>{t('덱 기본색으로 저장')}</Button>
                  <Button size="sm" onClick={() => { setBulkMode(false); setBulkSelected(new Set()) }}><X size={13} />{t('끝내기')}</Button>
                </div>
              )}
              {editing ? (
                <>
                  <span className="shrink-0 rounded-full bg-elevated px-2 py-1 text-2xs font-medium text-muted" aria-live="polite">
                    {selectedElement === 'image' ? t('그림 선택됨') : selectedElement === 'title' ? t('제목 선택됨') : selectedElement === 'chart' ? t('차트 선택됨') : selectedElement === 'table' ? t('표 선택됨') : selectedElement === 'metrics' ? t('핵심 수치 선택됨') : selectedElement === 'cards' ? t('항목 선택됨') : selectedElement === 'content' ? t('본문 선택됨') : t('요소를 선택하세요')}
                  </span>
                  {overflowing && (
                    <Button
                      size="sm"
                      variant="ghost"
                      className="shrink-0 text-warning"
                      aria-label={t('잘린 내용 자동 맞춤')}
                      disabled={autoFitting}
                      onClick={() => { setFitLimitReached(false); setAutoFitting(true) }}
                    >
                      {autoFitting ? <Loader2 size={14} className="animate-spin" /> : <TriangleAlert size={14} />}
                      {autoFitting ? t('맞추는 중…') : t('잘린 내용 맞춤')}
                    </Button>
                  )}
                  {slideDraft && overflowRisk(slideDraft) && !fitLimitReached && (
                    <Button size="sm" variant="ghost" disabled={saving} onClick={splitOverflow} aria-label={t('이 장을 두 장으로 나누기')}>
                      <ListPlus size={14} />{t('장 나누기')}
                    </Button>
                  )}
                  {fitLimitReached && overflowing && (
                    <div className="flex shrink-0 items-center gap-1" role="alert">
                      <span className="text-2xs text-danger">{t('한 장에 넣기 어렵습니다.')}</span>
                      <Button size="sm" variant="ghost" disabled={saving} onClick={splitOverflow} aria-label={t('내용을 다음 장으로 나누기')}>
                        <ListPlus size={14} />{t('두 장으로 나누기')}
                      </Button>
                    </div>
                  )}
                  <div className="flex shrink-0 items-center gap-0.5 rounded-control border border-line bg-panel p-0.5" role="group" aria-label={t('색상 도구')}>
                    <label
                      className="relative grid size-8 cursor-pointer place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg"
                      title={t('이 장의 강조색')}
                    >
                      <Palette size={16} />
                      <input
                        type="color"
                        aria-label={t('도구막대 강조색')}
                        value={slideDraft?.accent ?? deck.design?.accent ?? '#5b5bd6'}
                        onChange={(event) => setSlideDraft((current) => current ? ({ ...current, accent: event.target.value }) : current)}
                        className="absolute inset-0 cursor-pointer opacity-0"
                      />
                      <span
                        aria-hidden="true"
                        className="absolute bottom-1 h-0.5 w-4 rounded-full"
                        style={{ backgroundColor: slideDraft?.accent ?? deck.design?.accent ?? '#5b5bd6' }}
                      />
                    </label>
                  </div>
                  <div className="mx-1 h-6 w-px shrink-0 bg-line" aria-hidden="true" />
                  <div className={cn('flex shrink-0 items-center gap-0.5 rounded-control border border-line bg-panel p-0.5', selectedElement === 'image' && 'pointer-events-none opacity-40')} role="group" aria-label={t('선택한 글자 서식')} aria-disabled={selectedElement === 'image'}>
                    <span className="whitespace-nowrap px-1.5 text-2xs font-medium text-muted">{t('선택 영역')}</span>
                    {([
                      ['bold', 'bold', t('선택한 글자 굵게'), <Bold key="bold" size={15} />],
                      ['italic', 'italic', t('선택한 글자 기울임'), <Italic key="italic" size={15} />],
                      ['underline', 'underline', t('선택한 글자 밑줄'), <Underline key="underline" size={15} />],
                    ] as const).map(([command, state, label, icon]) => (
                      <button
                        key={command}
                        type="button"
                        aria-label={label}
                        title={label}
                        aria-pressed={selectionFormat[state]}
                        onMouseDown={(event) => { event.preventDefault(); rememberFormattingRange() }}
                        onClick={() => formatSelection(command)}
                        className={cn('grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg', selectionFormat[state] && 'bg-accent/10 text-accent')}
                      >
                        {icon}
                      </button>
                    ))}
                    <select aria-label={t('선택한 글자 크기')} value={Math.min(200, Math.max(80, selectionFormat.size))} onMouseDown={rememberFormattingRange} onChange={(event) => sizeSelection(Number(event.target.value))} className="h-8 rounded-control border-0 bg-transparent px-1 text-xs text-fg outline-none hover:bg-elevated">
                      {[80, 100, 120, 140, 160, 200].map((value) => <option key={value} value={value}>{value}%</option>)}
                    </select>
                    <label className="relative grid size-8 cursor-pointer place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg" title={t('선택한 글자 색')} onMouseDown={rememberFormattingRange}>
                      <Palette size={15} />
                      <input type="color" aria-label={t('선택한 글자 색')} defaultValue="#1a1a1a" onChange={(event) => formatSelection('foreColor', event.target.value)} className="absolute inset-0 cursor-pointer opacity-0" />
                    </label>
                    <button type="button" aria-label={t('선택한 글자 서식 지우기')} title={t('선택한 글자 서식 지우기')} onMouseDown={(event) => { event.preventDefault(); rememberFormattingRange() }} onClick={() => formatSelection('removeFormat')} className="grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg"><Eraser size={15} /></button>
                  </div>
                  <div className="mx-1 h-6 w-px shrink-0 bg-line" aria-hidden="true" />
                  <div className="flex shrink-0 items-center gap-0.5 rounded-control border border-line bg-panel p-0.5" role="group" aria-label={t('레이아웃')}>
                    <LayoutTemplate size={15} className="ml-1.5 shrink-0 text-muted" aria-hidden="true" />
                    <select
                      aria-label={t('도구막대 레이아웃')}
                      value={slideDraft?.layout ?? slide.layout}
                      onChange={(event) => setSlideDraft((current) => {
                        if (!current) return current
                        const next = relayout(current, event.target.value as Slide['layout'])
                        setDraft(toLines(next))
                        return next
                      })}
                      className="h-8 max-w-28 rounded-control border-0 bg-transparent px-1 text-sm text-fg outline-none hover:bg-elevated"
                    >
                      {LAYOUTS.map((candidate) => (
                        <option key={candidate.id} value={candidate.id}>{t(candidate.label)}</option>
                      ))}
                    </select>
                  </div>
                  <div className="mx-1 h-6 w-px shrink-0 bg-line" aria-hidden="true" />
                  <div className="flex shrink-0 items-center gap-0.5 rounded-control border border-line bg-panel p-0.5" role="group" aria-label={t('그림 도구')}>
                    <label
                      className="grid size-8 cursor-pointer place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg"
                      title={slideDraft?.image ? t('그림 교체') : t('그림 업로드')}
                    >
                      <ImagePlus size={16} />
                      <input
                        type="file"
                        accept="image/png,image/jpeg,image/gif,image/webp"
                        aria-label={t('로컬 그림 업로드')}
                        className="sr-only"
                        onChange={(event) => loadLocalPicture(event.target.files?.[0])}
                      />
                    </label>
                  {slideDraft?.image && selectedElement === 'image' && (
                    <>
                      <button
                        type="button"
                        aria-label={t('그림 전체 표시')}
                        aria-pressed={(slideDraft.image.fit ?? 'contain') === 'contain'}
                        title={t('그림 전체 표시')}
                        onClick={() => setSlideDraft((current) => current?.image ? ({ ...current, image: { ...current.image, fit: 'contain' } }) : current)}
                        className={cn('grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg', (slideDraft.image.fit ?? 'contain') === 'contain' && 'bg-accent/10 text-accent')}
                      >
                        <Grid2x2 size={15} />
                      </button>
                      <button
                        type="button"
                        aria-label={t('그림 영역 채우기')}
                        aria-pressed={slideDraft.image.fit === 'cover'}
                        title={t('그림 영역 채우기')}
                        onClick={() => setSlideDraft((current) => current?.image ? ({ ...current, image: { ...current.image, fit: 'cover' } }) : current)}
                        className={cn('grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg', slideDraft.image.fit === 'cover' && 'bg-accent/10 text-accent')}
                      >
                        <Maximize size={15} />
                      </button>
                      <span className="mx-0.5 h-5 w-px bg-line" aria-hidden="true" />
                      <select aria-label={t('그림 크기')} value={slideDraft.image.size ?? 'medium'} onChange={(event) => setSlideDraft((current) => current?.image ? ({ ...current, image: { ...current.image, size: event.target.value as 'small' | 'medium' | 'large' } }) : current)} className="h-8 rounded-control border-0 bg-transparent px-1 text-xs text-fg outline-none hover:bg-elevated">
                        <option value="small">{t('작게')}</option>
                        <option value="medium">{t('보통')}</option>
                        <option value="large">{t('크게')}</option>
                      </select>
                      <button type="button" aria-label={t('그림 왼쪽')} aria-pressed={(slideDraft.image.position ?? 'right') === 'left'} title={t('그림 왼쪽')} onClick={() => setSlideDraft((current) => current?.image ? ({ ...current, image: { ...current.image, position: 'left' } }) : current)} className={cn('grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg', slideDraft.image.position === 'left' && 'bg-accent/10 text-accent')}><PanelLeft size={15} /></button>
                      <button type="button" aria-label={t('그림 오른쪽')} aria-pressed={(slideDraft.image.position ?? 'right') === 'right'} title={t('그림 오른쪽')} onClick={() => setSlideDraft((current) => current?.image ? ({ ...current, image: { ...current.image, position: 'right' } }) : current)} className={cn('grid size-8 place-items-center rounded-control text-muted hover:bg-elevated hover:text-fg', (slideDraft.image.position ?? 'right') === 'right' && 'bg-accent/10 text-accent')}><PanelRight size={15} /></button>
                      <button type="button" aria-label={t('그림 제거')} title={t('그림 제거')} onClick={() => { setSlideDraft((current) => current ? ({ ...current, image: undefined }) : current); setSelectedElement(null) }} className="grid size-8 place-items-center rounded-control text-muted hover:bg-danger/10 hover:text-danger"><Trash2 size={15} /></button>
                    </>
                  )}
                  </div>
                  <div className="ml-auto flex shrink-0 items-center gap-1">
                    <Button size="sm" disabled={editHistory.length < 2} onClick={undoEdit} aria-label={t('슬라이드 편집 실행 취소')}><Undo2 size={14} /></Button>
                    <Button size="sm" disabled={!editFuture.length} onClick={redoEdit} aria-label={t('슬라이드 편집 다시 실행')}><Redo2 size={14} /></Button>
                  </div>
                </>
              ) : null}
            </div>
  )
  const editTools = !editBar || !(editing || bulkMode)
    ? null
    : editToolbarSlot ? createPortal(editBar, editToolbarSlot) : editBar

  return (
    <div
      ref={panel.ref}
      className="flex h-full min-h-0 flex-col"
      onKeyDown={(event) => {
        if (!(event.ctrlKey || event.metaKey) || event.key.toLowerCase() !== 's') return
        event.preventDefault()
        if (hasUnsavedEdit) void save()
      }}
    >
      {/* 접히는 머리말. 390px 에서는 이 줄이 화면보다 넓고, flex 는 그럴 때
          자식을 줄여서 "내보내기" 를 한 자씩 네 줄로 세운다. 제목은 줄어들되
          버튼은 줄어들지 않는 것이 옳은 순서다. */}
      <header className="relative z-40 flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2.5 max-sm:px-2">
        <div className="flex min-w-0 flex-1 items-center gap-2 max-sm:basis-full">
          <Presentation size={15} className="shrink-0 text-accent" />
          <p className="min-w-0 flex-1 truncate whitespace-nowrap text-base font-medium" title={deck.title}>
            {deck.title}
          </p>
        </div>
        <QuickAccess label={t('빠른 도구')}>
          {editing && <>
            <Button size="sm" variant="ghost" disabled={saving} onClick={() => discardOr('cancel')} aria-label={t('편집 취소')}>
              <X size={14} />{t('취소')}
            </Button>
            <Button variant="primary" size="sm" disabled={saving} onClick={() => void save()} aria-label={t('저장')} aria-keyshortcuts="Control+S Meta+S">
              {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}{t('저장')}
            </Button>
          </>}
          <PanelControls mode={width.mode} onCycle={width.cycle} onClose={() => discardOr('close')} />
        </QuickAccess>
        <ArtifactRibbon
          label={t('슬라이드 메뉴')}
          tabs={(editing
            // 편집 중에도 검토는 남긴다. 저장 시점이 그 안에 있고, 되돌리고
            // 싶어지는 때는 대개 고치고 있는 중이다 — 고치던 것을 저장하거나
            // 버려야만 되돌릴 곳에 닿는다면 그 길은 없는 것과 같다.
            ? [
                { id: 'home', label: t('편집') },
                { id: 'review', label: t('검토') },
                { id: 'file', label: t('파일') },
              ]
            : [
                { id: 'home', label: t('홈') }, { id: 'insert', label: t('삽입') },
                { id: 'review', label: t('검토') }, { id: 'view', label: t('보기') },
                { id: 'show', label: t('슬라이드 쇼') }, { id: 'file', label: t('파일') },
              ]) as Array<{ id: 'home' | 'insert' | 'review' | 'view' | 'show' | 'file'; label: string }>}
          active={ribbon}
          onChange={setRibbon}
        >
        {/* 장수 배지가 여기 있었다. 누를 수 없는 숫자였고, 바로 옆 목록이
            여덟 장을 그려 놓고 「1/8」 이라고 적어 두는 자리다 — 리본은
            할 일을 두는 곳이지 이미 보이는 것을 다시 적는 곳이 아니다. */}
        {/* 장마다 눌러 보지 않아도 확인이 필요한 곳이 몇 군데인지 보이게 한다 */}
        {ribbon === 'review' && (weakSlides.length > 0 || overflowRisks.length > 0) && <RibbonGroup label={t('검사')}>
        {weakSlides.length > 0 && (
          <Button
            size="sm"
            onClick={() => go(weakSlides[0])}
            title={t('{list}번 장').replace('{list}', weakSlides.map((i) => i + 1).join(', '))}
          >
            <TriangleAlert size={13} className="text-warn" />
            {t('확인 필요 {n}장').replace('{n}', String(weakSlides.length))}
          </Button>
        )}
        {overflowRisks.length > 0 && (
          <Button
            size="sm"
            onClick={() => go(overflowRisks.find((candidate) => candidate > index) ?? overflowRisks[0])}
            title={t('잘림 위험: {list}번 장').replace('{list}', overflowRisks.map((i) => i + 1).join(', '))}
            aria-label={t('잘림 위험 장으로 이동')}
          >
            <TriangleAlert size={13} className="text-warn" />
            {t('잘림 위험 {n}장').replace('{n}', String(overflowRisks.length))}
          </Button>
        )}
        </RibbonGroup>}
        {/* 셋뿐인 선택은 메뉴 뒤에 두지 않는다.
            드롭다운은 고른 것 하나만 보이고 나머지는 눌러 봐야 안다 — 세
            가지를 나란히 세우면 무엇을 고를 수 있는지가 곧 화면이고, 지금
            어느 것인지도 같은 자리에서 읽힌다. 색은 여섯이고 이름보다 색이
            빠르니 그대로 메뉴에 둔다. */}
        {ribbon === 'home' && (editing || bulkMode) && (
          <RibbonGroup label={t('슬라이드 편집')}>
            <div ref={setEditToolbarSlot} className="flex items-center" />
          </RibbonGroup>
        )}
        {ribbon === 'home' && !editing && <RibbonGroup label={t('인상')}>
          {([
            ['editorial', '편집형', '선과 넓은 여백'],
            ['poster', '포스터형', '강한 색면과 큰 번호'],
            ['minimal', '미니멀', '옅은 색과 절제된 제목'],
          ] as const).map(([value, label, why]) => (
            <Button
              key={value}
              size="sm"
              disabled={saving}
              aria-pressed={visualStyle === value}
              title={t(why)}
              onClick={() => void saveDeckVisualStyle(value)}
            >
              {t(label)}
            </Button>
          ))}
        </RibbonGroup>}
        {ribbon === 'home' && !editing && <RibbonGroup label={t('색')}><Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" disabled={saving} aria-label={t('덱 색 고르기')}>
              <span className="size-3 rounded-full ring-1 ring-black/10" style={{ backgroundColor: bulkAccent }} />
              {t('색')}
            </Button>
          )}
        >
          {([
            ['#5b5bd6', '보라'], ['#1f6feb', '파랑'], ['#0f766e', '청록'],
            ['#c2410c', '주황'], ['#b91c1c', '빨강'], ['#334155', '먹색'],
          ] as const).map(([colour, label]) => (
            <MenuItem key={colour} icon={<span className="size-3 rounded-full ring-1 ring-black/10" style={{ backgroundColor: colour }} />} checked={bulkAccent.toLowerCase() === colour} onClick={() => void saveDeckAccent(colour)}>
              {t(label)}
            </MenuItem>
          ))}
        </Dropdown></RibbonGroup>}
        {ribbon === 'home' && !editing && <RibbonGroup label={t('슬라이드 작업')}><Button
          size="sm"
          variant={slide?.bullets?.includes('이 장을 쓰지 못했습니다.') || slide?.body?.includes('이 장을 쓰지 못했습니다.') ? 'primary' : 'secondary'}
          disabled={writing || rewritingSlide}
          onClick={() => void regenerateSlide()}
          aria-label={t('이 장 다시 만들기')}
        >
          <RefreshCw size={14} className={rewritingSlide ? 'animate-spin' : undefined} />
          {rewritingSlide ? t('다시 만드는 중…') : t('이 장 다시 만들기')}
        </Button>
        <Button size="sm" variant="primary" disabled={writing || rewritingSlide} onClick={() => void startEditing()} aria-label={t('편집 도구')}>
          <Pencil size={14} />{t('편집 도구')}
        </Button></RibbonGroup>}
        {ribbon === 'insert' && !editing && slide && <RibbonGroup label={t('콘텐츠')}>
          <SlidePicture deck={deck} slide={slide} />
        </RibbonGroup>}
        {ribbon === 'review' && !editing && <RibbonGroup label={t('문서 검사')}><LintFindings
          findings={deck.lint}
          artifact={deck}
          onFix={fixFinding}
          onFixAll={fixAllFindings}
        /></RibbonGroup>}
        {ribbon === 'review' && !editing && <RibbonGroup label={t('메모')}><Button
          size="sm"
          aria-label={t('검토 메모')}
          aria-pressed={reviewOpen}
          onClick={() => setReviewOpen((open) => !open)}
        >
          <MessageSquare size={13} />
          {t('검토')} {reviewComments.filter((comment) => comment.status === 'open').length}
        </Button></RibbonGroup>}
        {/* Only where there is a drawer to open: with the rail standing beside
            the stage this button opens what is already on screen. */}
        {/* 보고서의 보기 탭이 「목차」 하나로 서 있는 자리, 그 짝.
            좁을 때만 세웠더니 넓은 화면에서는 보기 탭을 눌러도 아무것도 없는
            탭이었다 — 그 자리를 채우고 있던 것은 장수 배지 하나였고, 그것은
            누를 수 없는 숫자였다. 목록을 접었다 펴는 것은 넓은 화면에서도
            할 일이다: 한 장을 크게 볼 때 옆줄은 자리를 차지한다. */}
        {ribbon === 'view' && <RibbonGroup label={t('탐색')}>
          <Button
            size="sm"
            aria-label={t('장 목록')}
            aria-pressed={railShown}
            title={t('장 목록을 접었다 폅니다')}
            onClick={() => setRailToggled((pressed) => !pressed)}
          >
            <Rows3 size={13} />
            {t('장 목록')} {deck.slides.length ? index + 1 : 0}/{deck.slides.length}
          </Button>
        </RibbonGroup>}
        {/* 발표 모드. 덱은 방에서 보이는 크기로 한 번 넘겨 봐야 끝난다 */}
        {ribbon === 'show' && !editing && <RibbonGroup label={t('재생')}><Button size="sm" disabled={writing} onClick={() => setPresenting(true)}>
          <Play size={13} />
          {t('발표')}
        </Button></RibbonGroup>}
        {ribbon === 'file' && !editing && <RibbonGroup label={t('내보내기')}><Dropdown
          align="right"
          trigger={() => (
            <Button size="sm" disabled={writing}>
              <Download size={14} />
              {t('내보내기')}
            </Button>
          )}
        >
          {exportWarningCount > 0 && (
            <>
              <MenuLabel>{t('내보내기 전 확인 {n}건').replace('{n}', String(exportWarningCount))}</MenuLabel>
              {overflowRisks.length > 0 && (
                <MenuItem icon={<TriangleAlert size={14} />} onClick={() => go(overflowRisks[0])}>
                  {t('잘림 위험 {n}장 확인').replace('{n}', String(overflowRisks.length))}
                </MenuItem>
              )}
              {weakSlides.length > 0 && (
                <MenuItem icon={<ShieldQuestion size={14} />} onClick={() => go(weakSlides[0])}>
                  {t('근거 확인 필요 {n}장').replace('{n}', String(weakSlides.length))}
                </MenuItem>
              )}
              {unresolvedReviews.length > 0 && (
                <MenuItem icon={<MessageSquare size={14} />} onClick={() => {
                  const target = deck.slides.findIndex((candidate) => candidate.id === unresolvedReviews[0].slideId)
                  if (target >= 0) go(target)
                  setReviewOpen(true)
                }}>
                  {t('미해결 검토 메모 {n}개').replace('{n}', String(unresolvedReviews.length))}
                </MenuItem>
              )}
            </>
          )}
          <MenuLabel>{t('형식 선택')}</MenuLabel>
          <MenuItem hint="PPTX" onClick={() => void download(deck.id, 'pptx', deck.title)}>
            PowerPoint
          </MenuItem>
          <MenuItem hint="PDF" onClick={() => void download(deck.id, 'pdf', deck.title)}>
            {t('PDF (발표용)')}
          </MenuItem>
          <MenuItem hint="MD" onClick={() => void download(deck.id, 'md', deck.title)}>
            {t('텍스트 (노트 포함)')}
          </MenuItem>
        </Dropdown></RibbonGroup>}
        {/* 저장 시점. 한 장을 고쳐 놓고 원래가 나았다는 것은 고친 뒤에야
            알게 되고, 그때 되돌릴 곳이 이 줄 말고는 없다. */}
        {/* 편집 중에도 선다. 되돌리고 싶어지는 때는 대개 고치고 있는 중이고,
            `VersionHistory` 는 저장하지 않은 편집이 있으면 그것부터 말한다. */}
        {ribbon === 'review' && <RibbonGroup label={t('버전')}><VersionHistory
          artifact={deck}
          hasUnsavedChanges={hasUnsavedEdit}
          currentData={deck}
          // 되돌린 덱의 몇째 장인지는 편집기가 열릴 때 잡아 둔 것과 다르다.
          // 열어 둔 초안을 그대로 저장하면 방금 되돌린 장을 덮어쓴다.
          onRestored={() => {
            setEditing(false)
            setSlideDraft(null)
            setDraft('')
            setNotes('')
            baseline.current = ''
            setError(null)
          }}
        /></RibbonGroup>}
        </ArtifactRibbon>
      </header>

      <div className="relative flex min-h-0 flex-1">
        {reviewOpen && (
          <aside aria-label={t('검토 메모')} className="absolute inset-y-0 right-0 z-30 flex w-80 max-w-full flex-col border-l border-line bg-panel shadow-float">
            <div className="flex items-center gap-2 border-b border-line p-3">
              <MessageSquare size={14} className="text-accent" />
              <h3 className="flex-1 text-sm font-semibold">{t('검토 메모')}</h3>
              <button aria-label={t('검토 메모 닫기')} onClick={() => setReviewOpen(false)} className="rounded-control p-1 hover:bg-elevated">
                <X size={15} />
              </button>
            </div>
            <div className="border-b border-line p-3">
              <p className="mb-2 text-xs text-faint">{t('{n}번 장에 메모').replace('{n}', String(index + 1))}</p>
              <Textarea rows={3} value={reviewDraft} onChange={(event) => setReviewDraft(event.target.value)} aria-label={t('메모 내용')} placeholder={t('수정할 점이나 확인할 내용을 적으세요')} />
              <Button size="sm" className="mt-2 w-full" disabled={!reviewDraft.trim() || reviewSaving} onClick={addReviewComment}>
                {reviewSaving ? <Loader2 size={13} className="animate-spin" /> : <MessageSquare size={13} />}
                {t('메모 추가')}
              </Button>
            </div>
            <div className="min-h-0 flex-1 space-y-2 overflow-y-auto p-3">
              {reviewComments.length === 0 && <p className="text-sm text-faint">{t('아직 검토 메모가 없습니다.')}</p>}
              {reviewComments.map((comment) => {
                const slideIndex = deck.slides.findIndex((candidate) => candidate.id === comment.slideId)
                return (
                  <article key={comment.id} className={cn('rounded-card border p-3', comment.status === 'resolved' ? 'border-line bg-elevated/40 opacity-65' : 'border-line bg-panel')}>
                    <button className="text-xs font-semibold text-accent hover:underline" onClick={() => slideIndex >= 0 && go(slideIndex)}>
                      {slideIndex >= 0 ? t('{n}번 장').replace('{n}', String(slideIndex + 1)) : t('삭제된 장')}
                    </button>
                    <p className="mt-1 whitespace-pre-wrap text-sm text-fg">{comment.body}</p>
                    <div className="mt-2 flex items-center gap-3">
                      <button className="text-xs text-muted hover:text-fg" disabled={reviewSaving} onClick={() => toggleReviewComment(comment.id)}>
                        {comment.status === 'open' ? t('해결로 표시') : t('다시 열기')}
                      </button>
                      <button aria-label={t('메모 삭제')} className="ml-auto text-xs text-faint hover:text-danger" disabled={reviewSaving} onClick={() => deleteReviewComment(comment.id)}>
                        {t('삭제')}
                      </button>
                    </div>
                  </article>
                )
              })}
            </div>
          </aside>
        )}
        {panel.narrow && railShown && (
          <button
            aria-label={t('장 목록 닫기')}
            className="absolute inset-0 z-10 bg-black/30"
            onClick={() => setRailToggled(false)}
          />
        )}
        {/* ── 장 목록 레일 ───────────────────────────────────────────────
            Slides are ordered argument, and the order is the thing under
            revision for as long as the deck exists. A rail keeps the whole
            sequence beside the slide being worked on rather than below it. */}
        <nav
          className={cn(
            'w-[132px] shrink-0 flex-col border-r border-line bg-sidebar/40',
            railShown
              ? panel.narrow
                ? 'absolute inset-y-0 left-0 z-20 flex shadow-overlay'
                : 'flex'
              : 'hidden',
          )}
        >
          <div className="flex items-center gap-0.5 border-b border-line px-1.5 py-1.5">
            {(
              [
                { id: 'thumbs', icon: Grid2x2, label: '그림으로' },
                { id: 'outline', icon: Rows3, label: '차례로' },
              ] as const
            ).map((v) => (
              <button
                key={v.id}
                onClick={() => setRail(v.id)}
                aria-pressed={rail === v.id}
                aria-label={t(v.label)}
                title={t(v.label)}
                className={cn(
                  'grid size-6 place-items-center rounded-control transition-colors',
                  rail === v.id ? 'bg-elevated text-fg' : 'text-faint hover:text-fg',
                )}
              >
                <v.icon size={13} />
              </button>
            ))}
            <button
              onClick={() => { setBulkMode((value) => !value); setBulkSelected(new Set()) }}
              aria-pressed={bulkMode}
              aria-label={t('여러 장 선택')}
              title={t('여러 장 선택')}
              className={cn('grid size-6 place-items-center rounded-control transition-colors', bulkMode ? 'bg-accent/10 text-accent' : 'text-faint hover:text-fg')}
            >
              <Rows3 size={13} />
            </button>
            <span className="ml-auto pr-1 text-xs text-faint tabular-nums">
              {deck.slides.length ? index + 1 : 0}/{deck.slides.length}
            </span>
          </div>
          <div className="min-h-0 flex-1 space-y-1.5 overflow-y-auto p-1.5">
            {deck.slides.map((s, i) => {
              const weak = s.factCheck?.claims.some((c) => c.verdict !== 'supported')
              const openReviews = reviewComments.filter((comment) => comment.slideId === s.id && comment.status === 'open').length
              return rail === 'thumbs' ? (
                <div key={s.id} className="relative">
                <button
                  onClick={() => go(i)}
                  aria-label={t('{n}번 장').replace('{n}', String(i + 1))}
                  aria-current={i === index}
                  className={cn(
                    'relative block aspect-video w-full overflow-hidden rounded-control border-2 bg-white transition-colors',
                    i === index ? 'border-accent' : 'border-line hover:border-line-strong',
                  )}
                >
                  <SlideThumbnail
                    deck={deck}
                    slide={editing && i === index && slideDraft ? slideDraft : s}
                    index={i}
                    writing={writing}
                  />
                  <span className="absolute bottom-0.5 left-0.5 rounded bg-black/55 px-1 text-2xs font-medium text-white tabular-nums">
                    {i + 1}
                  </span>
                  {weak && (
                    <span className="absolute top-0.5 right-0.5 grid size-3.5 place-items-center rounded-full bg-warn text-white">
                      <TriangleAlert size={9} />
                    </span>
                  )}
                  {openReviews > 0 && (
                    <span aria-label={t('미해결 메모 {n}개').replace('{n}', String(openReviews))} className="absolute bottom-0.5 right-0.5 grid min-w-4 place-items-center rounded-full bg-accent px-1 text-2xs font-semibold text-white">
                      {openReviews}
                    </span>
                  )}
                </button>
                {bulkMode && (
                  <label className="absolute left-0.5 top-0.5 z-10 grid size-5 cursor-pointer place-items-center rounded bg-white/90 shadow-sm" onClick={(event) => event.stopPropagation()}>
                    <input type="checkbox" className="size-3.5 accent-[var(--color-accent)]" checked={bulkSelected.has(s.id)} onChange={() => toggleBulkSlide(s.id)} aria-label={t('{n}번 장 선택').replace('{n}', String(i + 1))} />
                  </label>
                )}
                </div>
              ) : (
                <div key={s.id} className="flex items-start">
                {bulkMode && <input type="checkbox" className="mt-1 size-3.5 shrink-0 accent-[var(--color-accent)]" checked={bulkSelected.has(s.id)} onChange={() => toggleBulkSlide(s.id)} aria-label={t('{n}번 장 선택').replace('{n}', String(i + 1))} />}
                <button
                  onClick={() => go(i)}
                  aria-current={i === index}
                  className={cn(
                    'flex w-full items-start gap-1.5 rounded-control px-1.5 py-1 text-left text-xs leading-snug transition-colors',
                    i === index ? 'bg-elevated text-fg' : 'text-muted hover:bg-elevated hover:text-fg',
                  )}
                >
                  <span className="shrink-0 text-faint tabular-nums">{i + 1}</span>
                  <span className="min-w-0 flex-1 line-clamp-2">{s.title}</span>
                  {weak && <TriangleAlert size={10} className="mt-0.5 shrink-0 text-warn" />}
                  {openReviews > 0 && <span aria-label={t('미해결 메모 {n}개').replace('{n}', String(openReviews))} className="shrink-0 rounded-full bg-accent px-1 text-2xs font-semibold text-white">{openReviews}</span>}
                </button>
                </div>
              )
            })}
          </div>
        </nav>

        {/* ── 무대 ─────────────────────────────────────────────────────── */}
        {/* The slide takes the room, the notes take the rest.
            Both used to sit in one band capped at `max-w-lg` — 32rem — so on a
            940px panel the slide was drawn a third of the width it had and
            everything below the notes was empty. That cap made sense when this
            lived beside a transcript in a 330px column; it stopped making sense
            the moment the panel could be widened, and nothing followed.
            A column instead: the slide keeps its 16:9 across the full width,
            and the notes are their own band under a rule, growing into whatever
            height is left rather than trailing the slide as a caption. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          {editTools}
          {/* 패널을 넓히면 슬라이드가 가로를 다 차지하며 키를 키웠고, 발표
              노트는 화면 아래로 밀려 스크롤해야 보였다. 이제 슬라이드 줄은
              높이의 3/4 를, 노트 띠는 1/4 을 나눠 갖고, 슬라이드는 그 줄의
              폭과 높이 중 먼저 닿는 쪽에 맞춰 16:9 로 줄어든다 — 슬라이드와
              노트가 한 화면에 들어온다. 편집 폼이 길어 넘칠 때만 세로로
              흐른다. */}
          <div className="flex min-h-[45%] flex-[3] basis-0 items-center gap-2 p-4">
              <button
                onClick={() => go(index - 1)}
                disabled={index === 0}
                aria-label={t('이전 장')}
                className="grid size-7 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg disabled:opacity-30"
              >
                <ChevronLeft size={16} />
              </button>
              <div ref={fit.ref} className="flex min-h-0 min-w-0 flex-1 items-center justify-center self-stretch">
              <div
                ref={stage.ref}
                style={{ width: fit.width ?? '100%' }}
                className="aspect-video min-w-0 overflow-hidden rounded-card border border-line shadow-raised"
              >
                {slide ? (
                  <SlideView
                    slide={editing && slideDraft ? slideDraft : slide}
                    scale={stage.scale}
                    writing={writing}
                    deckTitle={deck.title}
                    brand={deck.design ?? undefined}
                    index={index}
                    total={deck.slides.length}
                    /* 텍스트 수정을 누른 동안에는 슬라이드가 곧 편집기다.
                       고친 것은 아래 상자와 같은 초안으로 흘러가므로 저장은
                       한 곳에서만 일어난다. */
                    editable={editing}
                    selectedElement={selectedElement}
                    onSelectElement={setSelectedElement}
                    onOverflow={setOverflowing}
                    onEdit={(next) => {
                      setSlideDraft(next)
                      setDraft(toLines(next))
                    }}
                  />
                ) : (
                  <div className="grid size-full place-items-center bg-white text-base text-[#6b6b6b]">
                    {/* 흰 종이 위의 회색. 슬라이드는 테마를 따르지 않으므로
                        색이 값이다 — #999 는 흰 바탕에서 2.85:1 이라 WCAG 의
                        4.5:1 에 못 미쳤다. #6b6b6b 는 5.3:1 이면서 여전히
                        물러나 있다. */}
                    {t('구성을 잡는 중…')}
                  </div>
                )}
              </div>
              </div>
              <button
                onClick={() => go(index + 1)}
                disabled={index >= deck.slides.length - 1}
                aria-label={t('다음 장')}
                className="grid size-7 shrink-0 place-items-center rounded-control text-muted transition-colors hover:bg-elevated hover:text-fg disabled:opacity-30"
              >
                <ChevronRight size={16} />
              </button>
          </div>

          {slide && (
            <div className="flex min-h-40 flex-1 basis-0 flex-col border-t border-line bg-elevated/40 p-4">
              <div className="flex items-center gap-2">
                  <StickyNote size={13} className="shrink-0 text-faint" />
                  <span className="flex-1 text-xs font-semibold tracking-wide text-faint uppercase">
                    {t('발표 노트')}
                  </span>
                  {!editing && (
                    <Dropdown
                      align="right"
                      trigger={() => (
                        <Button variant="ghost" size="sm" disabled={writing || saving}>
                          <ListPlus size={13} />
                          {t('장 편집')}
                        </Button>
                      )}
                    >
                      <MenuLabel>{t('{n}번 장').replace('{n}', String(index + 1))}</MenuLabel>
                      <MenuItem onClick={() => addSlide(false)}>{t('앞에 장 추가')}</MenuItem>
                      <MenuItem onClick={() => addSlide(true)}>{t('뒤에 장 추가')}</MenuItem>
                      <MenuItem onClick={duplicateSlide}>{t('이 장 복제')}</MenuItem>
                      <MenuItem onClick={() => moveSlide(-1)} disabled={index === 0}>
                        {t('위로 옮기기')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => moveSlide(1)}
                        disabled={index >= deck.slides.length - 1}
                      >
                        {t('아래로 옮기기')}
                      </MenuItem>
                      <MenuItem onClick={removeSlide}>{t('이 장 지우기')}</MenuItem>
                      <MenuLabel>{t('레이아웃')}</MenuLabel>
                      {LAYOUTS.map((candidate) => (
                        <MenuItem
                          key={candidate.id}
                          checked={candidate.id === slide.layout}
                          onClick={() => changeLayout(candidate.id)}
                        >
                          {t(candidate.label)}
                        </MenuItem>
                      ))}
                      <MenuLabel>{t('글자 크기')}</MenuLabel>
                      <MenuItem
                        onClick={() => setTextScale(1.25)}
                        hint={(slide.textScale ?? 1) > 1 ? '✓' : undefined}
                      >
                        {t('크게')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => setTextScale(1)}
                        hint={(slide.textScale ?? 1) === 1 ? '✓' : undefined}
                      >
                        {t('보통')}
                      </MenuItem>
                      <MenuItem
                        onClick={() => setTextScale(0.8)}
                        hint={(slide.textScale ?? 1) < 1 ? '✓' : undefined}
                      >
                        {t('작게')}
                      </MenuItem>
                    </Dropdown>
                  )}
                  {!editing && (
                    <Button
                      variant="ghost"
                      size="sm"
                      disabled={writing || checking}
                      onClick={() => void runFactCheck(slide.id)}
                    >
                      {checking ? (
                        <Loader2 size={13} className="animate-spin" />
                      ) : (
                        <ShieldQuestion size={13} />
                      )}
                      {t('팩트체크')}
                    </Button>
                  )}
                </div>

                {editing ? (
                  <div className="mt-2 space-y-2">
                    <Textarea
                      rows={5}
                      value={draft}
                      onChange={(e) => setDraft(e.target.value)}
                      aria-label={t('슬라이드 텍스트')}
                    />
                    <p className="text-xs text-faint">
                      {t('위 슬라이드에서 글자를 눌러 바로 고칠 수 있습니다. 아래 상자는 한 번에 훑어 고칠 때 씁니다 — 첫 줄이 제목, 나머지 줄이 각각 한 항목이고, | 로 나눈 줄은 표의 한 행입니다.')}
                    </p>
                    {slideDraft && ['table', 'metrics', 'cards'].includes(selectedElement ?? '') && (
                      <SlideDataEditor
                        slide={slideDraft}
                        onChange={(next) => {
                          setSlideDraft(next)
                          setDraft(toLines(next))
                        }}
                      />
                    )}
                    {slideDraft?.image && selectedElement === 'image' && (
                      <div className="rounded-card border border-line bg-panel p-3">
                        <label className="block text-xs text-muted">
                          {t('그림 설명')}
                          <Input
                            aria-label={t('그림 설명')}
                            value={slideDraft.image.caption ?? ''}
                            onChange={(event) => setSlideDraft((current) => current?.image ? ({
                              ...current,
                              image: { ...current.image, caption: event.target.value },
                            }) : current)}
                          />
                        </label>
                      </div>
                    )}
                    {slideDraft?.layout === 'chart' && slideDraft.chart && selectedElement === 'chart' && (
                      <div className="space-y-3 rounded-card border border-line bg-panel p-3">
                        <div className="flex flex-wrap items-end gap-3">
                          <label className="text-xs text-muted">
                            {t('차트 종류')}
                            <select
                              aria-label={t('차트 종류')}
                              value={slideDraft.chart.kind}
                              onChange={(event) => setSlideDraft((current) => current?.chart ? ({
                                ...current,
                                chart: { ...current.chart, kind: event.target.value as 'bar' | 'line' },
                              }) : current)}
                              className="mt-1 block h-9 rounded-control border border-line bg-panel px-2 text-sm"
                            >
                              <option value="bar">{t('막대')}</option>
                              <option value="line">{t('꺾은선')}</option>
                            </select>
                          </label>
                          <label className="min-w-28 flex-1 text-xs text-muted">
                            {t('단위')}
                            <Input
                              aria-label={t('차트 단위')}
                              value={slideDraft.chart.unit ?? ''}
                              onChange={(event) => setSlideDraft((current) => current?.chart ? ({
                                ...current,
                                chart: { ...current.chart, unit: event.target.value || undefined },
                              }) : current)}
                            />
                          </label>
                        </div>
                        <label className="block text-xs text-muted">
                          {t('가로축 항목')}
                          <Input
                            aria-label={t('가로축 항목')}
                            value={slideDraft.chart.categories.join(', ')}
                            onChange={(event) => {
                              const categories = event.target.value.split(',').map((value) => value.trim()).filter(Boolean)
                              setSlideDraft((current) => current?.chart ? ({
                                ...current,
                                chart: {
                                  ...current.chart,
                                  categories,
                                  series: current.chart.series.map((series) => ({
                                    ...series,
                                    values: categories.map((_, i) => series.values[i] ?? 0),
                                  })),
                                },
                              }) : current)
                            }}
                          />
                          <span className="mt-1 block text-2xs text-faint">{t('쉼표로 나눠 입력하세요')}</span>
                        </label>
                        {slideDraft.chart.series.map((series, seriesIndex) => (
                          <div key={seriesIndex} className="grid gap-2 sm:grid-cols-[minmax(7rem,0.4fr)_1fr_auto]">
                            <Input
                              aria-label={t('{n}번째 계열 이름').replace('{n}', String(seriesIndex + 1))}
                              value={series.name}
                              onChange={(event) => setSlideDraft((current) => current?.chart ? ({
                                ...current,
                                chart: { ...current.chart, series: current.chart.series.map((row, i) => i === seriesIndex ? { ...row, name: event.target.value } : row) },
                              }) : current)}
                            />
                            <Input
                              aria-label={t('{n}번째 계열 값').replace('{n}', String(seriesIndex + 1))}
                              value={series.values.join(', ')}
                              onChange={(event) => {
                                const values = event.target.value.split(',').map((value) => Number(value.trim())).filter(Number.isFinite)
                                setSlideDraft((current) => current?.chart ? ({
                                  ...current,
                                  chart: { ...current.chart, series: current.chart.series.map((row, i) => i === seriesIndex ? { ...row, values } : row) },
                                }) : current)
                              }}
                            />
                            <Button
                              size="sm"
                              disabled={(slideDraft.chart?.series.length ?? 0) <= 1}
                              onClick={() => setSlideDraft((current) => current?.chart ? ({
                                ...current,
                                chart: { ...current.chart, series: current.chart.series.filter((_, i) => i !== seriesIndex) },
                              }) : current)}
                            >
                              {t('삭제')}
                            </Button>
                          </div>
                        ))}
                        <Button
                          size="sm"
                          onClick={() => setSlideDraft((current) => current?.chart ? ({
                            ...current,
                            chart: {
                              ...current.chart,
                              series: [...current.chart.series, {
                                name: t('새 계열'),
                                values: current.chart.categories.map(() => 0),
                              }],
                            },
                          }) : current)}
                        >
                          {t('계열 추가')}
                        </Button>
                      </div>
                    )}
                    <Textarea
                      rows={3}
                      value={notes}
                      onChange={(e) => setNotes(e.target.value)}
                      placeholder={t('발표 노트')}
                      aria-label={t('발표 노트')}
                    />
                    {error && (
                      <div role="alert" className="rounded-card border border-danger/30 bg-panel px-3 py-2">
                        <p className="text-sm text-danger">{error}</p>
                        {error.includes(t('다른 곳에서 이미 수정')) && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            <Button size="sm" variant="secondary" onClick={() => void copySlideRecovery()}>
                              <Copy size={13} />
                              {recoveryCopied ? t('복사됨') : t('내 편집 내용 복사')}
                            </Button>
                            <Button size="sm" variant="primary" disabled={saving} onClick={() => void reloadLatestDeck()}>
                              <RefreshCw size={13} />
                              {t('최신본 불러오기')}
                            </Button>
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                ) : (
                  /* The band owns the rest of the panel, so the note scrolls
                     inside it rather than pushing the slide off the top. */
                  <div className="mt-1.5 min-h-0 flex-1 overflow-y-auto">
                    <p className="text-base text-muted">
                      {slide.notes || <span className="text-faint">{t('노트 없음')}</span>}
                    </p>
                    {slide.factCheck?.status === 'done' && (
                      <FactCheckResults check={slide.factCheck} />
                    )}
                    {error && !editing && <p className="mt-2 text-sm text-danger">{error}</p>}
                  </div>
                )}
            </div>
          )}
        </div>
      </div>

      {presenting && (
        <PresentMode
          deck={deck}
          index={index}
          onIndex={setSelected}
          onClose={() => setPresenting(false)}
        />
      )}
      <ConfirmDialog
        open={discardAction !== null}
        onClose={() => setDiscardAction(null)}
        title={t('저장하지 않은 변경 내용이 있습니다')}
        description={t('계속하면 이 장에서 바꾼 내용이 사라집니다.')}
        confirmLabel={discardAction === 'close' ? t('저장하지 않고 닫기') : t('변경 내용 버리기')}
        onConfirm={() => {
          const action = discardAction
          setEditing(false)
          setSlideDraft(null)
          if (action === 'close') onClose?.()
        }}
      />
    </div>
  )
}
