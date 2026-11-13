/**
 * The web report as one Markdown document.
 *
 * The editor holds the whole report as one ProseMirror document: each section is
 * a heading (`##`, or `###` for a level-2 section) carrying the section id as a
 * node attribute, followed by its body. Storage stays `sections[]` of Markdown;
 * this module converts both ways and serialises the editor document back to
 * Markdown. Only Markdown-expressible structure exists here; precise formatting
 * is what the HWP/Word outputs are for.
 */

import type { JSONContent } from '@tiptap/core'
import type {
  Nodes as MdNode,
  Parent as MdParent,
  PhrasingContent,
  RootContent,
  Table as MdTable,
} from 'mdast'
import { fromMarkdown } from 'mdast-util-from-markdown'
import { gfmFromMarkdown } from 'mdast-util-gfm'
import { gfm } from 'micromark-extension-gfm'
import { cjkFriendlyExtension } from 'micromark-extension-cjk-friendly'
import { detachTables } from '@/lib/markdownTables'
import type { ReportSection } from '@/types'

/** Fences the app draws (diagrams, charts, KPI strips…); atoms in the editor, edited as Markdown. */
export const DRAWN_FENCES = new Set(['mermaid', 'kpi', 'steps', 'cards', 'callout', 'chart'])

/** Pandoc's page-break comment, which the exporters read. */
export const PAGE_BREAK = '<!-- pagebreak -->'
const PAGE_BREAK_RE = /^<!--\s*pagebreak\s*-->$/i

type Mark = { type: string; attrs?: Record<string, unknown> }

// ── Markdown → editor JSON ─────────────────────────────────────────────

/** Markdown parsed as the app reads it: GFM, `~~` only for strike (「3~5일」 is a range), CJK-friendly emphasis. */
function parse(markdown: string) {
  return fromMarkdown(markdown, {
    extensions: [gfm({ singleTilde: false }), cjkFriendlyExtension()],
    mdastExtensions: [gfmFromMarkdown()],
  })
}

function source(markdown: string, node: MdNode): string {
  const start = node.position?.start.offset
  const end = node.position?.end.offset
  return start === undefined || end === undefined ? '' : markdown.slice(start, end)
}

function phrasing(nodes: PhrasingContent[], markdown: string, marks: Mark[] = []): JSONContent[] {
  const out: JSONContent[] = []
  const text = (value: string, extra: Mark[] = marks) => {
    if (!value) return
    // A soft line break stays a line: the exporters and the page view read Markdown by line.
    value.split('\n').forEach((line, index) => {
      if (index) out.push({ type: 'hardBreak' })
      if (line) out.push({ type: 'text', text: line, ...(extra.length ? { marks: extra } : {}) })
    })
  }
  for (const node of nodes) {
    switch (node.type) {
      case 'text':
        text(node.value)
        break
      case 'strong':
        out.push(...phrasing(node.children, markdown, [...marks, { type: 'bold' }]))
        break
      case 'emphasis':
        out.push(...phrasing(node.children, markdown, [...marks, { type: 'italic' }]))
        break
      case 'delete':
        out.push(...phrasing(node.children, markdown, [...marks, { type: 'strike' }]))
        break
      case 'inlineCode':
        text(node.value, [...marks, { type: 'code' }])
        break
      case 'break':
        out.push({ type: 'hardBreak' })
        break
      case 'link':
        out.push(...phrasing(node.children, markdown, [
          ...marks.filter((mark) => mark.type !== 'link'),
          { type: 'link', attrs: { href: node.url, ...(node.title ? { title: node.title } : {}) } },
        ]))
        break
      case 'image':
        out.push({ type: 'image', attrs: { src: node.url, alt: node.alt ?? '', title: node.title ?? null } })
        break
      case 'html':
        out.push({ type: 'rawInline', attrs: { source: node.value } })
        break
      default:
        // References, footnote calls: kept as written.
        out.push({ type: 'rawInline', attrs: { source: source(markdown, node) } })
    }
  }
  return out
}

function paragraph(content: JSONContent[]): JSONContent {
  return content.length ? { type: 'paragraph', content } : { type: 'paragraph' }
}

function table(node: MdTable, markdown: string): JSONContent {
  return {
    type: 'table',
    content: node.children.map((row, index) => ({
      type: 'tableRow',
      content: row.children.map((cell) => ({
        type: index === 0 ? 'tableHeader' : 'tableCell',
        content: [paragraph(cellContent(phrasing(cell.children, markdown)))],
      })),
    })),
  }
}

