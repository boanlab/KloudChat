/**
 * Documents by purpose — the web mirror of `apps/api/app/services/doc_formats.py`.
 *
 * The table between the `wire` markers is `doc_formats.wire()` verbatim (JSON, same order);
 * `tests/test_report_formats_export.py` keeps the two equal. `numberHeading` is
 * `doc_formats.number_heading`, and the exporters number headings the same way.
 */
import type { ReportSection, ReportTitleBlock } from '@/types'
import { FORMAT_SPACE, formatScaleCss } from './docType'
import { PLACEHOLDER_CSS, blankFor, isBlank } from '@/lib/placeholders'

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
  lists: '' | 'gaejo'
  closing: boolean
}

export const DOC_FORMATS: DocFormatWire[] = /* wire:begin */ [
  {"id": "review", "label": "피어리뷰", "fields": ["심사 대상", "심사 의견", "확신도"], "numbering": "none", "head": "header", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "paper", "label": "학술 논문", "fields": ["저자", "소속", "교신저자"], "numbering": "roman", "head": "paper", "abstract": true, "keywords": true, "subtitle": false, "lists": "", "closing": false},
  {"id": "lab", "label": "실험 보고서", "fields": ["실험일", "과목·분반", "조", "학번·이름", "공동실험자", "제출일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "minutes", "label": "회의록", "fields": ["회의명", "일시", "장소", "참석자", "작성자"], "numbering": "none", "head": "memo", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "press", "label": "보도자료", "fields": ["배포일", "보도 시점", "문의처"], "numbering": "none", "head": "press", "abstract": false, "keywords": false, "subtitle": true, "lists": "", "closing": false},
  {"id": "internal_draft", "label": "간이기안문", "fields": ["생산등록번호", "등록일", "결재일", "공개 구분", "기안자", "검토자", "결재권자"], "numbering": "korean", "head": "draft", "abstract": false, "keywords": false, "subtitle": false, "lists": "gaejo", "closing": true},
  {"id": "official", "label": "공문·안내문", "fields": ["수신", "참조", "발신", "시행일"], "numbering": "official", "head": "memo", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "incident", "label": "장애 보고서", "fields": ["발생 일시", "영향 범위", "심각도", "작성자"], "numbering": "decimal", "head": "header", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "result_report", "label": "결과 보고서", "fields": ["보고일", "보고 부서", "보고자"], "numbering": "korean", "head": "memo", "abstract": false, "keywords": false, "subtitle": false, "lists": "gaejo", "closing": true},
  {"id": "plan", "label": "연구·조사 계획서", "fields": ["과제명", "연구자", "소속", "연구 기간", "지도교수", "작성일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "event_plan", "label": "추진 계획(안)", "fields": ["작성일", "작성 부서", "작성자"], "numbering": "korean", "head": "memo", "abstract": false, "keywords": false, "subtitle": false, "lists": "gaejo", "closing": true},
  {"id": "gaejo", "label": "개조식 보고서", "fields": ["보고일", "보고자(부서)", "보고 대상"], "numbering": "korean", "head": "memo", "abstract": false, "keywords": false, "subtitle": false, "lists": "gaejo", "closing": true},
  {"id": "status", "label": "업무·현황 보고", "fields": ["보고일", "보고자", "대상 기간", "보고 대상"], "numbering": "korean", "head": "memo", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "proposal", "label": "프로젝트 제안서", "fields": ["팀명", "팀원", "지도교수", "제출일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "client_proposal", "label": "고객 제안서", "fields": ["고객사", "사업명", "제안사", "제안일", "담당자", "연락처", "유효기간"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "comparison", "label": "비교·분석 보고서", "fields": ["검토 대상", "판단 기준", "작성일", "작성자"], "numbering": "decimal", "head": "header", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "career_history", "label": "경력기술서", "fields": ["이름", "연락처", "이메일", "지원 회사", "지원 직무", "작성일"], "numbering": "none", "head": "header", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "biz", "label": "기획안·업무 문서", "fields": ["작성 부서", "작성자", "작성일"], "numbering": "korean", "head": "memo", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false},
  {"id": "term", "label": "대학 과제 리포트", "fields": ["과목", "담당교수", "학과", "학번", "이름", "제출일"], "numbering": "decimal", "head": "cover", "abstract": false, "keywords": false, "subtitle": false, "lists": "", "closing": false}
] /* wire:end */

/** Formats retired with their agents (고객 맞춤 제안서, 자기소개서·경력기술서): an agent that has one
 *  keeps it, nothing new picks it. Kept in step with `doc_formats.RETIRED`. */
export const RETIRED_FORMATS: readonly string[] = ['client_proposal', 'career_history']

/** Shown where a head field is still blank: its own name in brackets (「[작성자]」), never an invented value. */
export { blankFor }

const HEADS: DocHead[] = ['cover', 'header', 'memo', 'press', 'paper', 'draft']
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
    lists: raw.lists === 'gaejo' ? 'gaejo' : undefined,
    closing: raw.closing === true ? true : undefined,
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
// A callout's title and a card's title are `<h3>`s the exporters draw inside the box or
// card, unnumbered; numbering a card's would also take the `::before` its colour bar uses.
const CALLOUT_HTML = /(<section\b[^>]{0,200}\b(?:callout|cards)\b[^>]{0,200}>[\s\S]*?<\/section>)/i

/** Headings inside a section's content: what the exporters number at level 2. */
export function contentHeadingCount(section: Pick<ReportSection, 'content' | 'format'>): number {
  if (section.format === 'html') {
    return section.content.split(CALLOUT_HTML).filter((_, at) => at % 2 === 0).join('').match(CONTENT_HEADING_HTML)?.length ?? 0
  }
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

/**
 * Each section's heading number, counting the headings in the sections before it. A
 * 개조식 붙임 (`lists: 'gaejo'`) is not numbered: it closes the body (`isAttachment`).
 */
export function sectionNumbers(
  sections: { level?: number; content: string; format?: ReportSection['format']; heading?: string }[],
  numbering: DocNumbering,
  lists?: ReportTitleBlock['lists'],
): SectionNumber[] {
  const counters = [0, 0, 0]
  return sections.map((section) => {
    if (numbering === 'none' || isAttachment(section, lists)) return { label: '', a: counters[0], b: counters[1] }
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
  return html.split(CALLOUT_HTML).map((part, at) => (at % 2 ? part : part.replace(CONTENT_HEADING_HTML, (_match, level: string, rest = '') => {
    b += 1
    return `<h${level} data-num="${numberHeading(numbering, 2, [start.a, b])}"${rest}>`
  }))).join('')
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
.fmt :is(.tb-counted, .ProseMirror) :is(h2, h3, h4, h5, h6):not(.callout *, .cards *) { counter-increment: tbb; }
.fmt :is(.tb-counted, .ProseMirror) :is(h2, h3, h4, h5, h6):not(.callout *, .cards *)::before { content: ${CONTENT_NUMBER[numbering]} !important; margin-right: 0.45em; }`
}

/** The title block's own look; colours lean on `currentColor` so a dark theme or a filled cover keeps them. */
export const TITLE_BLOCK_CSS = `
.tb { margin: 0 0 10mm; }
.tb .tb-title { margin: 0; font-size: var(--doc-title, 20pt); font-weight: 700; line-height: 1.3; }
.tb .tb-label { margin: 0 0 2mm; font-size: 0.85em; letter-spacing: 0.04em; opacity: 0.7; }
.tb .tb-subtitle { margin: 2mm 0 0; font-size: 1.2em; opacity: 0.8; }
.tb .tb-blank { color: #9a9a9a; }
${PLACEHOLDER_CSS}
.tb .tb-fields { border-collapse: collapse; font-size: 0.92em; }
.tb .tb-fields th, .tb .tb-fields td { border: 1px solid color-mix(in srgb, currentColor 28%, transparent); padding: 1.6mm 3mm; text-align: left; vertical-align: middle; }
.tb .tb-fields th { background: color-mix(in srgb, currentColor 6%, transparent); font-weight: 600; white-space: nowrap; width: 1%; }
.tb .tb-key { font-weight: 700; white-space: nowrap; }
.tb.tb-cover { display: flex; flex-direction: column; align-items: center; text-align: center; }
.tb.tb-cover .tb-label { margin: 0 0 5mm; font-size: var(--doc-coverLabel, 12pt); font-weight: 600; letter-spacing: 0.12em; opacity: 0.65; }
.tb.tb-cover .tb-title { max-width: 24ch; margin-inline: auto; font-size: var(--doc-cover, 26pt); line-height: 1.3; text-wrap: balance; word-break: keep-all; }
.tb.tb-cover .tb-subtitle { margin: 4mm 0 0; }
.tb.tb-cover .tb-fields { width: 64%; margin: 10mm auto 0; text-align: left; }
.tb.tb-cover .tb-fields th { width: 28%; }
.tb.tb-cover.cover { box-sizing: border-box; justify-content: flex-start; min-height: 250mm; margin: 0; padding: 72mm 0 0; break-after: page; page-break-after: always; }
.tb.tb-cover.cover .tb-fields { margin-top: 16mm; }
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
.tb.tb-draft .tb-fields { width: 100%; }
.tb.tb-draft .tb-fields th, .tb.tb-draft .tb-fields td { border-color: color-mix(in srgb, currentColor 60%, transparent); }
.tb.tb-draft .tb-fields .tb-signer { width: 18%; text-align: center; }
.tb.tb-draft .tb-fields td.tb-sign { height: 12mm; }
.tb.tb-onepage .tb-box { padding: 1.5mm 4mm 2mm; border: 1.5pt solid currentColor; text-align: center; }
.tb.tb-onepage .tb-box .tb-title { font-family: var(--font-head, inherit); font-size: var(--doc-title, 18pt); line-height: 1.3; }
.tb.tb-onepage .tb-fieldline { display: flex; flex-wrap: wrap; justify-content: flex-end; gap: 0 1.2em; margin: 1mm 0 0; font-size: var(--doc-small, 10pt); }
.tb.tb-onepage .tb-fieldline b { font-weight: 700; }
.tb.tb-onepage .tb-fieldline .tb-input { width: auto; min-width: 7em; }
@media print { .tb.tb-cover.cover { min-height: 0; } }
`

const escapeHtml = (text: string) => text
  .replace(/&/g, '&amp;')
  .replace(/</g, '&lt;')
  .replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;')

const shown = (value: string, label: string) => (value.trim() && !isBlank(value)
  ? escapeHtml(value)
  : `<span class="tb-blank doc-placeholder">${escapeHtml(value.trim() || blankFor(label))}</span>`)

/** Pairs of fields per table row: two across in the compact header table, one elsewhere. */
export function fieldRows(fields: [string, string][], perRow: number): [string, string][][] {
  const rows: [string, string][][] = []
  for (let i = 0; i < fields.length; i += perRow) rows.push(fields.slice(i, i + perRow))
  return rows
}

/** A paper's author or affiliation line: the value alone when given, else labelled. */
export const paperLineLabelled = (label: string, value: string) => !(value.trim() && (label === '저자' || label === '소속'))

/** Whether the block's format has its own scale and spacing: the 개조식 1쪽 보고 (`docType.FORMAT_SPACE`). */
export const onePage = (block: Pick<ReportTitleBlock, 'format'> | null | undefined) =>
  Boolean(block && FORMAT_SPACE[block.format])

/** The class list of the block's outer element; a cover in a page view is also `.cover`, so it owns its page. */
export const titleBlockClass = (block: ReportTitleBlock, paged: boolean) =>
  `tb tb-${block.head}${block.head === 'cover' && paged ? ' cover' : ''}${onePage(block) ? ' tb-onepage' : ''}`

/** The title block as markup, for the paged preview (Paged.js takes an HTML string). */
export function titleBlockHtml(block: ReportTitleBlock, title: string): string {
  const fieldsTable = (perRow: number) => block.fields.length
    ? `<table class="tb-fields"><tbody>${fieldRows(block.fields, perRow).map((row) => `<tr>${row.map(([label, value]) => `<th>${escapeHtml(label)}</th><td>${shown(value, label)}</td>`).join('')}${row.length < perRow ? '<th></th><td></td>'.repeat(perRow - row.length) : ''}</tr>`).join('')}</tbody></table>`
    : ''
  const heading = `<h1 class="tb-title">${escapeHtml(title)}</h1>`
  const subtitle = block.subtitle?.trim() ? `<p class="tb-subtitle">${escapeHtml(block.subtitle)}</p>` : ''
  const label = block.label ? `<p class="tb-label">${escapeHtml(block.label)}</p>` : ''
  let inner: string
  if (onePage(block)) {
    // The 1쪽 보고: the title boxed, the head fields one line under it, right.
    const line = block.fields.map(([key, value]) => `<span><b>${escapeHtml(key)}</b> ${shown(value, key)}</span>`).join('')
    inner = `<div class="tb-box">${heading}</div>${line ? `<p class="tb-fieldline">${line}</p>` : ''}`
  } else if (block.head === 'cover') inner = `${label}${heading}${subtitle}${fieldsTable(1)}`
  else if (block.head === 'memo') inner = `${fieldsTable(1)}<div class="tb-memo-title"><span class="tb-key">제목</span>${heading}</div>`
  else if (block.head === 'draft') {
    const rows = draftRows(block.fields).map((row) => `<tr>${row.map((cell) => {
      const signer = cell.kind === 'approver' || cell.kind === 'sign' || cell.kind === 'signer'
      const cls = [signer && 'tb-signer', cell.kind === 'sign' && 'tb-sign'].filter(Boolean).join(' ')
      const attrs = `${cls ? ` class="${cls}"` : ''}${cell.rowSpan ? ` rowspan="${cell.rowSpan}"` : ''}`
      if (cell.kind === 'label' || cell.kind === 'approver') return `<th${attrs}>${escapeHtml(cell.text)}</th>`
      if (cell.kind === 'value' || cell.kind === 'signer') return `<td${attrs}>${shown(block.fields[cell.field ?? -1]?.[1] ?? '', block.fields[cell.field ?? -1]?.[0] ?? '')}</td>`
      return `<td${attrs}></td>`
    }).join('')}</tr>`).join('')
    inner = `<table class="tb-fields" aria-label="결재란"><tbody>${rows}</tbody></table><div class="tb-memo-title"><span class="tb-key">제목</span>${heading}</div>`
  }
  else if (block.head === 'press') {
    inner = `<p class="tb-kicker">보도자료</p>${heading}${subtitle}<p class="tb-dateline">${block.fields.map(([key, value]) => `<span><b>${escapeHtml(key)}</b>${shown(value, key)}</span>`).join('')}</p>`
  } else if (block.head === 'paper') {
    const lines = block.fields.map(([key, value]) => `<p class="tb-line">${paperLineLabelled(key, value) ? `${escapeHtml(key)}: ${shown(value, key)}` : escapeHtml(value)}</p>`).join('')
    const summary = block.abstract !== undefined ? `<div class="tb-abstract"><span class="tb-key">초록</span><p>${shown(block.abstract, '초록')}</p></div>` : ''
    const words = block.keywords !== undefined ? `<p class="tb-keywords"><span class="tb-key">핵심어</span> ${shown(block.keywords.join(', '), '핵심어')}</p>` : ''
    inner = `${heading}${subtitle}${lines}${summary}${words}`
  } else inner = `${label}${heading}${subtitle}${fieldsTable(2)}`
  return `<header class="${titleBlockClass(block, true)}">${inner}</header>`
}

/** The 결재란 columns of a `draft` head, in signing order (`doc_formats.APPROVERS`). */
export const APPROVERS = ['기안자', '검토자', '결재권자']

/** One cell of the `draft` head; `field` indexes `block.fields`. */
export interface DraftCell {
  kind: 'label' | 'value' | 'approver' | 'sign' | 'signer' | 'empty'
  text: string
  field?: number
  rowSpan?: number
}

/**
 * The 간이기안문 head as rows (`report_export._draft_grid`): register fields on the left,
 * the 결재란 on the right — 직위 in the head row, a tall blank to sign, 직위와 성명 under it.
 */
export function draftRows(fields: [string, string][]): DraftCell[][] {
  const register = fields.map((pair, index) => ({ pair, index })).filter(({ pair }) => !APPROVERS.includes(pair[0]))
  const signer = (name: string) => fields.findIndex(([label]) => label === name)
  const height = Math.max(register.length, 3)
  return Array.from({ length: height }, (_, row) => {
    const own = register[row]
    const cells: DraftCell[] = own
      ? [{ kind: 'label', text: own.pair[0] }, { kind: 'value', text: own.pair[1], field: own.index }]
      : [{ kind: 'empty', text: '' }, { kind: 'empty', text: '' }]
    if (row === 0) cells.push(...APPROVERS.map((name) => ({ kind: 'approver' as const, text: name })))
    else if (row === 1) cells.push(...APPROVERS.map(() => ({ kind: 'sign' as const, text: '', rowSpan: height - 2 })))
    else if (row === height - 1) {
      cells.push(...APPROVERS.map((name) => {
        const at = signer(name)
        return at < 0 ? { kind: 'empty' as const, text: '' } : { kind: 'signer' as const, text: fields[at][1], field: at }
      }))
    }
    return cells
  })
}

/**
 * 개조식 (`lists: 'gaejo'`): each list depth hangs □ / ○ / - / ㆍ and sets in 2타 (one
 * Hangul) from its parent's marker; a ※ paragraph is a smaller note and the 붙임 lines
 * stand under their first item. The exporters draw the same (`report_export._docx_hanging`).
 */
export const GAEJO_CSS = `
.fmt.gaejo ul:not(.steps) { list-style: none !important; margin: 0.3em 0 !important; padding-left: 0 !important; }
.fmt.gaejo ul:not(.steps) ul { margin: 0.15em 0 0 -0.5em !important; }
.fmt.gaejo ul:not(.steps) > li { position: relative; list-style: none !important; margin: 0.15em 0 !important; padding-left: 1.5em !important; }
.fmt.gaejo ul:not(.steps) > li > p { margin: 0 !important; }
.fmt.gaejo ul:not(.steps) > li::marker { content: none; }
.fmt.gaejo ul:not(.steps) > li::before { content: '□'; position: absolute; left: 0; top: 0; color: inherit; font-weight: 400; }
.fmt.gaejo ul:not(.steps) ul > li::before { content: '○'; }
.fmt.gaejo ul:not(.steps) ul ul > li::before { content: '-'; }
.fmt.gaejo ul:not(.steps) ul ul ul > li::before { content: 'ㆍ'; }
.fmt.gaejo :is(p[data-note], p.gaejo-note) { margin: 0.2em 0 0.4em !important; padding-left: 2.5em; text-indent: -1.5em; font-size: 0.86em; }
.fmt.gaejo ul:not(.steps) > li:is(.gaejo-note, [data-note]) { padding-left: 1.5em !important; text-indent: -1.5em; font-size: 0.86em; }
.fmt.gaejo ul:not(.steps) > li:is(.gaejo-note, [data-note])::before { content: none; }
.fmt.gaejo :is(p[data-attach], p.gaejo-attach) { margin: 0.5em 0 0 !important; padding-left: 2.6em; text-indent: -2.6em; }
.fmt.gaejo p.gaejo-attach + p.gaejo-attach { margin-top: 0 !important; text-indent: 0; }
`

/**
 * The 1쪽 보고's 핵심 box (`report_export._pdf_core_box`): the label in a narrow column
 * beside its lines, a thin black rule round both.
 */
const CORE_CSS = `
.fmt.tb-scaled .callout { display: grid !important; grid-template-columns: 4.2em 1fr; align-items: center; padding: 0 !important; border: 0.8pt solid currentColor !important; border-radius: 0 !important; background: none !important; }
.fmt.tb-scaled .callout > :is(h3, h4) { grid-row: 1 / span 9; align-self: stretch; display: flex; align-items: center; justify-content: center; margin: 0 !important; padding: 0 0.3em; border-right: 0.5pt solid currentColor; color: inherit !important; font-size: var(--doc-h1) !important; }
.fmt.tb-scaled .callout > p { margin: 0 !important; padding: 0 0.6em; }
.fmt.tb-scaled .callout > p:nth-child(2) { padding-top: 0.2em; }
.fmt.tb-scaled .callout > p:last-child { padding-bottom: 0.25em; }
.fmt.tb-scaled h2 { color: inherit !important; }
`

/** The class list a formatted document's outer element carries: `fmt`, plus `gaejo`, plus its own scale's `f-<id>`. */
export const fmtClass = (block: ReportTitleBlock | null | undefined) =>
  block ? `fmt${block.lists === 'gaejo' ? ' gaejo' : ''}${onePage(block) ? ` tb-scaled f-${block.format}` : ''}` : ''

/** Every rule a formatted document needs: its head, heading numbers, list style and its own scale. */
export const formatCss = (block: ReportTitleBlock) =>
  `${TITLE_BLOCK_CSS}\n${numberingCss(block.numbering)}${block.lists === 'gaejo' ? `\n${GAEJO_CSS}` : ''}${onePage(block) ? `\n${CORE_CSS}${formatScaleCss(block.format)}` : ''}`

// The patterns `doc_formats.gaejo_markdown` reads; bounded, since the text is the writer's.
const FENCE_LINE = /^\s*```/
const TYPED = /^(\s{0,12})(?:[-*+]\s{1,4})?([□■○◦ㅇ\-–―ㆍ·∙])\s{1,4}(\S.*)$/
const ITEM = /^(\s{0,24})([-*+]|\d{1,3}[.)])\s{1,4}(.*)$/
const ATTACH = /^\s{0,8}붙\s{0,2}임\s{0,4}:?[\s\u00a0]{0,6}(.*)$/
const NUMBERED = /^\s{0,24}\d{1,3}\.\s{1,4}\S/
const TYPED_DEPTH: Record<string, number> = {
  '□': 1, '■': 1, '○': 2, '◦': 2, 'ㅇ': 2, '-': 3, '–': 3, '―': 3, 'ㆍ': 4, '·': 4, '∙': 4,
}
/** The 2타 before 「끝.」 and after 「붙임」; non-breaking, so HTML keeps both. */
export const GAP = '\u00a0\u00a0'

/**
 * `doc_formats.gaejo_markdown`: typed markers (「□ 현황」, 「ㅇ」 for ○) become nested list
 * items, a ※ line under an item becomes an item at its depth (drawn with ※ as the marker),
 * a ※ line elsewhere stands apart as a note, and a 붙임 run is one paragraph a line per item.
 */
export function gaejoMarkdown(text: string): string {
  const lines = (text || '').split('\n')
  const typed = lines.some((line) => {
    const found = TYPED.exec(line)
    return Boolean(found && '□■○◦ㅇ'.includes(found[2]))
  })
  const out: string[] = []
  const indents: number[] = []
  let fenced = false
  let attaching = false
  // The depth of the last list item, while a ※ line may still stand under it.
  let itemDepth = 0
  const blank = () => {
    if (out.length && out[out.length - 1].trim()) out.push('')
  }
  for (const line of lines) {
    if (FENCE_LINE.test(line)) {
      fenced = !fenced
      out.push(line)
      continue
    }
    const stripped = line.trim()
    if (fenced || stripped.startsWith('|')) {
      out.push(line)
      attaching = false
      itemDepth = 0
      continue
    }
    if (!stripped) {
      if (!attaching) {
        indents.length = 0
        out.push('')
      }
      continue
    }
    if (attaching && NUMBERED.test(line)) {
      if (out[out.length - 1] === '붙임') out[out.length - 1] = `붙임${GAP}${stripped}`
      else {
        out[out.length - 1] = `${out[out.length - 1].trimEnd()}  `
        out.push(stripped)
      }
      continue
    }
    if (attaching) blank()
    attaching = false
    const attach = ATTACH.exec(line)
    if (attach) {
      blank()
      const rest = attach[1].trim()
      out.push(rest ? `붙임${GAP}${rest}` : '붙임')
      attaching = true
      indents.length = 0
      itemDepth = 0
      continue
    }
    if (stripped.startsWith('※')) {
      const note = `※ ${stripped.slice(1).trim()}`
      if (itemDepth) {
        // A ※ under an item is a note at that item's depth: its own marker, no ○.
        while (out.length && !out[out.length - 1].trim()) out.pop()
        out.push(`${'  '.repeat(itemDepth - 1)}- ${note}`)
        continue
      }
      blank()
      out.push(note)
      out.push('')
      indents.length = 0
      continue
    }
    const typedItem = typed ? TYPED.exec(line) : null
    if (typedItem) {
      out.push(`${'  '.repeat(TYPED_DEPTH[typedItem[2]] - 1)}- ${typedItem[3].trim()}`)
      itemDepth = TYPED_DEPTH[typedItem[2]]
      continue
    }
    const item = ITEM.exec(line)
    if (item) {
      const indent = item[1].replace(/\t/g, '    ').length
      while (indents.length && indent < indents[indents.length - 1]) indents.pop()
      if (!indents.length || indent > indents[indents.length - 1]) indents.push(indent)
      let depth = Math.min(indents.length, 4)
      let body = item[3].trim()
      const inner = TYPED.exec(body)
      if (inner && '□■○◦ㅇㆍ·∙'.includes(inner[2])) {
        depth = TYPED_DEPTH[inner[2]]
        body = inner[3].trim()
      }
      const mark = '-*+'.includes(item[2]) ? '- ' : `${item[2]} `
      out.push(`${'  '.repeat(depth - 1)}${mark}${body}`)
      itemDepth = depth
      continue
    }
    const last = out.length ? out[out.length - 1].trimStart() : ''
    if ((last.startsWith('- ') || last.startsWith('* ')) && indents.length) blank()
    indents.length = 0
    itemDepth = 0
    out.push(stripped)
  }
  return out.join('\n').replace(/\n{3,}/g, '\n\n').replace(/^\n+|\n+$/g, '')
}

// `doc_formats.without_closing`: a typed 「끝.」 alone, after a gap of two, or after a sentence's end.
const TYPED_CLOSING = /(?:^[\s\u00a0]{0,8}|[\s\u00a0]{2,8}|(?<=[.)」”])[\s\u00a0]{1,8})끝\s{0,2}\.\s{0,4}$/

/** A Markdown body ending in 「끝.」: after its last line, or under a closing table or figure. */
export function closeMarkdown(text: string): string {
  const lines = (text || '').split('\n').map((line) => line.replace(TYPED_CLOSING, ''))
  while (lines.length && !lines[lines.length - 1].trim()) lines.pop()
  if (!lines.length) return `${GAP}끝.`
  const last = lines[lines.length - 1].trim()
  if (/^(?:\||```|!\[|#)/.test(last)) return `${lines.join('\n')}\n\n${GAP}끝.`
  lines[lines.length - 1] = `${lines[lines.length - 1].trimEnd()}${GAP}끝.`
  return lines.join('\n')
}

/** An edited (HTML) body ending in 「끝.」: inside its last paragraph or item, or after a table. */
export function closeHtml(html: string): string {
  const end = Math.max(html.lastIndexOf('</p>'), html.lastIndexOf('</li>'))
  if (end < 0 || /<(?:table|figure|img)\b/i.test(html.slice(end))) return `${html}<p>${GAP}끝.</p>`
  const head = html.slice(0, end).replace(/(?:&nbsp;|\u00a0|\s){1,16}끝\.$/, '')
  return `${head}${GAP}끝.${html.slice(end)}`
}

/**
 * A section as its format shows it: 개조식 Markdown canonical, and the last body of a
 * closing format ending in 「끝.」. The stored section is untouched.
 */
export function shapedSection<T extends Pick<ReportSection, 'content' | 'format'> & { heading?: string }>(
  section: T,
  block: ReportTitleBlock | null | undefined,
  last: boolean,
): T {
  if (!block) return section
  let content = section.content
  const attachment = isAttachment(section, block.lists)
  // A 붙임 that lists nothing is not drawn (`report_export._drawn_sections`).
  if (attachment && section.format !== 'html' && NO_ATTACHMENT.test(content)) content = ''
  else if (attachment && section.format !== 'html') content = attachmentBody(content)
  if (section.format !== 'html' && block.lists === 'gaejo') content = gaejoMarkdown(content)
  if (block.closing && last) {
    if (section.format === 'html') content = closeHtml(content)
    else if (attachment) {
      // 「끝.」 closes the 붙임 list; what was attached in full (a moved table) follows it.
      const [list, ...rest] = content.split(/\n\s*\n/)
      content = rest.some((part) => /^\s*\|/m.test(part))
        ? [closeMarkdown(list), ...rest].join('\n\n')
        : closeMarkdown(content)
    } else content = closeMarkdown(content)
  }
  return content === section.content ? section : { ...section, content }
}

const ATTACH_HEADING = /^\s{0,4}붙\s{0,2}임\s{0,4}$/
const NO_ATTACHMENT = /^[\s\-*·]*(?:없음|해당\s?없음|-|—)?[\s.]*$/

/**
 * Whether a 개조식 section is the 붙임 (`report_export._is_attachment`): drawn as the closing
 * 「붙임  1. ○○ 1부.」 lines after the body, with no heading or number of its own.
 */
export const isAttachment = (section: { heading?: string }, lists: ReportTitleBlock['lists'] | undefined) =>
  lists === 'gaejo' && ATTACH_HEADING.test(section.heading ?? '')

/**
 * The 붙임 section's body under its 붙임 label (`doc_formats.attachment_body`): 「붙임」 put
 * first when the writer left it off, and a label typed as a list item made a line of its own.
 */
export function attachmentBody(text: string): string {
  const lines = (text || '').split('\n')
  const at = lines.findIndex((line) => line.trim())
  if (at < 0) return text
  const item = ITEM.exec(lines[at])
  if (item && '-*+'.includes(item[2]) && ATTACH.test(item[3])) lines[at] = item[3]
  else if (!ATTACH.test(lines[at])) lines.splice(at, 0, '붙임')
  return lines.join('\n')
}

/** Index of the section 「끝.」 closes: the last one (the reference list is not a section here). */
export const closingIndex = (block: ReportTitleBlock | null | undefined, count: number) =>
  block?.closing ? count - 1 : -1
