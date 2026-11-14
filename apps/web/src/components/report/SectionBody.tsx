import { Markdown } from '@/components/chat/Markdown'
import type { ReportSection } from '@/types'

/** A section's Markdown body. */
export function SectionBody({
  section,
  className,
  owner,
}: {
  section: Pick<ReportSection, 'content' | 'diagrams'>
  className?: string
  /** Diagram store target; absent in the transcript. */
  owner?: { artifactId: string; sectionId: string }
}) {
  return (
    <Markdown
      className={className}
      owner={owner ? { ...owner, stored: section.diagrams } : undefined}
    >
      {section.content}
    </Markdown>
  )
}

/** A section's text for search and quoting. */
export function sectionText(section: Pick<ReportSection, 'content'>): string {
  return section.content
}
