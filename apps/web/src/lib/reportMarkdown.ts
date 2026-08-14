/**
 * Report ↔ a single Markdown document.
 *
 * The panel holds `{title, sections[]}`; the document editor needs the whole
 * thing at once, or the title, the section headings and the space between
 * sections are not editable.
 *
 * Parsing is the dangerous direction, since saving overwrites the artifact.
 * Two things must not happen: splitting a section at a `##` belonging to the
 * body, and returning zero sections because the last heading was deleted.
 */

import type { ReportSection } from '@/types'
import { uid } from '@/lib/utils'

const FENCE = /^\s*(```|~~~)/
const TITLE = /^#(?!#)\s+(.*)$/
const SECTION = /^##(?!#)\s+(.*)$/
/** `#` or `##` written inside a section's prose. */
const INNER_TOP_HEADING = /^#{1,2}(?!#)\s+/

/**
 * Section headings are `##`, so a `##` inside a body would be read back as a
 * new section. Body headings are demoted to `###`, which is what they mean and
 * how the exporters draw them. Idempotent: `###` is left alone.
 */
function pushInnerHeadings(content: string): string {
  let fenced = false
  return content
    .split('\n')
    .map((line) => {
      if (FENCE.test(line)) fenced = !fenced
      if (fenced) return line
      return INNER_TOP_HEADING.test(line) ? line.replace(INNER_TOP_HEADING, '### ') : line
    })
    .join('\n')
}

export function toMarkdown(report: { title: string; sections: ReportSection[] }): string {
  const parts = [`# ${report.title}`]
  for (const section of report.sections) {
    parts.push(`## ${section.heading}`)
    const body = pushInnerHeadings(section.content ?? '').trim()
    if (body) parts.push(body)
  }
  return `${parts.join('\n\n')}\n`
}

/**
 * `previous` supplies the section ids, matched by position — so reordering,
 * inserting and deleting all work without the writer keeping an id around.
 */
export function fromMarkdown(
  markdown: string,
  previous: ReportSection[],
): { title: string; sections: ReportSection[] } {
  const lines = markdown.split('\n')
  let title: string | null = null
  const preamble: string[] = []
  const parsed: { heading: string; body: string[] }[] = []
  let fenced = false

  for (const line of lines) {
    if (FENCE.test(line)) fenced = !fenced

    if (!fenced) {
      const heading = SECTION.exec(line)
      if (heading) {
        parsed.push({ heading: heading[1].trim(), body: [] })
        continue
      }
      // Only the first `#`, and only before any section, is the document title.
      // A `#` further down is prose, and rewriting the title from it would be a
      // surprise the writer never asked for.
      const docTitle = TITLE.exec(line)
      if (docTitle && title === null && parsed.length === 0) {
        title = docTitle[1].trim()
        continue
      }
    }

    if (parsed.length === 0) preamble.push(line)
    else parsed[parsed.length - 1].body.push(line)
  }

  // Text above the first heading has nowhere of its own to live; it belongs to
  // whatever follows rather than being dropped on save.
  if (parsed.length > 0 && preamble.join('').trim()) {
    parsed[0].body = [...preamble, '', ...parsed[0].body]
  }

  // Every heading was deleted. Rather than saving an empty report, keep the
  // body and borrow the title the document already had.
  if (parsed.length === 0) {
    const body = preamble.join('\n').trim()
    if (!body) return { title: title ?? previous[0]?.heading ?? '', sections: [] }
    parsed.push({ heading: previous[0]?.heading ?? '본문', body: [body] })
  }

  const sections: ReportSection[] = parsed.map((section, index) => ({
    ...(previous[index] ?? {}),
    id: previous[index]?.id ?? uid(),
    heading: section.heading,
    level: previous[index]?.level ?? 1,
    status: 'done',
    content: section.body.join('\n').trim(),
  }))

  return { title: title ?? '', sections }
}
