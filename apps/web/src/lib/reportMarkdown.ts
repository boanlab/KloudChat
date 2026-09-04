/**
 * Report `{title, sections[]}` ↔ one Markdown document. Parsing must neither
 * split a section at a `##` inside a body nor return zero sections when the
 * last heading was deleted, since saving overwrites the artifact.
 */

import type { ReportSection } from '@/types'
import { uid } from '@/lib/utils'

const FENCE = /^\s*(```|~~~)/
const TITLE = /^#(?!#)\s+(.*)$/
const SECTION = /^##(?!#)\s+(.*)$/
/** `#` or `##` written inside a section's prose. */
const INNER_TOP_HEADING = /^#{1,2}(?!#)\s+/

/** Demotes `#`/`##` inside a body to `###` so they are not read back as sections. Idempotent. */
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

/** `previous` supplies section ids, matched by position. */
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
      // Only the first `#` before any section is the title; a later `#` is prose.
      const docTitle = TITLE.exec(line)
      if (docTitle && title === null && parsed.length === 0) {
        title = docTitle[1].trim()
        continue
      }
    }

    if (parsed.length === 0) preamble.push(line)
    else parsed[parsed.length - 1].body.push(line)
  }

  // Text above the first heading joins the first section rather than being dropped.
  if (parsed.length > 0 && preamble.join('').trim()) {
    parsed[0].body = [...preamble, '', ...parsed[0].body]
  }

  // Every heading deleted: keep the body under the previous first heading.
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