/** `<br>` in a cell is a line break in it. */
function cellContent(nodes: JSONContent[]): JSONContent[] {
  return nodes.map((node) =>
    node.type === 'rawInline' && /^<br\s*\/?>$/i.test(String(node.attrs?.source ?? ''))
      ? { type: 'hardBreak' }
      : node,
  )
}

function blocks(nodes: RootContent[], markdown: string, shift: (depth: number) => number): JSONContent[] {
  const out: JSONContent[] = []
  for (const node of nodes) {
    switch (node.type) {
      case 'paragraph':
        out.push(paragraph(phrasing(node.children, markdown)))
        break
      case 'heading':
        out.push({
          type: 'heading',
          attrs: { level: shift(node.depth) },
          content: phrasing(node.children, markdown),
        })
        break
      case 'thematicBreak':
        out.push({ type: 'horizontalRule' })
        break
      case 'blockquote':
        out.push({ type: 'blockquote', content: orParagraph(blocks(node.children, markdown, shift)) })
        break
      case 'code': {
        const lang = (node.lang ?? '').toLowerCase()
        out.push(
          DRAWN_FENCES.has(lang)
            ? { type: 'fenceBlock', attrs: { lang, source: node.value } }
            : {
                type: 'codeBlock',
                attrs: { language: node.lang ?? null },
                ...(node.value ? { content: [{ type: 'text', text: node.value }] } : {}),
              },
        )
        break
      }
      case 'list': {
        const task = node.children.length > 0 && node.children.every((item) => typeof item.checked === 'boolean')
        out.push({
          type: task ? 'taskList' : node.ordered ? 'orderedList' : 'bulletList',
          ...(node.ordered && !task ? { attrs: { start: node.start ?? 1 } } : {}),
          content: node.children.map((item) => ({
            type: task ? 'taskItem' : 'listItem',
            ...(task ? { attrs: { checked: Boolean(item.checked) } } : {}),
            content: orParagraph(blocks(item.children, markdown, shift)),
          })),
        })
        break
      }
      case 'table':
        out.push(table(node, markdown))
        break
      case 'html':
        out.push(
          PAGE_BREAK_RE.test(node.value.trim())
            ? { type: 'pageBreak' }
            : { type: 'rawBlock', attrs: { source: node.value } },
        )
        break
      default:
        // Definitions, footnote definitions, anything else: kept verbatim.
        out.push({ type: 'rawBlock', attrs: { source: source(markdown, node) } })
    }
  }
  return out
}

/** List items and quotes must hold a block. */
function orParagraph(content: JSONContent[]): JSONContent[] {
  return content.length && content[0].type !== 'paragraph' && content[0].type !== 'heading' &&
    !['bulletList', 'orderedList', 'taskList'].includes(content[0].type ?? '')
    ? [{ type: 'paragraph' }, ...content]
    : content.length ? content : [{ type: 'paragraph' }]
}

/** Markdown as editor blocks; `shift` maps a Markdown heading depth to the editor level. */
export function markdownToBlocks(markdown: string, shift: (depth: number) => number = (d) => d): JSONContent[] {
  const text = detachTables(markdown.replace(/\r\n?/g, '\n'))
  return blocks(parse(text).children, text, shift)
}

// ── editor JSON → Markdown ─────────────────────────────────────────────

const MARK_ORDER = ['link', 'bold', 'italic', 'strike']
const DELIMITER: Record<string, string> = { bold: '**', italic: '*', strike: '~~' }

const sameMark = (a: Mark, b: Mark) =>
  a.type === b.type && (a.type !== 'link' || a.attrs?.href === b.attrs?.href)

