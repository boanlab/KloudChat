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
  ChevronDown,
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
import { FRAMES, drawIntoFitting, frameElement, paperStyles, rasterise, slideTheme } from '@/lib/mermaid'
import { cn } from '@/lib/utils'
import type { DeckArtifact, LintFinding, Slide } from '@/types'
import { FactCheckResults } from '@/components/artifacts/FactCheckResults'
import { LintFindings, byWhere, fixNote } from '@/components/artifacts/LintFindings'
import { VersionHistory } from '@/components/artifacts/VersionHistory'
import { useStore } from '@/store/useStore'
import { SlideChart } from '@/components/slides/SlideChart'
import { BODY_BOTTOM, BODY_TOP, BULLET_GAP, FLOOR_PT, LEADING, PAD_X, TYPE, columnShares, tablePad, tableSize, titlePt, units } from '@/components/slides/typeScale'
import { useT } from '@/lib/useT'
import { PicturePicker } from '@/components/artifacts/PicturePicker'
import { ArtifactRibbon, QuickAccess, RibbonGroup } from '@/components/artifacts/ArtifactRibbon'
import { copyText } from '@/lib/clipboard'

/** Whether a slide has content in any field; mirrors `deck.has_content` on the server. */
export function hasContent(slide: Slide): boolean {
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

/** Cheap overflow estimate for the whole deck; the selected slide is measured in pixels. */
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

/** Layouts made of [left, right] pairs; see `Slide.bands`. */
const PAIRED = ['bands', 'tiles', 'timeline', 'steps', 'cards'] as const

/** Layouts that count as content without a body: cover, dividers, agenda. */
const STRUCTURAL: Slide['layout'][] = ['title', 'section', 'agenda']
/** Layouts drawn as a cover. */
const COVERS: Slide['layout'][] = ['title', 'section', 'closing']

// Korean line breaking, inherited from each slide root; `_deck/seed.html` matches.
const KOREAN_WRAP = { wordBreak: 'keep-all', overflowWrap: 'break-word' } as const
/** Titles wrap with even lines, so the last line is never one stranded word. */
const BALANCED = { ...KOREAN_WRAP, textWrap: 'balance' } as const

type VisualStyle = 'editorial' | 'poster' | 'minimal' | 'dark' | 'split' | 'warm' | 'mono' | 'pastel' | 'forest' | 'slate' | 'paper'

/** Visual style parameters; `deck_export` keeps an identical table. */
interface Look {
  bg: string
  ink: string
  muted: string
  faint: string
  hair: string
  /** Accent percentage in the tint. */
  tint: number
  card: 'filled' | 'outlined'
  radius: number
  badge: 'square' | 'circle'
  cover: 'gradient' | 'wash' | 'glow' | 'split' | 'paper' | 'brackets'
  ornament: 'top-band' | 'left-bar' | 'corner-circle' | 'bottom-rule' | 'gutter' | 'frame' | 'bottom-band'
  /** Title weight, title letter-spacing in slide units, and body line height; `deck_export` reads `leading`. */
  titleWeight: number
  tracking: number
  leading: number
}

const LOOKS: Record<VisualStyle, Look> = {
  editorial: { bg: '#ffffff', ink: '#1a1a1a', muted: '#666666', faint: '#8a8a8a', hair: '#e6e6e6', tint: 7, card: 'filled', radius: 0, badge: 'square', cover: 'gradient', ornament: 'top-band', titleWeight: 700, tracking: -0.2, leading: 1.6 },
  poster: { bg: '#f7f3ed', ink: '#1a1a1a', muted: '#666666', faint: '#8a8a8a', hair: '#e2ddd4', tint: 9, card: 'filled', radius: 0, badge: 'square', cover: 'gradient', ornament: 'left-bar', titleWeight: 800, tracking: -0.4, leading: 1.55 },
  minimal: { bg: '#ffffff', ink: '#1a1a1a', muted: '#666666', faint: '#8a8a8a', hair: '#ececec', tint: 5, card: 'outlined', radius: 0, badge: 'square', cover: 'wash', ornament: 'corner-circle', titleWeight: 600, tracking: -0.3, leading: 1.7 },
  dark: { bg: '#0f172a', ink: '#f1f5f9', muted: '#a3b1c6', faint: '#64748b', hair: '#273449', tint: 22, card: 'filled', radius: 6, badge: 'circle', cover: 'glow', ornament: 'bottom-rule', titleWeight: 700, tracking: 0, leading: 1.6 },
  split: { bg: '#ffffff', ink: '#111827', muted: '#5b6472', faint: '#9aa3b2', hair: '#e5e7eb', tint: 6, card: 'outlined', radius: 0, badge: 'square', cover: 'split', ornament: 'gutter', titleWeight: 700, tracking: -0.2, leading: 1.6 },
  warm: { bg: '#f6f1e8', ink: '#3f3328', muted: '#7a6a5a', faint: '#a8998a', hair: '#e2d8c8', tint: 12, card: 'filled', radius: 10, badge: 'circle', cover: 'paper', ornament: 'bottom-band', titleWeight: 700, tracking: 0, leading: 1.7 },
  mono: { bg: '#ffffff', ink: '#111111', muted: '#555555', faint: '#8a8a8a', hair: '#111111', tint: 0, card: 'outlined', radius: 0, badge: 'square', cover: 'brackets', ornament: 'frame', titleWeight: 700, tracking: 0.3, leading: 1.6 },
  pastel: { bg: '#f3f0fa', ink: '#2b2540', muted: '#6b6480', faint: '#9a93ad', hair: '#e3ddf0', tint: 14, card: 'filled', radius: 12, badge: 'circle', cover: 'wash', ornament: 'corner-circle', titleWeight: 600, tracking: -0.2, leading: 1.65 },
  forest: { bg: '#f1f5f0', ink: '#1f2d22', muted: '#5c6b5e', faint: '#8e9a90', hair: '#d9e2da', tint: 10, card: 'filled', radius: 6, badge: 'square', cover: 'gradient', ornament: 'left-bar', titleWeight: 700, tracking: -0.2, leading: 1.6 },
  slate: { bg: '#eef1f5', ink: '#1c2431', muted: '#55617a', faint: '#8b96ab', hair: '#d5dbe6', tint: 8, card: 'outlined', radius: 2, badge: 'square', cover: 'split', ornament: 'bottom-rule', titleWeight: 700, tracking: 0, leading: 1.6 },
  paper: { bg: '#fbfaf6', ink: '#2a2622', muted: '#6b655c', faint: '#9b948a', hair: '#e6e1d6', tint: 6, card: 'outlined', radius: 0, badge: 'square', cover: 'paper', ornament: 'frame', titleWeight: 600, tracking: 0, leading: 1.7 },
}
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

/** The looks in the order the picker shows them, with what each one does to a slide. */
const LOOK_CHOICES: { id: VisualStyle; label: string; why: string }[] = [
  { id: 'editorial', label: '편집형', why: '흰 바탕, 위쪽 색 띠, 선과 넓은 여백' },
  { id: 'minimal', label: '미니멀', why: '옅은 색과 절제된 제목, 모서리의 동그라미' },
  { id: 'poster', label: '포스터형', why: '강한 색면 표지와 큰 번호' },
  { id: 'split', label: '분할형', why: '왼쪽 색면과 큰 번호, 회색 선' },
  { id: 'dark', label: '다크', why: '어두운 바탕에 빛나는 강조색' },
  { id: 'slate', label: '강철', why: '차가운 회색 바탕, 분할 표지, 얇은 선' },
  { id: 'warm', label: '따뜻한', why: '크림색 종이 바탕과 둥근 상자' },
  { id: 'pastel', label: '파스텔', why: '연한 보랏빛 바탕과 둥근 모서리' },
  { id: 'forest', label: '숲', why: '초록빛 바탕과 색면 표지, 왼쪽 세로 띠' },
  { id: 'paper', label: '학술', why: '종이 바탕에 테두리 선, 넓은 행간' },
  { id: 'mono', label: '흑백', why: '검정 선과 큰 제목, 강조색 없음' },
]

/** A thumbnail of a look: its cover on the left, a body slide on the right. */
function LookSwatch({ look, accent, size = 1 }: { look: Look; accent: string; size?: number }) {
  const w = 30 * size
  const h = 17 * size
  const mono = look.hair === look.ink
  const tone = mono ? look.ink : accent
  const coverBackground =
    look.cover === 'gradient' ? `linear-gradient(135deg, ${tone}, color-mix(in srgb, ${tone} 60%, #111827))`
    : look.cover === 'glow' ? `radial-gradient(circle at 80% 90%, color-mix(in srgb, ${tone} 70%, transparent), ${look.bg} 70%)`
    : look.cover === 'wash' ? `linear-gradient(145deg, color-mix(in srgb, ${tone} 14%, #fff), ${look.bg} 70%)`
    : look.bg
  const onAccent = look.cover === 'gradient' || look.cover === 'glow'
  const coverInk = onAccent ? '#fff' : look.ink
  return (
    <span className="inline-flex shrink-0 overflow-hidden rounded-[3px] ring-1 ring-black/10" style={{ width: w * 2 + 2, height: h, gap: 2, background: look.hair }} aria-hidden>
      <span className="relative block" style={{ width: w, height: h, background: coverBackground }}>
        {look.cover === 'split' && <span className="absolute inset-y-0 left-0" style={{ width: w * 0.36, background: tone }} />}
        {look.cover === 'paper' && <span className="absolute rounded-full" style={{ width: h * 0.7, height: h * 0.7, right: -h * 0.15, top: h * 0.1, background: tone }} />}
        {look.cover === 'brackets' && <span className="absolute" style={{ left: 2, top: 2, width: 4, height: 4, borderLeft: `1px solid ${look.ink}`, borderTop: `1px solid ${look.ink}` }} />}
        <span className="absolute" style={{ left: look.cover === 'split' ? w * 0.44 : w * 0.16, top: h * 0.42, width: w * 0.42, height: 2 * size, background: coverInk }} />
        <span className="absolute" style={{ left: look.cover === 'split' ? w * 0.44 : w * 0.16, top: h * 0.62, width: w * 0.3, height: 1.5 * size, background: coverInk, opacity: 0.6 }} />
      </span>
      <span className="relative block" style={{ width: w, height: h, background: look.bg }}>
        {look.ornament === 'top-band' && <span className="absolute inset-x-0 top-0" style={{ height: 1.5 * size, background: tone }} />}
        {look.ornament === 'left-bar' && <span className="absolute inset-y-0 left-0" style={{ width: 1.5 * size, background: tone }} />}
        {look.ornament === 'corner-circle' && <span className="absolute rounded-full" style={{ width: h * 0.5, height: h * 0.5, right: -h * 0.2, top: -h * 0.25, background: `color-mix(in srgb, ${tone} 14%, transparent)` }} />}
        {look.ornament === 'bottom-rule' && <span className="absolute inset-x-0 bottom-0" style={{ height: 1.5 * size, background: `linear-gradient(90deg, ${tone}, transparent)` }} />}
        {look.ornament === 'gutter' && <span className="absolute inset-y-0 left-0" style={{ width: 1 * size, background: tone }} />}
        {look.ornament === 'frame' && <span className="absolute" style={{ inset: 1.5 * size, border: `0.5px solid ${look.ink}` }} />}
        {look.ornament === 'bottom-band' && <span className="absolute inset-x-0 bottom-0" style={{ height: h * 0.14, background: `color-mix(in srgb, ${tone} ${look.tint || 8}%, ${look.bg})` }} />}
        <span className="absolute" style={{ left: w * 0.16, top: h * 0.2, width: w * 0.5, height: 2 * size, background: look.ink }} />
        <span className="absolute" style={{ left: w * 0.16, top: h * 0.45, width: w * 0.66, height: 1 * size, background: look.muted }} />
        <span className="absolute" style={{ left: w * 0.16, top: h * 0.6, width: w * 0.58, height: 1 * size, background: look.muted }} />
        <span className="absolute" style={{ left: w * 0.16, top: h * 0.75, width: w * 0.62, height: 1 * size, background: look.muted }} />
      </span>
    </span>
  )
}

/** Accent colours by name; `deck._THEMES` on the server lists the same nine. */
const ACCENTS: [string, string][] = [
  ['#1e3a8a', '남색'], ['#1f6feb', '파랑'], ['#0f766e', '청록'], ['#15803d', '초록'],
  ['#5b5bd6', '보라'], ['#a21caf', '자주'], ['#c2410c', '주황'], ['#b91c1c', '빨강'], ['#334155', '먹'],
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
    // `execCommand(fontSize)` emits `<font size>`; converted to a relative size
    // so it scales with the slide.
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

/** The slide's paired layout, or `null`. */
function pairedLayout(slide: Slide): Paired | null {
  return (PAIRED as readonly string[]).includes(slide.layout)
    ? (slide.layout as Paired)
    : null
}

/** Pairs on the field the layout names; the other paired fields cleared. */
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

/** Slide matching a finding's `where` title: exact first, then ignoring whitespace. */
function slideFor(slides: Slide[], where: string): Slide | undefined {
  if (!where) return undefined
  const exact = slides.find((s) => s.title === where)
  if (exact) return exact
  const loose = (text: string) => text.replace(/\s+/g, '')
  return slides.find((s) => loose(s.title) === loose(where))
}

/** Keeps the half-typed working copy unless a different slide arrived. */
function pick(next: Slide, working: Slide): Slide {
  return next.id === working.id ? working : next
}

/**
 * One slide in a 400x225 unit space, scaled. Geometry matches `deck_export.py`.
 * Also draws gallery thumbnails.
 */
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
  artifactId,
}: {
  slide: Slide
  scale?: number
  /** Whether the deck is still being written; decides what an empty slide says. */
  writing?: boolean
  /** Footer fields; omitted for thumbnails. */
  deckTitle?: string
  index?: number
  total?: number
  /** Deck-level design: accent, footer line, logo, visual style. */
  brand?: { accent?: string; footer?: string; logo?: string; visualStyle?: VisualStyle }
  /** Makes the text `contentEditable`; edits come back through `onEdit` as a whole slide. */
  editable?: boolean
  onEdit?: (next: Slide) => void
  selectedElement?: SlideElement | null
  onSelectElement?: (element: SlideElement) => void
  onOverflow?: (overflowing: boolean) => void
  /** Set on the editor's stage only: the browser stores its raster of a slide's own figure onto this deck. */
  artifactId?: string
}) {
  const t = useT()
  // The slide as typed; a ref so re-renders do not move the caret.
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
  const visualStyle: VisualStyle = brand?.visualStyle && brand.visualStyle in LOOKS ? brand.visualStyle : 'editorial'
  const look = LOOKS[visualStyle]
  // `mono` uses ink for the accent.
  const accent = visualStyle === 'mono' ? look.ink : (slide.accent ?? brand?.accent ?? 'var(--accent)')
  const px = (n: number) => `${n * scale}px`
  // Type size only; `textScale` does not touch padding or gaps. `deck_export` applies the same factor.
  // Never under 12pt, whatever the scale; `deck_export` floors the same way.
  const type = (n: number) => `${Math.max(n * (slide.textScale ?? 1), units(FLOOR_PT)) * scale}px`
  // A size from the shared table, in points.
  const pt = (points: number) => type(units(points))
  // The title keeps its own size: 32pt, or 30 or 28 when it would otherwise wrap.
  const titleSize = `${units(titlePt(slide.title ?? '')) * scale}px`
  // Tint percentages match `deck_export._mix`.
  const tint = look.tint ? `color-mix(in srgb, ${accent} ${look.tint}%, ${look.bg})` : '#f2f2f2'
  const hair = look.hair
  const boxed = (extra?: React.CSSProperties): React.CSSProperties =>
    look.card === 'outlined'
      ? { background: look.bg, border: `1px solid ${hair}`, borderRadius: px(look.radius), ...extra }
      : { background: tint, borderRadius: px(look.radius), ...extra }
  const badge = (size: number): React.CSSProperties => ({
    width: px(size),
    height: px(size),
    background: accent,
    color: visualStyle === 'mono' ? look.bg : '#fff',
    borderRadius: look.badge === 'circle' ? '50%' : px(look.radius / 2),
  })
  const rows = slide.rows ?? []
  const metrics = slide.metrics ?? []
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
  // Two columns only with enough bullets to fill them.
  const twoColumn = slide.layout === 'two-column' && (slide.bullets?.length ?? 0) >= 5
  // Table type size from the row count, so it stays on the slide; `deck_export` scales the same way.
  const dense = { size: tableSize(rows.length), pad: tablePad(rows.length), shares: columnShares(rows) }
  // Band and timeline rows divide the body height; matches `deck_export`
  // (`min(72, room/n)` and `min(56, room/n)` in its 540-unit slide).
  const stack = (() => {
    const body = BODY_BOTTOM - BODY_TOP - 4
    const count = Math.max(pairs.length, 1)
    const gap = 4
    const height = Math.min(30, (body - gap * (count - 1)) / count)
    const band = Math.max(units(TYPE.bandMin), Math.min(units(TYPE.bandMax), height / 3))
    const step = Math.min(23, body / count)
    const line = Math.max(units(TYPE.lineMin), Math.min(units(TYPE.lineMax), step / 2.3))
    // Left label width from character count (a Korean glyph is about 1em),
    // bounded between the floor and a third of the slide.
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

  if (COVERS.includes(slide.layout)) {
    const closing = slide.layout === 'closing'
    // Cover composition per look; `deck_export` composes the same six.
    const onAccent = look.cover === 'gradient' || look.cover === 'glow'
    const coverInk = onAccent ? '#fff' : look.ink
    const coverMuted = onAccent ? 'rgba(255,255,255,0.8)' : look.muted
    const coverMark = onAccent ? 'rgba(255,255,255,0.9)' : accent
    const background =
      look.cover === 'gradient'
        ? `linear-gradient(135deg, ${accent}, color-mix(in srgb, ${accent} ${visualStyle === 'poster' ? 48 : 62}%, #111827))`
        : look.cover === 'wash'
          ? `linear-gradient(145deg, color-mix(in srgb, ${accent} 10%, #fff), #fff 70%)`
          : look.bg
    const inSplit = look.cover === 'split'
    return (
      <div
        ref={canvas}
        className="relative flex size-full flex-col justify-center overflow-hidden"
        style={{
          background,
          padding: px(34),
          paddingLeft: inSplit ? px(34 + 160 + 20) : px(34),
          ...KOREAN_WRAP,
        }}
      >
        {visualStyle === 'poster' && <div className="absolute rounded-full border border-white/20" style={{ width: px(150), height: px(150), right: px(-35), top: px(-45) }} />}
        {look.cover === 'glow' && (
          <div className="absolute rounded-full" style={{ width: px(260), height: px(260), right: px(-90), bottom: px(-120), background: `radial-gradient(circle at 40% 40%, color-mix(in srgb, ${accent} 70%, transparent), transparent 68%)` }} />
        )}
        {look.cover === 'paper' && (
          <div className="absolute rounded-full" style={{ width: px(190), height: px(190), right: px(-60), top: px(18), background: accent, opacity: 0.9 }} />
        )}
        {inSplit && (
          <>
            <div className="absolute inset-y-0 left-0" style={{ width: px(160), background: accent }} />
            <div className="absolute font-black tabular-nums" style={{ left: px(26), bottom: px(22), fontSize: pt(TYPE.splitNumber), color: 'rgba(255,255,255,0.35)', lineHeight: 1 }}>
              {slide.layout === 'section' && slide.number ? slide.number.replace('.', '') : closing ? 'END' : '01'}
            </div>
          </>
        )}
        {look.cover === 'brackets' && (
          <>
            <div className="absolute" style={{ left: px(26), top: px(28), width: px(22), height: px(30), borderLeft: `${px(2.5)} solid ${look.ink}`, borderTop: `${px(2.5)} solid ${look.ink}` }} />
            <div className="absolute" style={{ right: px(26), bottom: px(28), width: px(22), height: px(30), borderRight: `${px(2.5)} solid ${look.ink}`, borderBottom: `${px(2.5)} solid ${look.ink}` }} />
          </>
        )}
        {slide.layout === 'section' && slide.number && !inSplit ? (
          <div
            style={{
              fontSize: pt(TYPE.sectionNumber),
              fontWeight: 700,
              color: onAccent ? 'rgba(255,255,255,0.7)' : accent,
              marginBottom: px(6),
            }}
          >
            {slide.number}
          </div>
        ) : look.cover === 'brackets' ? null : (
          <div
            style={{
              width: px(44),
              height: px(3),
              background: coverMark,
              marginBottom: px(18),
            }}
          />
        )}
        <h3
          style={{ ...BALANCED, fontSize: pt(closing ? TYPE.closing : visualStyle === 'poster' ? TYPE.coverPoster : visualStyle === 'mono' ? TYPE.coverMono : TYPE.cover), fontWeight: visualStyle === 'minimal' ? 600 : look.titleWeight + 50, lineHeight: 1.2, color: coverInk, maxWidth: visualStyle === 'editorial' ? '78%' : look.cover === 'paper' ? '62%' : undefined, letterSpacing: px(look.tracking * 1.5) }}
          {...typed('title', (text) => ({ title: text }))}
          {...selectable('title')}
        >
          {rich('title', slide.title || (closing ? t('마무리') : ''))}
        </h3>
        {closing && slide.bullets && slide.bullets.length > 0 && (
          <ul style={{ marginTop: px(12), fontSize: pt(TYPE.closingBullets), lineHeight: LEADING.body, color: onAccent ? 'rgba(255,255,255,0.92)' : look.ink }}>
            {slide.bullets.slice(0, 3).map((b, i) => (
              <li key={i} className="flex gap-2">
                <span style={{ color: onAccent ? 'rgba(255,255,255,0.6)' : accent }}>—</span>
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
            style={{ left: inSplit ? px(34 + 180) : px(34), bottom: px(30), fontSize: pt(TYPE.closingBody), fontWeight: 700, color: onAccent ? '#fff' : accent }}
            {...typed('body', (text) => ({ body: text }))}
          >
            {rich('body', slide.body)}
          </p>
        ) : slide.body && (
          <p
            style={{
              fontSize: pt(TYPE.coverBody),
              marginTop: px(12),
              lineHeight: 1.5,
              color: coverMuted,
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
      className="relative flex size-full flex-col overflow-hidden"
      style={{
        background: look.bg,
        color: look.ink,
        // Title at 24; the body box ends at `BODY_BOTTOM`, above the foot rule.
        paddingTop: px(24),
        paddingLeft: px(look.ornament === 'gutter' ? 40 : PAD_X),
        paddingRight: px(PAD_X),
        paddingBottom: px(225 - BODY_BOTTOM),
        ...KOREAN_WRAP,
      }}
    >
      {/* Body-slide ornament, one per look. */}
      {look.ornament === 'top-band' && <div className="absolute inset-x-0 top-0" style={{ height: px(6), background: accent }} />}
      {look.ornament === 'left-bar' && <div className="absolute inset-y-0 left-0" style={{ width: px(8), background: accent }} />}
      {look.ornament === 'corner-circle' && <div className="absolute rounded-full" style={{ width: px(70), height: px(70), right: px(-30), top: px(-35), background: `color-mix(in srgb, ${accent} 12%, transparent)` }} />}
      {look.ornament === 'bottom-rule' && <div className="absolute inset-x-0 bottom-0" style={{ height: px(4), background: `linear-gradient(90deg, ${accent}, transparent)` }} />}
      {look.ornament === 'gutter' && (
        <>
          <div className="absolute inset-y-0 left-0" style={{ width: px(3), background: accent }} />
          {index !== undefined && <span className="absolute font-black tabular-nums" style={{ left: px(12), bottom: px(30), fontSize: pt(TYPE.gutterNumber), color: accent, lineHeight: 1 }}>{String(index + 1).padStart(2, '0')}</span>}
        </>
      )}
      {look.ornament === 'frame' && <div className="pointer-events-none absolute" style={{ inset: px(10), border: `1px solid ${look.ink}` }} />}
      {look.ornament === 'bottom-band' && <div className="absolute inset-x-0 bottom-0" style={{ height: px(22), background: tint }} />}
      {visualStyle === 'poster' && index !== undefined && <span className="absolute font-black tabular-nums" style={{ right: px(22), top: px(13), fontSize: pt(TYPE.posterNumber), color: `color-mix(in srgb, ${accent} 14%, transparent)` }}>{String(index + 1).padStart(2, '0')}</span>}

      {slide.layout === 'statement' ? (
        <div className="flex flex-1 flex-col items-center justify-center text-center">
          <div style={{ width: px(26), height: px(2), background: accent, marginBottom: px(14) }} />
          <p
            style={{ ...BALANCED, fontSize: pt(TYPE.statement), fontWeight: look.titleWeight + 50, lineHeight: 1.25, color: accent, maxWidth: '86%', letterSpacing: px(look.tracking) }}
            {...typed('title', (text) => ({ title: text }))}
            {...selectable('title')}
          >
            {rich('title', slide.title)}
          </p>
          {slide.body && (
            <p
              style={{ fontSize: pt(TYPE.statementBody), marginTop: px(10), color: look.muted, lineHeight: 1.5, maxWidth: '74%' }}
              {...typed('body', (text) => ({ body: text }))}
            >
              {rich('body', slide.body)}
            </p>
          )}
        </div>
      ) : slide.layout === 'quote' && slide.body ? (
        <div className="flex flex-1 flex-col justify-center">
          <p style={{ fontSize: pt(TYPE.quote), fontWeight: 600, lineHeight: 1.4, color: accent }}>
            “<span {...typed('body', (text) => ({ body: text }))}>{rich('body', slide.body)}</span>”
          </p>
          <p
            style={{ fontSize: pt(TYPE.quoteBy), marginTop: px(10), color: look.muted }}
            {...typed('title', (text) => ({ title: text }))}
          >
            {rich('title', slide.title)}
          </p>
        </div>
      ) : (
        <div className="flex min-h-0 flex-1 flex-col">
          <h3
            style={{ ...BALANCED, fontSize: titleSize, fontWeight: look.titleWeight, lineHeight: LEADING.title, letterSpacing: px(look.tracking) }}
            {...typed('title', (text) => ({ title: text }))}
            {...selectable('title')}
          >
            {rich('title', slide.title)}
          </h3>
          <div
            style={{
              width: px(26),
              height: px(2),
              background: accent,
              marginTop: px(6),
              marginBottom: px(12),
            }}
          />
          <div className="flex min-h-0 flex-1" style={{ gap: px(16) }}>
            <div
              className={cn(
                'flex min-w-0 flex-1 flex-col',
                // Paired shapes cannot overflow (see `stack`), so they centre;
                // bullets can, and a centred overflow clips at both ends.
                PAIRED.includes(slide.layout as (typeof PAIRED)[number]) && 'justify-center',
              )}
              data-overflow-box
              style={{ order: slide.image?.position === 'left' ? 2 : 1 }}
              {...selectable(contentElement)}
            >
              {pairs.length > 0 && slide.layout === 'bands' && (
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
                          color: visualStyle === 'mono' ? look.bg : '#fff',
                          fontSize: type(stack.band),
                          fontWeight: 700,
                          padding: `${px(stack.pad)} ${px(4)}`,
                          borderRadius: px(look.radius / 2),
                        }}
                      >
                        {name}
                      </div>
                      <div
                        className="flex min-w-0 flex-1 items-center"
                        style={boxed({
                          fontSize: type(stack.band),
                          lineHeight: 1.5,
                          padding: `${px(stack.pad)} ${px(12)}`,
                        })}
                      >
                        {text}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'tiles' && (
                <div className="flex" style={{ gap: px(11), marginTop: px(8) }}>
                  {pairs.map(([mark, name], i) => (
                    <div key={i} className="flex min-w-0 flex-1 flex-col items-center">
                      <div
                        className="grid aspect-square w-full place-items-center"
                        style={{
                          maxWidth: px(62),
                          background: accent,
                          color: visualStyle === 'mono' ? look.bg : '#fff',
                          fontSize: pt(TYPE.tileMark),
                          fontWeight: 700,
                          borderRadius: look.badge === 'circle' ? '50%' : px(look.radius / 2),
                        }}
                      >
                        {mark}
                      </div>
                      <div
                        className="text-center"
                        style={{ fontSize: pt(TYPE.tileName), marginTop: px(7), color: look.muted }}
                      >
                        {name}
                      </div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'steps' && (
                <div className="relative flex" style={{ gap: px(8), marginTop: px(8) }}>
                  {pairs.length > 1 && (
                    <div className="absolute" style={{ left: px(11), right: `calc(${100 / pairs.length}% - ${px(11)})`, top: px(10), height: px(1), background: tint }} />
                  )}
                  {pairs.map(([name, text], i) => (
                    <div key={i} className="relative flex min-w-0 flex-1 flex-col">
                      <div
                        className="grid place-items-center"
                        style={{ ...badge(22), fontSize: pt(TYPE.stepBadge), fontWeight: 700 }}
                      >
                        {String(i + 1).padStart(2, '0')}
                      </div>
                      <div style={{ fontSize: pt(TYPE.stepName), fontWeight: 700, marginTop: px(7), lineHeight: 1.3 }}>{name}</div>
                      <div style={{ fontSize: pt(TYPE.stepText), marginTop: px(3), color: look.muted, lineHeight: LEADING.stepText }}>{text}</div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'cards' && (
                <div className="flex" style={{ gap: px(8), marginTop: px(4), height: px(100) }}>
                  {pairs.map(([name, text], i) => (
                    <div
                      key={i}
                      className="flex min-w-0 flex-1 flex-col overflow-hidden"
                      style={boxed({ borderTop: `${px(2)} solid ${accent}`, padding: `${px(8)} ${px(7)}` })}
                    >
                      <div style={{ fontSize: pt(TYPE.cardName), fontWeight: 700, color: accent, lineHeight: 1.3 }}>{name}</div>
                      <div style={{ fontSize: pt(TYPE.cardText), marginTop: px(5), lineHeight: LEADING.cardText }}>{text}</div>
                    </div>
                  ))}
                </div>
              )}
              {pairs.length > 0 && slide.layout === 'timeline' && (
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
                <div className="flex flex-1 flex-col justify-center">
                  <div className="flex items-baseline" style={{ gap: px(8) }}>
                    <span
                      style={{ fontSize: pt(TYPE.bigNumber), fontWeight: 750, lineHeight: 1, color: accent }}
                      {...typed('metrics.0.0', (text) => ({ metrics: [[text, working.current.metrics?.[0]?.[1] ?? '']] }))}
                    >
                      {rich('metrics.0.0', metrics[0][0])}
                    </span>
                    <span
                      style={{ fontSize: pt(TYPE.bigNumberLabel), color: look.muted }}
                      {...typed('metrics.0.1', (text) => ({ metrics: [[working.current.metrics?.[0]?.[0] ?? '', text]] }))}
                    >
                      {rich('metrics.0.1', metrics[0][1])}
                    </span>
                  </div>
                  {slide.body && (
                    <p style={{ fontSize: pt(TYPE.bigNumberBody), marginTop: px(10), lineHeight: 1.5 }} {...typed('body', (text) => ({ body: text }))}>
                      {rich('body', slide.body)}
                    </p>
                  )}
                </div>
              )}
              {metrics.length > 0 && slide.layout !== 'big-number' && (
                <div className="flex" style={{ gap: px(12), marginTop: px(6) }}>
                  {metrics.map(([figure, label], i) => (
                    <div
                      key={i}
                      className="min-w-0 flex-1"
                      style={boxed({
                        borderTop: `${px(2)} solid ${accent}`,
                        padding: `${px(14)} ${px(14)} ${px(16)}`,
                      })}
                    >
                      <div
                        style={{
                          fontSize: pt(TYPE.metric),
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
                        style={{ fontSize: pt(TYPE.metricLabel), marginTop: px(5), color: look.muted }}
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
                <div className="min-h-0 overflow-hidden">
                <table
                  style={{
                    fontSize: type(dense.size),
                    lineHeight: LEADING.table,
                    width: '100%',
                    borderCollapse: 'collapse',
                    tableLayout: 'fixed',
                  }}
                >
                  <colgroup>
                    {dense.shares.map((share, c) => (
                      <col key={c} style={{ width: `${share * 100}%` }} />
                    ))}
                  </colgroup>
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
                              color: r === 0 ? (visualStyle === 'mono' ? look.bg : '#fff') : look.ink,
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
                <ol
                  style={{
                    fontSize: pt(TYPE.agenda),
                    lineHeight: LEADING.agenda,
                    ...(slide.bullets.length > 4 ? { columnCount: 2, columnGap: px(20) } : null),
                  }}
                >
                  {slide.bullets.map((b, i) => (
                    <li
                      key={i}
                      className="flex items-baseline"
                      style={{ gap: px(10), padding: `${px(5)} 0`, borderBottom: `1px solid ${hair}`, breakInside: 'avoid' }}
                    >
                      <span className="tabular-nums" style={{ color: accent, fontWeight: 700, fontSize: pt(TYPE.agendaNumber) }}>
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
                    fontSize: pt(twoColumn ? TYPE.bodyNarrow : TYPE.body),
                    lineHeight: look.leading,
                    ...(twoColumn ? { columnCount: 2, columnGap: px(20) } : null),
                  }}
                >
                  {slide.bullets.map((b, i) => (
                    <li key={i} className="flex gap-2" style={{ breakInside: 'avoid', marginTop: i === 0 || (twoColumn && i === Math.ceil(slide.bullets!.length / 2)) ? 0 : `${BULLET_GAP}em` }}>
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
                pairs.length === 0 &&
                rows.length === 0 &&
                metrics.length === 0 &&
                !chart && (
                <p
                  style={{ fontSize: pt(TYPE.paragraph), color: look.muted, marginTop: px(2), lineHeight: look.leading }}
                  {...typed('body', (text) => ({ body: text }))}
                >
                  {rich('body', slide.body)}
                </p>
              )}
              {pending && !slide.image && (
                <p style={{ fontSize: type(12), color: '#aaa', marginTop: px(6) }}>
                  {writing ? t('쓰는 중…') : t('내용이 비었습니다 — 텍스트 수정으로 채워 주세요.')}
                </p>
              )}
            </div>
            {(slide.diagram?.source || slide.image?.src) && (
              <div
                className={cn('flex min-h-0 shrink-0 flex-col justify-center overflow-hidden', selectedElement === 'image' && 'ring-2 ring-accent ring-offset-2')}
                style={{
                  // A figure the deck drew for itself takes the large share until a person resizes it.
                  width: pending ? '100%' : ({ small: '32%', medium: '42%', large: '54%' }[slide.image?.size ?? (slide.diagram ? 'large' : 'medium')]),
                  order: (slide.image?.position ?? 'right') === 'left' ? 1 : 2,
                }}
                {...selectable('image')}
              >
                {slide.diagram?.source ? (
                  <SlideFigure slide={slide} accent={accent} look={look} artifactId={artifactId} />
                ) : slide.image?.src ? (
                  <img
                    src={slide.image.src}
                    alt={slide.image.caption || t('그림')}
                    className={cn(
                      'min-h-0 w-full',
                      slide.image.fit === 'cover' ? 'flex-1 object-cover' : 'max-h-full object-contain',
                    )}
                  />
                ) : null}
                {(slide.image?.caption || slide.diagram?.caption) && (
                  <p style={{ fontSize: pt(TYPE.caption), color: look.muted, marginTop: px(4) }}>
                    {slide.image?.caption || slide.diagram?.caption}
                  </p>
                )}
              </div>
            )}
          </div>
        </div>
      )}

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
              style={{ fontSize: pt(TYPE.footer), letterSpacing: px(0.3), color: look.faint }}
            >
              {deckTitle}
            </span>
          </span>
          {brand?.footer && (
            <span
              className="min-w-0 truncate"
              style={{
                fontSize: pt(TYPE.footer),
                letterSpacing: px(0.3),
                color: look.faint,
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
              fontSize: pt(TYPE.pageNumber),
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
 * A figure the deck drew for itself, rendered live from its mermaid so it stays crisp at any
 * scale. On the editor's stage (`artifactId` set) the raster is stored once for the exporters,
 * which have no browser; a stored raster is not sent again.
 */
function SlideFigure({ slide, accent, look, artifactId }: { slide: Slide; accent: string; look: Look; artifactId?: string }) {
  const t = useT()
  const host = useRef<HTMLDivElement>(null)
  const [failed, setFailed] = useState(false)
  const refreshArtifact = useStore((s) => s.refreshArtifact)
  const source = slide.diagram?.source ?? ''
  const key = slide.diagram?.key ?? ''
  const stored = Boolean(slide.image?.diagram && slide.image.src)
  const slideId = slide.id

  useEffect(() => {
    let live = true
    const node = host.current
    if (!node || !source.trim()) return
    setFailed(false)
    void (async () => {
      const drawn = await drawIntoFitting(
        node,
        source,
        slideTheme({ accent, ink: look.ink, muted: look.muted }),
        FRAMES.slide.aspect,
      )
      if (!live) return
      if (!drawn) {
        setFailed(true)
        return
      }
      // Mermaid theme variables do not reach `:::hot`; the highlight is a stylesheet.
      const style = document.createElementNS('http://www.w3.org/2000/svg', 'style')
      style.textContent = paperStyles(accent)
      drawn.prepend(style)
      // Every figure in a deck has the same 16:9 footprint; the frame is what is shown and stored.
      const frame = frameElement(drawn, FRAMES.slide.aspect, FRAMES.slide.width)
      const styled = new XMLSerializer().serializeToString(frame)
      frame.removeAttribute('width')
      frame.removeAttribute('height')
      frame.style.width = '100%'
      frame.style.height = '100%'
      frame.style.maxWidth = 'none'
      node.replaceChildren(frame)
      if (artifactId && !stored) {
        const png = await rasterise(styled, 1)
        if (!png || !live) return
        try {
          await artifactsApi.storeSlideDiagram(artifactId, slideId, key, png)
          if (live) void refreshArtifact(artifactId)
        } catch {
          // A missing raster only costs the exporters this figure; the panel still shows it.
        }
      }
    })()
    return () => {
      live = false
    }
  }, [source, key, accent, look.ink, look.muted, artifactId, stored, slideId, refreshArtifact])

  if (failed) {
    return (
      <p className="text-center" style={{ fontSize: '11px', color: look.muted }}>
        {t('도식을 그리지 못했습니다.')}
      </p>
    )
  }
  return <div ref={host} className="min-h-0 w-full flex-1 [&_svg]:block" />
}

/** Inserts an image artifact into a slide; the server embeds it as a `data:` URI. */
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
        {slide.diagram ? t('그림 모델로 바꾸기') : slide.image ? t('그림 바꾸기') : t('그림 넣기')}
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
          context={[
            slide.diagram?.description,
            slide.body,
            ...(slide.bullets ?? []),
            ...(slide.rows ?? []).map((row) => row.join(' · ')),
            ...(slide.metrics ?? []).map(([value, label]) => `${value} — ${label}`),
          ]
            .filter(Boolean)
            .join('\n')}
        />
        {slide.diagram && (
          <p className="mt-2 text-sm text-muted">
            {t('이 장의 도식은 글에서 자동으로 그린 것입니다. 이미지 모델의 그림으로 바꾸면 도식은 사라집니다.')}
          </p>
        )}
        {error && <p className="mt-2 text-base text-danger">{error}</p>}
      </Modal>
    </>
  )
}

/**
 * The slide as editable lines: title, then bullets/body, then rows, metrics
 * and pairs as `| a | b |`. A paired slide omits bullets and body, which
 * `SlideView` does not draw.
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
  // `| --- |` is a Markdown rule, not data.
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

/** Full-screen presentation chrome; shared with the HTML deck panel. */
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
  /** Presenter notes; omitted for decks without them. */
  notes?: ReactNode
  /** Slide titles for the jump list. */
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
      // Fullscreen can be refused; the overlay still works.
    }
  }

  const close = () => {
    if (document.fullscreenElement === stageRef.current) void document.exitFullscreen()
    onClose()
  }

  const clock = `${String(Math.floor(elapsed / 60)).padStart(2, '0')}:${String(elapsed % 60).padStart(2, '0')}`

  // Capture phase, stopped here: a bubbling Escape would also close the host dialog.
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

  // Portalled to body: an animated ancestor would make `fixed` resolve against itself.
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

/** Full-screen presentation of a JSON deck. */
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
  const stage = useStageScale()
  const slide = deck.slides[index]
  if (!slide) return null
  return (
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

/** Widest 16:9 box that fits the measured element's width and height. */
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

/** Stage width over 400 units, as `SlideView`'s `scale`. */
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

/** Rail preview drawn by the same renderer as the stage. */
function SlideThumbnail({ deck, slide, index, writing }: { deck: DeckArtifact; slide: Slide; index: number; writing: boolean }) {
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
  /** Reports the width the deck wants; the host decides whether there is room. */
  onModeChange?: (mode: PanelMode) => void
  onDirtyChange?: (dirty: boolean) => void
}) {
  const t = useT()
  const width = usePanelWidth(onModeChange)

  // Rewrites the slide a finding names; a deck-wide finding has nowhere to go.
  const fixFinding = async (finding: LintFinding) => {
    const slide = slideFor(deck.slides, finding.where)
    if (!slide) throw new Error(t('어느 장을 고쳐야 하는지 알 수 없습니다.'))
    const row = await artifactsApi.rewriteSlide(
      deck.id,
      slide.id,
      t('검사에서 지적된 문제를 고쳐 주세요: {message}').replace('{message}', finding.message),
    )
    const data = (row.data ?? {}) as { slides?: Slide[] }
    // Written onto the prop too: the artifacts screen renders a copy, not the store row.
    if (data.slides) deck.slides = data.slides
    deck.version = row.version
  }
  // One rewrite per slide (see `byWhere`), sequential because they share a version.
  const fixAllFindings = async (findings: LintFinding[]) => {
    const failed: string[] = []
    for (const [where, group] of byWhere(findings)) {
      const slide = where ? slideFor(deck.slides, where) : undefined
      if (!slide) {
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
  const [ribbon, setRibbon] = useState<'home' | 'edit' | 'insert' | 'review' | 'view' | 'show' | 'file'>('home')
  const [editing, setEditing] = useState(false)
  // Ribbon slot for the edit tools; without it they render in place.
  const [editToolbarSlot, setEditToolbarSlot] = useState<HTMLElement | null>(null)
  const [discardAction, setDiscardAction] = useState<'cancel' | 'close' | null>(null)
  const [draft, setDraft] = useState('')
  // The live slide on stage; structured fields do not round-trip through the text box.
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
  const [rail, setRail] = useState<'thumbs' | 'outline'>('thumbs')
  // The rail is a drawer (closed by default) on a narrow panel and a column
  // (open by default) otherwise; the toggle flips whichever default applies.
  const [railToggled, setRailToggled] = useState(false)
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
  // Slides stream in, so the selection can point past the end.
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
  // Export and editing wait only for the run to finish, not for every slide to have content.
  const writing = deck.draft === true || deck.slides.length === 0

  useEffect(() => {
    if (writing) setEditing(false)
  }, [writing])

  // The edit tab has nothing to show once the editor closes; go back to 홈.
  useEffect(() => {
    if (!editing && !bulkMode) setRibbon((tab) => (tab === 'edit' ? 'home' : tab))
  }, [editing, bulkMode])

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
        setSlideDraft((current) => current ? ({ ...current, image: undefined, diagram: undefined }) : current)
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

  // Deck as it stood when editing began; saves compare content, not version.
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

  // Refetches first so the baseline is current.
  const startEditing = async () => {
    if (!slide) return
    setError(null)
    setRibbon('edit')
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

  /** Rewrites only the selected slide. */
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
    // Pairs read from the same `|` lines as table rows; extra cells are rejoined.
    const sourceSlide = slideDraft ?? slide
    const paired = pairedLayout(sourceSlide)
    const pairs: [string, string][] = paired
      ? table.map((cells) => [cells[0], cells.slice(1).join(' | ')])
      : []

    // What is in the box is what the slide becomes; typing no rows removes the table.
    const shaped: Slide =
      table.length > 0 && !paired
        ? // Two-column rows stay metrics only if the slide already was.
          sourceSlide.metrics?.length && table.every((row) => row.length === 2)
          ? { ...sourceSlide, metrics: table.map(([f, l]) => [f, l] as [string, string]), rows: undefined }
          : { ...sourceSlide, layout: 'table', rows: table, metrics: undefined }
        : { ...sourceSlide, rows: undefined, metrics: undefined, ...(paired ? pairFields(null) : null) }

    const edited: Slide =
      sourceSlide.layout === 'chart' && sourceSlide.chart
        ? { ...sourceSlide, title, notes, body: undefined, bullets: undefined }
      : paired && pairs.length > 0
        ? {
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
          : // `section` must keep its layout: `number` lives on the slide.
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
      // Conflict check by content, not version (the panel's version may be stale).
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
      const row = await artifactsApi.update(deck.id, {
        data: { ...latestData, kind: 'deck', theme: deck.theme, slides, ...(savedDesign ? { design: savedDesign } : {}) },
        summary: t('{n}장 편집').replace('{n}', String(index + 1)),
        // The version just read, not the panel's possibly stale copy.
        expectedVersion: latest?.version ?? deck.version,
      })
      deck.slides = slides
      if (savedDesign) deck.design = savedDesign
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

  // Structural edits: the whole deck as one PATCH, checked against the server first.
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
      // `baseline` is empty until the editor has opened; fall back to the panel's deck.
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
      // Follow the slide, not the index.
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
      // Verdicts are tied to the original's sentences; the copy starts unchecked.
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

  /** Text scale for this slide only. */
  const setTextScale = (value: number) => {
    const next = deck.slides.map((row, i) =>
      i === index ? { ...row, textScale: value === 1 ? undefined : value } : row,
    )
    void restructure(next, t('{n}장 글자 크기').replace('{n}', String(index + 1)), index)
  }

  const removeSlide = () => {
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
        // A picture a person chose replaces the figure the deck drew for itself.
        diagram: undefined,
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
  // The text box is parsed only on save, so it can differ from `slideDraft`; that counts as unsaved too.
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

  // Edit tools, portalled into the ribbon slot when there is one.
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
                      <button type="button" aria-label={t('그림 제거')} title={t('그림 제거')} onClick={() => { setSlideDraft((current) => current ? ({ ...current, image: undefined, diagram: undefined }) : current); setSelectedElement(null) }} className="grid size-8 place-items-center rounded-control text-muted hover:bg-danger/10 hover:text-danger"><Trash2 size={15} /></button>
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
      <header className="relative z-40 flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2.5 max-sm:px-2">
        <div className="flex min-w-0 flex-1 items-center gap-2 max-sm:basis-full">
          <Presentation size={15} className="shrink-0 text-accent" />
          <p className="min-w-0 flex-1 truncate whitespace-nowrap text-base font-medium" title={deck.title}>
            {deck.title}
          </p>
        </div>
        <QuickAccess label={t('빠른 도구')}>
          <PanelControls mode={width.mode} onCycle={width.cycle} onClose={() => discardOr('close')} />
        </QuickAccess>
        <ArtifactRibbon
          label={t('슬라이드 메뉴')}
          // The same tabs whether or not a slide is being edited: 「편집」 holds the editor.
          tabs={[
            { id: 'home', label: t('홈') }, { id: 'edit', label: t('편집') }, { id: 'insert', label: t('삽입') },
            { id: 'review', label: t('검토') }, { id: 'view', label: t('보기') },
            { id: 'show', label: t('슬라이드 쇼') }, { id: 'file', label: t('파일') },
          ] as Array<{ id: 'home' | 'edit' | 'insert' | 'review' | 'view' | 'show' | 'file'; label: string }>}
          active={ribbon}
          onChange={(tab) => {
            if (tab === 'edit' && !editing && !bulkMode) {
              void startEditing()
              return
            }
            setRibbon(tab)
          }}
        >
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
        {ribbon === 'edit' && (editing || bulkMode) && (
          <RibbonGroup label={t('슬라이드 편집')}>
            <div ref={setEditToolbarSlot} className="flex items-center" />
          </RibbonGroup>
        )}
        {ribbon === 'home' && !editing && <RibbonGroup label={t('디자인')}><Dropdown
          align="left"
          trigger={() => (
            <Button size="sm" disabled={saving} aria-label={t('슬라이드 디자인 고르기')} title={t(LOOK_CHOICES.find((c) => c.id === visualStyle)?.why ?? '')}>
              <LookSwatch look={LOOKS[visualStyle]} accent={bulkAccent} />
              {t(LOOK_CHOICES.find((c) => c.id === visualStyle)?.label ?? visualStyle)}
              <ChevronDown size={13} className="text-muted" />
            </Button>
          )}
        >
          <MenuLabel>{t('같은 내용, 다른 인상')}</MenuLabel>
          {LOOK_CHOICES.map(({ id, label, why }) => (
            <MenuItem
              key={id}
              icon={<LookSwatch look={LOOKS[id]} accent={bulkAccent} size={1.3} />}
              checked={visualStyle === id}
              hint={t(why)}
              onClick={() => void saveDeckVisualStyle(id)}
            >
              {t(label)}
            </MenuItem>
          ))}
        </Dropdown></RibbonGroup>}
        {ribbon === 'home' && !editing && <RibbonGroup label={t('강조색')}><Dropdown
          align="left"
          trigger={() => (
            <Button size="sm" disabled={saving} aria-label={t('강조색 고르기')} title={t('제목 밑줄, 표 머리, 번호, 표지에 쓰는 색')}>
              <span className="block size-3.5 rounded-full ring-1 ring-black/10" style={{ backgroundColor: bulkAccent }} />
              {t(ACCENTS.find(([colour]) => colour.toLowerCase() === bulkAccent.toLowerCase())?.[1] ?? '직접 고른 색')}
              <ChevronDown size={13} className="text-muted" />
            </Button>
          )}
        >
          <MenuLabel>{t('제목 밑줄, 표 머리, 번호, 표지에 쓰는 색')}</MenuLabel>
          {ACCENTS.map(([colour, label]) => (
            <MenuItem key={colour} icon={<span className="block size-3.5 rounded-full ring-1 ring-black/10" style={{ backgroundColor: colour }} />} checked={bulkAccent.toLowerCase() === colour} onClick={() => void saveDeckAccent(colour)}>
              {t(label)}
            </MenuItem>
          ))}
          <label className="mt-1 flex cursor-pointer items-center gap-2 border-t border-line px-3 py-2 text-sm text-fg hover:bg-elevated">
            <input type="color" value={bulkAccent} onChange={(event) => void saveDeckAccent(event.target.value)} className="size-4 cursor-pointer border-0 bg-transparent p-0" aria-label={t('직접 고르기')} />
            {t('직접 고르기')}
          </label>
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
        {ribbon === 'review' && <RibbonGroup label={t('버전')}><VersionHistory
          artifact={deck}
          hasUnsavedChanges={hasUnsavedEdit}
          currentData={deck}
          // An open editor still holds the pre-restore slide; drop it.
          onRestored={() => {
            setEditing(false)
            setSlideDraft(null)
            setDraft('')
            setNotes('')
            baseline.current = ''
            setError(null)
          }}
        /></RibbonGroup>}
        {ribbon === 'edit' && editing && <RibbonGroup label={t('저장')}>
          <Button size="sm" variant="ghost" disabled={saving} onClick={() => discardOr('cancel')} aria-label={t('편집 취소')}>
            <X size={14} />{t('취소')}
          </Button>
          <Button variant="primary" size="sm" disabled={saving} onClick={() => void save()} aria-label={t('저장')} aria-keyshortcuts="Control+S Meta+S">
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Save size={13} />}{t('저장')}
          </Button>
        </RibbonGroup>}
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
        {/* Slide rail. */}
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
              onClick={() => { const next = !bulkMode; setBulkMode(next); setBulkSelected(new Set()); if (next) setRibbon('edit') }}
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

        {/* Stage (3/4 of the height) over the notes band (1/4); the slide fits 16:9 within its row. */}
        <div className="flex min-h-0 min-w-0 flex-1 flex-col overflow-y-auto">
          {editTools}
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
                    editable={editing}
                    selectedElement={selectedElement}
                    onSelectElement={setSelectedElement}
                    onOverflow={setOverflowing}
                    artifactId={deck.id}
                    onEdit={(next) => {
                      setSlideDraft(next)
                      setDraft(toLines(next))
                    }}
                  />
                ) : (
                  <div className="grid size-full place-items-center bg-white text-base text-[#6b6b6b]">
                    {/* Literal colour: slides ignore the theme. #6b6b6b on white is 5.3:1. */}
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
