import { cn } from '@/lib/utils'
import { Markdown } from '@/components/chat/Markdown'
import type { ReportSection } from '@/types'

/** Markdown, or HTML once edited; HTML must have passed `design_templates.sanitise` before `dangerouslySetInnerHTML`. */
export function SectionBody({
  section,
  className,
  owner,
}: {
  section: Pick<ReportSection, 'content' | 'format' | 'diagrams'>
  className?: string
  /** Diagram store target; absent in the transcript. */
  owner?: { artifactId: string; sectionId: string }
}) {
  if (section.format === 'html') {
    // `doc-html`: template markup styling outside the page view's shadow root.
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

// Line-ending elements for text conversion.
const BLOCKS = 'p, div, section, li, h1, h2, h3, h4, h5, h6, tr, blockquote'

/** Plain text via DOMParser (no script, no fetch), so escaped text cannot become markup. */
export function sectionText(section: Pick<ReportSection, 'content' | 'format'>): string {
  if (section.format !== 'html') return section.content
  const doc = new DOMParser().parseFromString(section.content, 'text/html')
  doc.querySelectorAll('br').forEach((br) => br.replaceWith('\n'))
  doc.body.querySelectorAll(BLOCKS).forEach((block) => block.append('\n'))
  return (doc.body.textContent ?? '').replace(/\n{3,}/g, '\n\n').trim()
}
