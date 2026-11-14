import { detachTables } from '@/lib/markdownTables'
import { parseCallout, parseCards } from '@/components/report/CardGrid'
import { parse as parsePairs } from '@/components/report/StepList'
import type { ReportSection } from '@/types'

/** Section body as HTML from the Markdown the editor writes, read line by line like the exporters; lists nest by indent; `gaejo` marks ※/붙임 for `GAEJO_CSS`. */
export function htmlOf(section: Pick<ReportSection, 'content'>, pictures: Map<string, string>, gaejo = false): string {
  // Escapes `"` too: output goes into attribute values (`data-source`).
  const escape = (s: string) =>
    s
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
  // Backslash escapes held apart while marks are read, then written as their character.
  const held: string[] = []
  const inline = (s: string) =>
    escape(s.replace(/\\([!-/:-@[-`{-~])/g, (_, ch: string) => `\u0000${held.push(ch) - 1}\u0000`))
      .replace(/`(.+?)`/g, '<code>$1</code>')
      .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
      .replace(/(?<!\w)\*(.+?)\*(?!\w)/g, '<em>$1</em>')
      .replace(/~~(.+?)~~/g, '<s>$1</s>')
      .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2">$1</a>')
      .replace(/\u0000(\d+)\u0000/g, (_, at: string) => escape(held[Number(at)] ?? ''))

  const out: string[] = []
  // Pending list items, table rows, fence lines; flushed on anything else.
  let items: string[] = []
  let ordered = false
  // 개조식 items with depth (1–4), nested at flush.
  let nested: { depth: number; ordered: boolean; html: string; note?: boolean }[] = []
  let attaching = false
  // Indents of the open list levels.
  let indents: number[] = []
  let rows: string[][] = []
  let fence: string[] | null = null
  let fenceLang = ''
  const flush = () => {
    if (nested.length) {
      const open: string[] = []
      let html = ''
      for (const item of nested) {
        if (item.depth > open.length) {
          while (open.length < item.depth) {
            const tag = item.ordered ? 'ol' : 'ul'
            html += `<${tag}>`
            open.push(tag)
            // A skipped level still needs an item to hold the deeper list.
            if (open.length < item.depth) html += '<li>'
          }
        } else {
          while (open.length > item.depth) html += `</li></${open.pop()}>`
          html += '</li>'
        }
        html += item.note ? `<li class="gaejo-note">${item.html}` : `<li>${item.html}`
      }
      while (open.length) html += `</li></${open.pop()}>`
      out.push(html)
      nested = []
      indents = []
    }
    if (items.length) {
      const tag = ordered ? 'ol' : 'ul'
      out.push(`<${tag}>${items.map((i) => `<li>${i}</li>`).join('')}</${tag}>`)
      items = []
    }
    if (rows.length) {
      const width = Math.max(...rows.map((r) => r.length))
      const cell = (row: string[], c: number, tag: string) =>
        `<${tag}>${row[c] ?? ''}</${tag}>`
      const [head, ...body] = rows
      const headRow = `<tr>${Array.from({ length: width }, (_, c) => cell(head, c, 'th')).join('')}</tr>`
      const bodyRows = body
        .map(
          (row) =>
            `<tr>${Array.from({ length: width }, (_, c) => cell(row, c, 'td')).join('')}</tr>`,
        )
        .join('')
      out.push(`<table><thead>${headRow}</thead><tbody>${bodyRows}</tbody></table>`)
      rows = []
    }
  }
  for (const raw of detachTables(section.content || '').split('\n')) {
    const line = raw.trim()
    const fenceMark = /^```+\s*([A-Za-z0-9_-]*)\s*$/.exec(line)
    if (fence !== null) {
      if (fenceMark) {
        out.push(fenceHtml(fenceLang, fence.join('\n'), escape, pictures))
        fence = null
      } else {
        // Raw line: indentation inside a fence is content.
        fence.push(raw)
      }
      continue
    }
    if (fenceMark) {
      flush()
      fence = []
      fenceLang = fenceMark[1].toLowerCase()
      continue
    }
    if (!line) {
      // Blank line does not end a table (models put one between rows), nor a list.
      if (!rows.length && !nested.length) flush()
      continue
    }
    if (!rows.length && /^<!--\s*pagebreak\s*-->$/i.test(line)) {
      flush()
      out.push('<div data-page-break="true"></div>')
      continue
    }
    if (!rows.length && /^<!--.*-->$/.test(line)) continue
    if (!rows.length && /^(?:(?:-\s*){3,}|(?:\*\s*){3,}|(?:_\s*){3,})$/.test(line)) {
      flush()
      out.push('<hr>')
      continue
    }
    // `| --- |` rule carries no cells.
    const row = /^\|(.+)\|$/.exec(line)
    if (row) {
      if (!/^[\s:|-]+$/.test(row[1])) {
        rows.push(row[1].split(/(?<!\\)\|/).map((c) => inline(c.replace(/\\\|/g, '|').trim())))
      }
      continue
    }
    const image = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(line)
    if (image) {
      flush()
      const caption = escape(image[1])
      out.push(
        `<figure><img src="${escape(image[2])}" alt="${caption}">` +
          (caption ? `<figcaption>${caption}</figcaption>` : '') +
          '</figure>',
      )
      continue
    }
    const heading = /^(#{2,6})\s+(.*)$/.exec(line)
    const bullet = /^[-*+]\s+(.*)$/.exec(line)
    const numbered = /^\d{1,9}[.)]\s+(.*)$/.exec(line)
    const quote = /^>\s*(.*)$/.exec(line)
    if (gaejo && !heading) {
      const depth = Math.min(Math.floor(raw.replace(/\t/g, '    ').search(/\S/) / 2) + 1, 4)
      if (attaching && /^\d{1,3}\.\s/.test(line)) {
        out.push(`<p class="gaejo-attach">${inline(line)}</p>`)
        continue
      }
      attaching = false
      if (bullet || numbered) {
        // Pending table goes out first; the list keeps growing.
        if (rows.length || items.length) flush()
        const text = (bullet ?? numbered)![1]
        // ※ item: note at its depth, ※ as marker (`GAEJO_CSS`).
        nested.push({ depth, ordered: !bullet, html: inline(text), note: Boolean(bullet) && text.startsWith('※') })
        continue
      }
      if (line.startsWith('※') || line.startsWith('붙임')) {
        flush()
        attaching = line.startsWith('붙임')
        out.push(`<p class="${attaching ? 'gaejo-attach' : 'gaejo-note'}">${inline(line)}</p>`)
        continue
      }
    }
    if (heading) {
      flush()
      // Wrapper draws the section heading: body headings start at h3.
      const level = Math.min(heading[1].length + 1, 6)
      out.push(`<h${level}>${inline(heading[2])}</h${level}>`)
    } else if (bullet || numbered) {
      if (rows.length || items.length) flush()
      // Depth by indent: a child sits under its parent's text (two spaces under 「- 」, three under 「1. 」).
      const indent = raw.replace(/\t/g, '    ').search(/\S/)
      while (indents.length && indents[indents.length - 1] > indent) indents.pop()
      if (!indents.length || indents[indents.length - 1] < indent) indents.push(indent)
      const text = (bullet ?? numbered)![1].replace(/^\[([ xX])\]\s+/, (_, box: string) => (box === ' ' ? '☐ ' : '☑ '))
      nested.push({ depth: Math.min(indents.length, 6), ordered: !bullet, html: inline(text) })
    } else if (quote) {
      flush()
      out.push(`<blockquote>${inline(quote[1])}</blockquote>`)
    } else {
      flush()
      out.push(`<p>${inline(line)}</p>`)
    }
  }
  // Unterminated fence (mid-stream) drawn with what arrived.
  if (fence !== null) out.push(fenceHtml(fenceLang, fence.join('\n'), escape, pictures))
  flush()
  return out.join('')
}

/** Fenced block as template-styled markup; any other fence is a code block. */
export function fenceHtml(
  lang: string,
  source: string,
  escape: (s: string) => string,
  pictures: Map<string, string>,
): string {
  if (lang === 'kpi') {
    const cells = parsePairs(source, 4)
      .map(
        ([value, label]) =>
          `<div><strong>${escape(value)}</strong><span>${escape(label)}</span></div>`,
      )
      .join('')
    return cells ? `<div class="kpi">${cells}</div>` : ''
  }
  if (lang === 'steps') {
    const items = parsePairs(source, 8)
      .map(
        ([name, detail]) =>
          `<li><strong>${escape(name)}</strong>` +
          (detail ? ` <span>${escape(detail)}</span>` : '') +
          '</li>',
      )
      .join('')
    return items ? `<ol class="steps">${items}</ol>` : ''
  }
  if (lang === 'cards') {
    // `<section>`, not `<div>`: `richtext` reads back with a lazy close, so wrapper and children tags must differ.
    const grid = parseCards(source)
      .map(
        (card) =>
          '<div>' +
          `<h3>${escape(card.title)}</h3>` +
          (card.items.length
            ? `<ul>${card.items.map((line) => `<li>${escape(line)}</li>`).join('')}</ul>`
            : '') +
          '</div>',
      )
      .join('')
    return grid ? `<section class="cards">${grid}</section>` : ''
  }
  if (lang === 'callout') {
    const callout = parseCallout(source)
    if (!callout?.title) return ''
    return (
      '<section class="callout">' +
      `<h3>${escape(callout.title)}</h3>` +
      callout.items.map((line) => `<p>${escape(line)}</p>`).join('') +
      '</section>'
    )
  }
  if (lang === 'chart') {
    // Source only; exporters build the chart from it.
    return `<figure class="chart" data-source="${escape(source)}"></figure>`
  }
  if (lang === 'mermaid') {
    const picture = pictures.get(source)
    return (
      `<figure class="diagram" data-source="${escape(source)}">` +
      (picture ? `<img src="${escape(picture)}" alt="">` : '') +
      '</figure>'
    )
  }
  // Any other fence is code, shown as written.
  return `<pre><code>${escape(source)}</code></pre>`
}

export function mermaidSources(section: Pick<ReportSection, 'content'>): string[] {
  const out: string[] = []
  let collecting: string[] | null = null
  for (const raw of (section.content || '').split('\n')) {
    const mark = /^```+\s*([A-Za-z0-9_-]*)\s*$/.exec(raw.trim())
    if (collecting !== null) {
      if (mark) {
        out.push(collecting.join('\n'))
        collecting = null
      } else collecting.push(raw)
    } else if (mark && mark[1]?.toLowerCase() === 'mermaid') {
      collecting = []
    }
  }
  if (collecting !== null) out.push(collecting.join('\n'))
  return out
}
