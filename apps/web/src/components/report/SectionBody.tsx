import { Markdown } from '@/components/chat/Markdown'
import type { ReportSection } from '@/types'

/**
 * One section's body, drawn the way it was stored.
 *
 * A section is Markdown until somebody formats it in the document editor, and
 * HTML afterwards. Every place that draws a section has to know that, and for
 * a while only one of them did: the panel checked `format` while the print
 * portal and the transcript card did not, so a report that had been edited by
 * hand printed as `<p>현재 운영 중인…</p>` — tags and all — and nobody found
 * out until the file came off the printer.
 *
 * One component instead of a condition repeated at each call site. The next
 * place that draws a section gets the behaviour by using this, and cannot
 * quietly get it wrong by not knowing the field exists.
 *
 * `dangerouslySetInnerHTML` is the right tool here and only here: the markup
 * has been through `design_templates.sanitise` — on the way out of the model
 * and again on the way in from a PATCH — and it is the same string the page
 * view renders. Anything that has not been through that sanitiser must not
 * reach this component.
 */
export function SectionBody({
  section,
  className,
  owner,
}: {
  section: Pick<ReportSection, 'content' | 'format' | 'diagrams'>
  className?: string
  /**
   * Which document this section belongs to, so a mermaid diagram drawn here
   * can be kept as a picture the exports carry. Absent in the transcript,
   * where a diagram is worth drawing and there is nothing to store it onto.
   */
  owner?: { artifactId: string; sectionId: string }
}) {
  if (section.format === 'html') {
    return <div className={className} dangerouslySetInnerHTML={{ __html: section.content }} />
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

/**
 * The same body as plain text, for the places that show a document rather than
 * render it — a card preview, a copy button, a diff.
 *
 * Tags are stripped rather than escaped: a preview exists to say what the
 * section is about, and `&lt;p&gt;` says nothing about that.
 */
export function sectionText(section: Pick<ReportSection, 'content' | 'format'>): string {
  if (section.format !== 'html') return section.content
  return section.content
    .replace(/<\/(p|div|section|li|h[1-6]|tr|blockquote)>/gi, '\n')
    .replace(/<br\s*\/?>/gi, '\n')
    .replace(/<[^>]+>/g, '')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&amp;/g, '&')
    .replace(/&nbsp;/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim()
}
