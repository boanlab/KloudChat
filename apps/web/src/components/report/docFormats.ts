/**
 * Documents by purpose — the web mirror of `apps/api/app/services/doc_formats.py`.
 *
 * The table between the `wire` markers is `doc_formats.wire()` verbatim (JSON, same order);
 * `tests/test_report_formats_export.py` keeps the two equal. `numberHeading` is
 * `doc_formats.number_heading`, and the exporters number headings the same way.
 */
import type { ReportSection, ReportTitleBlock } from '@/types'

export type DocNumbering = ReportTitleBlock['numbering']
export type DocHead = ReportTitleBlock['head']

export interface DocFormatWire {
  id: string
  label: string
  fields: string[]
  numbering: DocNumbering
  head: DocHead
  abstract: boolean
  keywords: boolean
  subtitle: boolean
}

export const DOC_FORMATS: DocFormatWire[] = /* wire:begin */ [
  {"id": "review", "label": "피어리뷰", "fields": ["심사 대상", "심사 의견", "확신도"], "numbering": "none", "head": "header", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "paper", "label": "학술 논문", "fields": ["저자", "소속", "교신저자"], "numbering": "roman", "head": "paper", "abstract": true, "keywords": true, "subtitle": false},
  {"id": "lab", "label": "실험 보고서", "fields": ["실험일", "과목·분반", "조", "학번·이름", "공동실험자", "제출일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "minutes", "label": "회의록", "fields": ["회의명", "일시", "장소", "참석자", "작성자"], "numbering": "none", "head": "memo", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "press", "label": "보도자료", "fields": ["배포일", "보도 시점", "문의처"], "numbering": "none", "head": "press", "abstract": false, "keywords": false, "subtitle": true},
  {"id": "official", "label": "공문·안내문", "fields": ["수신", "참조", "발신", "시행일"], "numbering": "official", "head": "memo", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "incident", "label": "장애 보고서", "fields": ["발생 일시", "영향 범위", "심각도", "작성자"], "numbering": "decimal", "head": "header", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "status", "label": "업무·현황 보고", "fields": ["보고일", "보고자", "대상 기간", "보고 대상"], "numbering": "korean", "head": "memo", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "plan", "label": "연구·조사 계획서", "fields": ["과제명", "연구자", "소속", "지도교수", "작성일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "proposal", "label": "프로젝트 제안서", "fields": ["팀명", "팀원", "지도교수", "제출일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "biz", "label": "기획안·업무 문서", "fields": ["작성 부서", "작성자", "작성일"], "numbering": "korean", "head": "memo", "abstract": false, "keywords": false, "subtitle": false},
  {"id": "term", "label": "대학 과제 리포트", "fields": ["과목", "담당교수", "학과", "학번", "이름", "제출일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false}
] /* wire:end */

/** Shown where a head field is still blank: a place to fill in, never an invented value. */
export const BLANK = '(기입)'

const HEADS: DocHead[] = ['cover', 'header', 'memo', 'press', 'paper']
const NUMBERINGS: DocNumbering[] = ['decimal', 'roman', 'korean', 'official', 'none']

/** The stored block, made safe to render; null when the document has none. */
export function cleanTitleBlock(block: unknown): ReportTitleBlock | null {
  if (!block || typeof block !== 'object' || !Object.keys(block).length) return null
  const raw = block as Partial<ReportTitleBlock> & Record<string, unknown>
  const fields = (Array.isArray(raw.fields) ? raw.fields : [])
    .filter((pair): pair is [string, string] => Array.isArray(pair) && Boolean(String(pair[0] ?? '').trim()))
    .map(([label, value]) => [String(label).trim(), String(value ?? '')] as [string, string])
  return {
    ...raw,
    format: String(raw.format ?? ''),
    label: String(raw.label ?? ''),
    head: HEADS.includes(raw.head as DocHead) ? (raw.head as DocHead) : 'header',
    numbering: NUMBERINGS.includes(raw.numbering as DocNumbering) ? (raw.numbering as DocNumbering) : 'none',
    fields,
  }
}

/** The number before a heading, given the running counters ([h2, h3, h4]). */
export function numberHeading(numbering: DocNumbering, level: number, counters: number[]): string {
  if (numbering === 'none') return ''
  const korean = '가나다라마바사아자차카타파하'
  const [a, b, c] = [...counters, 0, 0, 0].slice(0, 3)
  if (numbering === 'roman') {
    const romans = ['I', 'II', 'III', 'IV', 'V', 'VI', 'VII', 'VIII', 'IX', 'X', 'XI', 'XII']
    // Past XII the exporters fall back to digits too.
    return level === 1 ? `${romans[a - 1] ?? a}.` : `${String.fromCharCode(64 + b)}.`
  }
  if (numbering === 'korean' || numbering === 'official') {
    if (level === 1) return `${a}.`
    if (level === 2) return `${korean[(((b - 1) % korean.length) + korean.length) % korean.length]}.`
    return `${c})`
  }
  return level === 1 ? `${a}.` : `${a}.${b}`
}

const CONTENT_HEADING_HTML = /<h([2-6])(\s[^>]*)?>/gi

/** Headings inside a section's content: what the exporters number at level 2. */
export function contentHeadingCount(section: Pick<ReportSection, 'content' | 'format'>): number {
  if (section.format === 'html') return section.content.match(CONTENT_HEADING_HTML)?.length ?? 0
  const prose = section.content.replace(/^\s*```[\s\S]*?^\s*```\s*$/gm, '')
  return prose.match(/^#{2,6}\s+\S/gm)?.length ?? 0
}

export interface SectionNumber {
  /** `1.`, `I.`, `1.2`, or empty. */
  label: string
  /** Counters after the section's own heading, where its content headings start. */
  a: number
  b: number
}

/** Each section's heading number, counting the headings in the sections before it. */
export function sectionNumbers(
  sections: { level?: number; content: string; format?: ReportSection['format'] }[],
  numbering: DocNumbering,
): SectionNumber[] {
  const counters = [0, 0, 0]
  return sections.map((section) => {
    if (numbering === 'none') return { label: '', a: 0, b: 0 }
    const level = section.level === 2 ? 2 : 1
    counters[level - 1] += 1
    for (let deeper = level; deeper < 3; deeper += 1) counters[deeper] = 0
    const label = numberHeading(numbering, level, counters)
    const at = { label, a: counters[0], b: counters[1] }
    counters[1] += contentHeadingCount(section)
    return at
  })
}

/** Content headings in an HTML body, each given its number as `data-num` (the paged preview). */
export function numberHtmlHeadings(html: string, numbering: DocNumbering, start: SectionNumber): string {
  if (numbering === 'none') return html
  let b = start.b
  return html.replace(CONTENT_HEADING_HTML, (_match, level: string, rest = '') => {
    b += 1
    return `<h${level} data-num="${numberHeading(numbering, 2, [start.a, b])}"${rest}>`
  })
}

/** The inline style a numbered section carries so its content headings count on from it. */
export function counterStyle(at: SectionNumber): { counterReset: string } {
  return { counterReset: `tba ${at.a} tbb ${at.b}` }
}

const CONTENT_NUMBER: Record<Exclude<DocNumbering, 'none'>, string> = {
  decimal: 'counter(tba) "." counter(tbb)',
  roman: 'counter(tbb, upper-alpha) "."',
  korean: 'counter(tbb, hangul) "."',
  official: 'counter(tbb, hangul) "."',
}

/**
 * Numbering rules under `.fmt`: the template's own section counter is switched off, a
 * heading with `data-num` shows it, and headings in a live body (`.tb-counted`, the
 * editor's `.ProseMirror`) count on from the section's `counter-reset`.
 */
export function numberingCss(numbering: DocNumbering): string {
  const off = '.fmt h2::before { content: none !important; }'
  if (numbering === 'none') return off
  return `${off}
.fmt :is(h1, h2, h3, h4, h5, h6)[data-num]::before { content: attr(data-num) !important; margin-right: 0.45em; }
.fmt :is(.tb-counted, .ProseMirror) :is(h2, h3, h4, h5, h6) { counter-increment: tbb; }
.fmt :is(.tb-counted, .ProseMirror) :is(h2, h3, h4, h5, h6)::before { content: ${CONTENT_NUMBER[numbering]} !important; margin-right: 0.45em; }`
}

/** The title block's own look; colours lean on `currentColor` so a dark theme or a filled cover keeps them. */
export const TITLE_BLOCK_CSS = `
.tb { margin: 0 0 10mm; }
.tb .tb-title { margin: 0; font-size: var(--doc-title, 20pt); font-weight: 700; line-height: 1.3; }
.tb .tb-label { margin: 0 0 2mm; font-size: 0.85em; letter-spacing: 0.04em; opacity: 0.7; }
.tb .tb-subtitle { margin: 2mm 0 0; font-size: 1.2em; opacity: 0.8; }
.tb .tb-blank { color: #9a9a9a; }
.tb .tb-fields { border-collapse: collapse; font-size: 0.92em; }
.tb .tb-fields th, .tb .tb-fields td { border: 1px solid color-mix(in srgb, currentColor 28%, transparent); padding: 1.6mm 3mm; text-align: left; vertical-align: middle; }
.tb .tb-fields th { background: color-mix(in srgb, currentColor 6%, transparent); font-weight: 600; white-space: nowrap; width: 1%; }
.tb .tb-key { font-weight: 700; white-space: nowrap; }
.tb.tb-cover { display: flex; flex-direction: column; align-items: center; text-align: center; }
.tb.tb-cover .tb-title { max-width: 26ch; margin-inline: auto; font-size: calc(var(--doc-title, 20pt) * 1.25); }
.tb.tb-cover .tb-fields { margin-top: auto; min-width: 60%; }
.tb.tb-cover:not(.cover) .tb-fields { margin-top: 10mm; }
.tb.tb-cover .tb-fields th { width: 30%; }
.tb.tb-header .tb-fields { width: 100%; margin-top: 4mm; }
.tb.tb-memo .tb-fields { width: 100%; border-top: 2px solid currentColor; border-bottom: 2px solid currentColor; }
.tb.tb-memo .tb-fields th, .tb.tb-memo .tb-fields td { border-left: 0; border-right: 0; }
.tb .tb-memo-title { display: flex; align-items: baseline; gap: 5mm; margin-top: 5mm; padding-bottom: 3mm; border-bottom: 1px solid currentColor; }
.tb .tb-memo-title .tb-title { flex: 1; min-width: 0; }
.tb.tb-press .tb-kicker { display: inline-block; margin: 0 0 4mm; padding-bottom: 1mm; border-bottom: 2px solid currentColor; font-size: 1.25em; font-weight: 800; letter-spacing: 0.3em; }
.tb.tb-press .tb-title { font-size: calc(var(--doc-title, 20pt) * 1.2); }
.tb .tb-dateline { display: flex; flex-wrap: wrap; gap: 1mm 6mm; margin: 4mm 0 0; padding: 2mm 0; border-top: 1.5px solid currentColor; border-bottom: 1px solid color-mix(in srgb, currentColor 28%, transparent); font-size: 0.92em; }
.tb .tb-dateline > span { display: inline-flex; align-items: baseline; gap: 2mm; }
.tb .tb-dateline b { white-space: nowrap; }
.tb .tb-dateline .tb-input, .tb .tb-line .tb-input { width: auto; min-width: 9em; }
.tb.tb-paper { text-align: center; }
.tb.tb-paper .tb-line { margin: 1.5mm 0 0; }
.tb.tb-paper .tb-line:first-of-type { margin-top: 5mm; }
.tb .tb-abstract, .tb .tb-keywords { text-align: left; }
.tb .tb-abstract { margin: 7mm 0 0; padding: 3mm 0; border-top: 1px solid color-mix(in srgb, currentColor 28%, transparent); }
.tb .tb-abstract > p { margin: 1mm 0 0; }
.tb .tb-keywords { margin: 2mm 0 0; padding-bottom: 3mm; border-bottom: 1px solid color-mix(in srgb, currentColor 28%, transparent); }
.tb .tb-input { box-sizing: border-box; width: 100%; min-width: 6em; padding: 0; border: 0; border-bottom: 1px dashed color-mix(in srgb, currentColor 35%, transparent); background: transparent; color: inherit; font: inherit; text-align: inherit; }
.tb textarea.tb-input { min-height: 4.5em; resize: vertical; }
.tb .tb-input::placeholder { color: #9a9a9a; opacity: 1; }
.tb .tb-input:focus { outline: 1px solid color-mix(in srgb, currentColor 40%, transparent); outline-offset: 1px; }
@media print { .tb.tb-cover { break-after: page; page-break-after: always; } }
`

const escapeHtml = (text: string) => text
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')

const shown = (value: string) => (value.trim() ? escapeHtml(value) : `<span class="tb-blank">${BLANK}</span>`)

/** Pairs of fields per table row: two across in the compact header table, one elsewhere. */
export function fieldRows(fields: [string, string][], perRow: number): [string, string][][] {
  const rows: [string, string][][] = []
  for (let i = 0; i < fields.length; i += perRow) rows.push(fields.slice(i, i + perRow))
  return rows
}

/** A paper's author or affiliation line: the value alone when given, else labelled. */
export const paperLineLabelled = (label: string, value: string) => !(value.trim() && (label === '저자' || label === '소속'))

/** The class list of the block's outer element; a cover in a page view is also `.cover`, so it owns its page. */
export const titleBlockClass = (block: ReportTitleBlock, paged: boolean) =>
  `tb tb-${block.head}${block.head === 'cover' && paged ? ' cover' : ''}`

/** The title block as markup, for the paged preview (Paged.js takes an HTML string). */
export function titleBlockHtml(block: ReportTitleBlock, title: string): string {
  const fieldsTable = (perRow: number) => block.fields.length
    ? `<table class="tb-fields"><tbody>${fieldRows(block.fields, perRow).map((row) => `<tr>${row.map(([label, value]) => `<th>${escapeHtml(label)}</th><td>${shown(value)}</td>`).join('')}${row.length < perRow ? '<th></th><td></td>'.repeat(perRow - row.length) : ''}</tr>`).join('')}</tbody></table>`
    : ''
  const heading = `<h1 class="tb-title">${escapeHtml(title)}</h1>`
  const subtitle = block.subtitle?.trim() ? `<p class="tb-subtitle">${escapeHtml(block.subtitle)}</p>` : ''
  const label = block.label ? `<p class="tb-label">${escapeHtml(block.label)}</p>` : ''
  let inner: string
  if (block.head === 'cover') inner = `${label}${heading}${subtitle}${fieldsTable(1)}`
  else if (block.head === 'memo') inner = `${fieldsTable(1)}<div class="tb-memo-title"><span class="tb-key">제목</span>${heading}</div>`
  else if (block.head === 'press') {
    inner = `<p class="tb-kicker">보도자료</p>${heading}${subtitle}<p class="tb-dateline">${block.fields.map(([key, value]) => `<span><b>${escapeHtml(key)}</b>${shown(value)}</span>`).join('')}</p>`
  } else if (block.head === 'paper') {
    const lines = block.fields.map(([key, value]) => `<p class="tb-line">${paperLineLabelled(key, value) ? `${escapeHtml(key)}: ${shown(value)}` : escapeHtml(value)}</p>`).join('')
    const summary = block.abstract !== undefined ? `<div class="tb-abstract"><span class="tb-key">초록</span><p>${shown(block.abstract)}</p></div>` : ''
    const words = block.keywords !== undefined ? `<p class="tb-keywords"><span class="tb-key">핵심어</span> ${shown(block.keywords.join(', '))}</p>` : ''
    inner = `${heading}${subtitle}${lines}${summary}${words}`
  } else inner = `${label}${heading}${subtitle}${fieldsTable(2)}`
  return `<header class="${titleBlockClass(block, true)}">${inner}</header>`
}
