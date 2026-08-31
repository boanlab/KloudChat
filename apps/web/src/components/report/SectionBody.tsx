import { cn } from '@/lib/utils'
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
    // `doc-html` carries the styles for markup nobody here wrote.
    //
    // A section written into a 서식 is stored as HTML, and the rules that made
    // it look like a document live in the 서식's typesetting — which is loaded
    // into the page view's shadow root and nowhere else. In the web view the
    // same markup landed with browser defaults, so a table came out as columns
    // of text with no rules and read as a table that had failed to render.
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

//: Where a line break belongs when markup becomes text.
const BLOCKS = 'p, div, section, li, h1, h2, h3, h4, h5, h6, tr, blockquote'

/**
 * The same body as plain text, for the places that show a document rather than
 * render it — a card preview, a copy button, a diff.
 *
 * Parsed rather than stripped with regular expressions. The old version peeled
 * tags off with `/<[^>]+>/` and then turned `&lt;` back into `<`, which is a
 * machine for building `<script>` out of text somebody escaped on purpose —
 * and it handed that string back from a function named as though it were safe,
 * in a file that uses `dangerouslySetInnerHTML` forty lines above. Nothing
 * renders it as HTML today; React escapes what it is given. The next caller is
 * the problem, and it is not one to leave for them.
 *
 * `DOMParser` runs no script and fetches nothing — it builds a detached
 * document — and `textContent` of one cannot contain markup at all, which is
 * the whole property this function was pretending to have.
 */
export function sectionText(section: Pick<ReportSection, 'content' | 'format'>): string {
  if (section.format !== 'html') return section.content
  const doc = new DOMParser().parseFromString(section.content, 'text/html')
  // A break is a newline, and a block ends one. Done on the tree rather than on
  // the string so `<br>` inside an attribute is not mistaken for one.
  doc.querySelectorAll('br').forEach((br) => br.replaceWith('\n'))
  doc.body.querySelectorAll(BLOCKS).forEach((block) => block.append('\n'))
  return (doc.body.textContent ?? '').replace(/\n{3,}/g, '\n\n').trim()
}