function escapeText(text: string, inTable: boolean): string {
  let out = text
    // A backslash before punctuation would escape it; one at the end would break the line.
    .replace(/\\(?=[!-/:-@[-`{-~]|$)/g, '\\\\')
    .replace(/`/g, '\\`')
    // `*` opens emphasis unless spaced on both sides (「3 * 4」).
    .replace(/\*/g, (_, at: number, all: string) =>
      /\s/.test(all[at - 1] ?? '') && /\s/.test(all[at + 1] ?? '') ? '*' : '\\*')
    // `_` inside a word never opens emphasis (「snake_case」).
    .replace(/_/g, (_, at: number, all: string) =>
      /[\p{L}\p{N}]/u.test(all[at - 1] ?? '') && /[\p{L}\p{N}]/u.test(all[at + 1] ?? '') ? '_' : '\\_')
    // Only `~~` strikes.
    .replace(/~(?=~)|(?<=~)~/g, '\\~')
    // 「[담당자]」 and 「[1]」 stay as typed; only link-shaped brackets are escaped.
    .replace(/\[(?=[^\]\n]*\]\()|\[(?=\^)/g, '\\[')
    .replace(/<(?=[A-Za-z/!?])/g, '\\<')
    .replace(/&(?=#?[A-Za-z0-9]+;)/g, '\\&')
  if (inTable) out = out.replace(/\|/g, '\\|')
  return out
}

/** A line that Markdown would read as structure is escaped at its start. */
function escapeLineStart(line: string): string {
  if (/^#{1,6}(\s|$)/.test(line)) return `\\${line}`
  if (/^>/.test(line)) return `\\${line}`
  if (/^[-+](\s|$)/.test(line)) return `\\${line}`
  if (/^(=+|-+)\s*$/.test(line)) return `\\${line}`
  const numbered = /^(\d{1,9})([.)])(\s|$)/.exec(line)
  if (numbered) return `${numbered[1]}\\${line.slice(numbered[1].length)}`
  return line
}

function codeSpan(text: string): string {
  const longest = Math.max(0, ...(text.match(/`+/g) ?? []).map((run) => run.length))
  const fence = '`'.repeat(longest + 1)
  const pad = text.startsWith('`') || text.endsWith('`') ? ' ' : ''
  return `${fence}${pad}${text}${pad}${fence}`
}

function linkTarget(href: string): string {
  return /[\s()<>]/.test(href) ? `<${href.replace(/[<>]/g, encodeURIComponent)}>` : href
}

function inline(nodes: JSONContent[] | undefined, inTable = false): string {
  let out = ''
  let open: Mark[] = []
  const close = (mark: Mark) => {
    // Delimiters hug the text: trailing spaces move outside.
    const space = /\s*$/.exec(out)?.[0] ?? ''
    out = out.slice(0, out.length - space.length)
    if (mark.type === 'link') {
      const { href, title } = mark.attrs ?? {}
      const label = out.slice(out.lastIndexOf('\u0000') + 1)
      out = out.slice(0, out.lastIndexOf('\u0000'))
      const bare = typeof href === 'string' && /^https?:\/\/\S+$/.test(href) && label === escapeText(href, inTable)
      out += bare
        ? String(href)
        : `[${label}](${linkTarget(String(href ?? ''))}${title ? ` "${String(title).replace(/"/g, '\\"')}"` : ''})`
    } else out += DELIMITER[mark.type]
    out += space
  }
  const openMark = (mark: Mark) => {
    out += mark.type === 'link' ? '\u0000' : DELIMITER[mark.type]
  }
  for (const node of nodes ?? []) {
    const marks = ((node.marks ?? []) as Mark[])
      .filter((mark) => MARK_ORDER.includes(mark.type))
      .sort((a, b) => MARK_ORDER.indexOf(a.type) - MARK_ORDER.indexOf(b.type))
    const code = (node.marks ?? []).some((mark) => mark.type === 'code')
    const wanted = node.type === 'text' ? marks : []
    let keep = 0
    while (keep < open.length && keep < wanted.length && sameMark(open[keep], wanted[keep])) keep += 1
    for (let at = open.length - 1; at >= keep; at -= 1) close(open[at])
    open = open.slice(0, keep)
    if (node.type === 'text') {
      let text = node.text ?? ''
      if (wanted.length > keep) {
        // Leading spaces go before the opening delimiter.
        const lead = /^\s*/.exec(text)?.[0] ?? ''
        out += lead
        text = text.slice(lead.length)
        for (const mark of wanted.slice(keep)) {
          openMark(mark)
          open.push(mark)
        }
      }
      // A space the browser typed as a no-break space (at a line's end) is a space.
      text = text.replace(/\u00a0/g, ' ')
      out += code ? codeSpan(text) : escapeText(text, inTable)
    } else if (node.type === 'hardBreak') {
      out += inTable ? '<br>' : '\n'
    } else if (node.type === 'image') {
      const { src, alt, title } = node.attrs ?? {}
      out += `![${String(alt ?? '').replace(/[[\]]/g, '\\$&')}](${linkTarget(String(src ?? ''))}${title ? ` "${String(title).replace(/"/g, '\\"')}"` : ''})`
    } else if (node.type === 'rawInline') {
      out += String(node.attrs?.source ?? '')
    }
  }
  for (let at = open.length - 1; at >= 0; at -= 1) close(open[at])
  return out
}

const indent = (text: string, by: string) =>
  text.split('\n').map((line) => (line ? by + line : line)).join('\n')

function paragraphText(node: JSONContent): string {
  return inline(node.content)
    .split('\n')
    // Leading spaces would make a code block; Markdown drops them anyway.
    .map((line) => escapeLineStart(line.replace(/^\s+/, '').replace(/\s+$/, '')))
    .join('\n')
}

function list(node: JSONContent, shift: (level: number) => number): string {
  const ordered = node.type === 'orderedList'
  let number = Number(node.attrs?.start ?? 1)
  const items = (node.content ?? []).map((item) => {
    const marker = node.type === 'taskList'
      ? `- [${item.attrs?.checked ? 'x' : ' '}] `
      : ordered ? `${number++}. ` : '- '
    const pad = ' '.repeat(node.type === 'taskList' ? 2 : marker.length)
    const children = item.content ?? []
    const parts: string[] = []
    children.forEach((child, index) => {
      const text = block(child, shift)
      if (text === null) return
      const isList = ['bulletList', 'orderedList', 'taskList'].includes(child.type ?? '')
      parts.push((parts.length ? (isList ? '\n' : '\n\n') : '') + (index === 0 ? text : text))
    })
    const body = parts.join('')
    const [first, ...rest] = body.split('\n')
    return marker + (first ?? '') + (rest.length ? '\n' + indent(rest.join('\n'), pad) : '')
  })
  const loose = (node.content ?? []).some((item) =>
    (item.content ?? []).filter((child) => !['bulletList', 'orderedList', 'taskList'].includes(child.type ?? '')).length > 1)
  return items.join(loose ? '\n\n' : '\n')
}

function tableText(node: JSONContent): string {
  const rows = (node.content ?? []).map((row) =>
    (row.content ?? []).map((cell) =>
      (cell.content ?? []).map((block) => inline(block.content, true)).filter(Boolean).join('<br>').trim()))
  if (!rows.length) return ''
  const width = Math.max(...rows.map((row) => row.length), 1)
  const line = (cells: string[]) =>
    `| ${Array.from({ length: width }, (_, at) => cells[at] ?? '').join(' | ')} |`
  return [line(rows[0]), line(Array.from({ length: width }, () => '---')), ...rows.slice(1).map(line)].join('\n')
}

function fence(lang: string, body: string): string {
  const longest = Math.max(2, ...(body.match(/^\s*`{3,}/gm) ?? []).map((run) => run.trim().length))
  const ticks = '`'.repeat(longest + 1)
  return `${ticks}${lang}\n${body}\n${ticks}`
}

/** One block as Markdown; `null` for what Markdown cannot hold (an empty paragraph). */
function block(node: JSONContent, shift: (level: number) => number): string | null {
  switch (node.type) {
    case 'paragraph': {
      const text = paragraphText(node)
      return text.trim() ? text : null
    }
    case 'heading': {
      const text = inline(node.content).replace(/\n/g, ' ').trim()
      const level = shift(Number(node.attrs?.level ?? 2))
      return text ? `${'#'.repeat(level)} ${text}` : '#'.repeat(level)
    }
    case 'blockquote': {
      const inner = blocksText(node.content ?? [], shift)
      return inner ? inner.split('\n').map((line) => (line ? `> ${line}` : '>')).join('\n') : null
    }
    case 'bulletList':
    case 'orderedList':
    case 'taskList':
      return list(node, shift)
    case 'codeBlock':
      return fence(String(node.attrs?.language ?? ''), (node.content ?? []).map((part) => part.text ?? '').join(''))
    case 'fenceBlock':
      return fence(String(node.attrs?.lang ?? ''), String(node.attrs?.source ?? ''))
    case 'horizontalRule':
      return '---'
    case 'pageBreak':
      return PAGE_BREAK
    case 'rawBlock':
      return String(node.attrs?.source ?? '')
    case 'table':
      return tableText(node)
    default:
      return node.content ? blocksText(node.content, shift) : null
  }
}

function blocksText(nodes: JSONContent[], shift: (level: number) => number = (l) => l): string {
  return nodes.map((node) => block(node, shift)).filter((text): text is string => Boolean(text)).join('\n\n')
}

/** Editor blocks as Markdown; `shift` maps an editor heading level to the written depth. */
export function blocksToMarkdown(nodes: JSONContent[], shift: (level: number) => number = (l) => l): string {
  return blocksText(nodes, shift)
}

// ── report ↔ one editor document ───────────────────────────────────────

export const newSectionId = () => `s_${Math.random().toString(36).slice(2, 10)}`

const levelOf = (section: Pick<ReportSection, 'level'>) => (section.level === 2 ? 2 : 1)

/** Plain text of a heading node: section headings are stored as text. */
export function headingText(node: JSONContent): string {
  const walk = (nodes: JSONContent[] | undefined): string =>
    (nodes ?? []).map((child) =>
      child.type === 'text' ? child.text ?? '' : child.type === 'hardBreak' ? ' ' : walk(child.content)).join('')
  return walk(node.content).replace(/\s+/g, ' ').trim()
}

/** A section's body in editor coordinates: its headings sit below the section heading. */
function bodyBlocks(section: ReportSection): JSONContent[] {
  const level = levelOf(section)
  return markdownToBlocks(section.content ?? '', (depth) => Math.min(6, Math.max(level + 2, depth + level)))
}

function sectionHeading(section: Pick<ReportSection, 'id' | 'heading' | 'level'>): JSONContent {
  const text = (section.heading ?? '').replace(/\s+/g, ' ').trim()
  return {
    type: 'heading',
    attrs: { level: levelOf(section) + 1, sectionId: section.id },
    ...(text ? { content: [{ type: 'text', text }] } : {}),
  }
}

/** The report as one editor document. */
export function sectionsToDoc(sections: ReportSection[]): JSONContent {
  const content = sections.flatMap((section) => [sectionHeading(section), ...bodyBlocks(section)])
  return { type: 'doc', content: content.length ? content : [{ type: 'paragraph' }] }
}

/** Whether a top-level node starts a section: any `##`, or a `###` that still carries a section id. */
export function isSectionHeading(node: JSONContent): boolean {
  if (node.type !== 'heading') return false
  const level = Number(node.attrs?.level)
  return level === 2 || (level === 3 && Boolean(node.attrs?.sectionId))
}

/** Markdown a section's stored text becomes once read into the editor and written back. */
export function canonicalBody(section: ReportSection): string {
  const cached = canonical.get(section)
  if (cached !== undefined && cached.content === section.content) return cached.markdown
  const level = levelOf(section)
  const markdown = blocksToMarkdown(bodyBlocks(section), (l) => Math.max(1, l - level))
  canonical.set(section, { content: section.content, markdown })
  return markdown
}

// Saves run on every keystroke: a stored section is read once.
const canonical = new WeakMap<ReportSection, { content: string; markdown: string }>()

/**
 * Editor document back to stored sections. Ids ride on the heading nodes, so an
 * edited or moved section keeps its id and fields; a new heading is a new
 * section; a deleted heading's body joins the section above. A section whose
 * text the editor did not change keeps its stored Markdown byte for byte; one
 * whose text changed loses its fact-check (its verdicts point at old sentences).
 */
export function docToSections(doc: JSONContent, previous: ReportSection[]): ReportSection[] {
  const known = new Map(previous.map((section) => [section.id, section]))
  const parsed: { id: string; heading: string; level: 1 | 2; nodes: JSONContent[] }[] = []
  const preamble: JSONContent[] = []
  const used = new Set<string>()
  for (const node of doc.content ?? []) {
    if (isSectionHeading(node)) {
      let id = String(node.attrs?.sectionId ?? '')
      if (!id || used.has(id)) id = newSectionId()
      used.add(id)
      parsed.push({ id, heading: headingText(node), level: Number(node.attrs?.level) === 3 ? 2 : 1, nodes: [] })
    } else if (parsed.length) parsed[parsed.length - 1].nodes.push(node)
    else preamble.push(node)
  }
  const hasText = (nodes: JSONContent[]) => blocksToMarkdown(nodes).trim().length > 0
  if (!parsed.length) {
    if (!hasText(preamble)) return []
    const first = previous[0]
    parsed.push({ id: first?.id ?? newSectionId(), heading: first?.heading ?? '본문', level: 1, nodes: [] })
  }
  // Text above the first heading joins the first section rather than being dropped.
  if (hasText(preamble)) parsed[0].nodes = [...preamble, ...parsed[0].nodes]

  return parsed.map(({ id, heading, level, nodes }) => {
    const before = known.get(id)
    const content = blocksToMarkdown(nodes, (l) => Math.max(1, l - level))
    const kept = before && levelOf(before) === level &&
      canonicalBody(before) === content
    const section: ReportSection = {
      ...(before ?? {}),
      id,
      heading,
      level,
      status: 'done',
      content: kept ? before.content : content,
      format: 'markdown',
    }
    if (!kept && before?.factCheck) delete section.factCheck
    return section
  })
}

// ── the whole document as Markdown text (「Markdown 원문」) ──────────────

/** Title and editor document as one Markdown text. */
export function docToMarkdown(title: string, doc: JSONContent): string {
  const body = blocksToMarkdown(doc.content ?? [])
  return `# ${title.replace(/\s+/g, ' ').trim()}\n\n${body}${body ? '\n' : ''}`
}

/**
 * Markdown text back to a title and an editor document. The first `#` is the
 * title; `##` starts a section. Section ids come from `previous` (the editor's
 * sections before the text was edited): same heading first, then same place.
 */
export function markdownToDoc(
  markdown: string,
  previous: { id: string; heading: string; level: 1 | 2 }[],
): { title: string | null; doc: JSONContent } {
  let title: string | null = null
  const text = detachTables(markdown.replace(/\r\n?/g, '\n'))
  const tree = parse(text)
  const firstTitle = tree.children.findIndex((node) => node.type === 'heading' && node.depth === 1)
  const firstSection = tree.children.findIndex((node) => node.type === 'heading' && node.depth >= 2)
  let children = tree.children
  if (firstTitle >= 0 && (firstSection < 0 || firstTitle < firstSection)) {
    const node = tree.children[firstTitle] as MdParent
    title = headingText({ content: phrasing(node.children as PhrasingContent[], text) })
    children = tree.children.filter((_, index) => index !== firstTitle)
  }
  // A later `#` has no place under the title: it starts a section.
  const content = blocks(children, text, (depth) => Math.max(2, depth))
  assignSectionIds(content, previous)
  return { title, doc: { type: 'doc', content: content.length ? content : [{ type: 'paragraph' }] } }
}

/** Ids for the section headings of a document read from text. */
function assignSectionIds(nodes: JSONContent[], previous: { id: string; heading: string; level: 1 | 2 }[]): void {
  const used = new Set<string>()
  const headings = nodes.filter((node) => node.type === 'heading' && Number(node.attrs?.level) <= 3)
  // A `###` is a section only where a level-2 section of that name was.
  const sections = headings.filter((node) =>
    Number(node.attrs?.level) === 2 ||
    previous.some((row) => row.level === 2 && row.heading === headingText(node)))
  // Same heading first.
  const matched = new Map<JSONContent, string>()
  for (const node of sections) {
    const level = Number(node.attrs?.level) === 3 ? 2 : 1
    const at = previous.findIndex((row) =>
      !used.has(row.id) && row.level === level && row.heading === headingText(node))
    if (at >= 0) {
      matched.set(node, previous[at].id)
      used.add(previous[at].id)
    }
  }
  // Then the same place, for a renamed section.
  sections.forEach((node, index) => {
    if (matched.has(node)) return
    const row = previous[index]
    const level = Number(node.attrs?.level) === 3 ? 2 : 1
    if (row && !used.has(row.id) && row.level === level) {
      matched.set(node, row.id)
      used.add(row.id)
    }
  })
  for (const node of sections) {
    node.attrs = { ...node.attrs, sectionId: matched.get(node) ?? newSectionId() }
  }
}

/** The editor's sections as `markdownToDoc` matches them. */
export function sectionOutline(doc: JSONContent): { id: string; heading: string; level: 1 | 2 }[] {
  return (doc.content ?? []).filter(isSectionHeading).map((node) => ({
    id: String(node.attrs?.sectionId ?? ''),
    heading: headingText(node),
    level: Number(node.attrs?.level) === 3 ? 2 as const : 1 as const,
  }))
}

/** Whole report as Markdown (download, recovery copy). */
export function reportToMarkdown(title: string, sections: ReportSection[]): string {
  return docToMarkdown(title, sectionsToDoc(sections))
}
