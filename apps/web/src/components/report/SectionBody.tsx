import { cn } from '@/lib/utils'
import { Markdown } from '@/components/chat/Markdown'
import type { ReportSection } from '@/types'

/**
 * One section's body: Markdown, or HTML once edited in the document editor.
 * HTML content must have been through `design_templates.sanitise`; nothing
 * else may reach the `dangerouslySetInnerHTML` here.
 */
export function SectionBody({
  section,
  className,
  owner,
}: {
  section: Pick<ReportSection, 'content' | 'format' | 'diagrams'>
  className?: string
  /** Document to store rendered diagrams on; absent in the transcript. */
  owner?: { artifactId: string; sectionId: string }
}) {
  if (section.format === 'html') {
    // `doc-html` styles the template markup outside the page view's shadow root.
    return (
      <div
        className={cn('doc-html', className)}
        dangerouslySetInnerHTML={{ __html: section.content }}
      />
    )
  }
  return (
    <Markdown
      className={className}
      owner={owner ? { ...owner, stored: section.diagrams } : undefined}
    >
      {section.content}
    </Markdown>
  )
}

// Elements that end a line when markup becomes text.
const BLOCKS = 'p, div, section, li, h1, h2, h3, h4, h5, h6, tr, blockquote'

/**
 * The body as plain text. Parsed with DOMParser (no script, no fetch) rather
 * than regex-stripped, so escaped text cannot turn back into markup.
 */
export function sectionText(section: Pick<ReportSection, 'content' | 'format'>): string {
  if (section.format !== 'html') return section.content
  const doc = new DOMParser().parseFromString(section.content, 'text/html')
  doc.querySelectorAll('br').forEach((br) => br.replaceWith('\n'))
  doc.body.querySelectorAll(BLOCKS).forEach((block) => block.append('\n'))
  return (doc.body.textContent ?? '').replace(/\n{3,}/g, '\n\n').trim()
}
