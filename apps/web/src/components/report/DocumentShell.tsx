import { useEffect, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'

/**
 * A shadow root the template's own stylesheet is loaded into.
 *
 * Everywhere else in this app an authored document is shown in a `sandbox=""`
 * iframe, and for a card or a preview that is exactly right — nothing in there
 * is meant to be clicked, and the sandbox is the cheapest possible guarantee.
 * An editor has to be clicked, focused and typed in, so it has to live in the
 * page. That trades the sandbox for two problems, and this component is both
 * answers.
 *
 * **The seed's CSS must not touch the app.** A template stylesheet is written
 * for a whole document: it styles `body`, bare `h1`, bare `table`. Injected
 * into the page it would restyle the panel around it. A shadow root is the
 * only boundary in the platform that stops a stylesheet by construction rather
 * than by naming discipline.
 *
 * **The app's CSS must not touch the document.** The inverse matters just as
 * much and is easier to forget: Tailwind's reset would flatten the very
 * margins and type scale the template exists to set, so a document edited in
 * the page would not look like the file that comes out of it. The same
 * boundary handles this direction for free.
 *
 * React renders into the shadow root through a portal, so everything inside is
 * ordinary React — state, events and refs all work, which is what makes an
 * editor possible at all.
 */
export function DocumentShell({
  css,
  className,
  children,
}: {
  /** The template's stylesheet, from `designTemplatesApi.style`. */
  css: string
  className?: string
  children: ReactNode
}) {
  const host = useRef<HTMLDivElement>(null)
  const [root, setRoot] = useState<ShadowRoot | null>(null)
  const sheet = useRef<HTMLStyleElement | null>(null)

  useEffect(() => {
    const node = host.current
    if (!node) return
    // `shadowRoot` survives a re-render and cannot be attached twice; in
    // StrictMode this effect runs again on the same node.
    const shadow = node.shadowRoot ?? node.attachShadow({ mode: 'open' })
    if (!sheet.current) {
      sheet.current = document.createElement('style')
      shadow.appendChild(sheet.current)
    }
    setRoot(shadow)
  }, [])

  useEffect(() => {
    // Replaced rather than appended: switching 서식 mid-document would
    // otherwise leave both stylesheets fighting, and the loser is whichever
    // one happens to be less specific.
    if (sheet.current) sheet.current.textContent = css
  }, [css])

  return (
    <div ref={host} className={className}>
      {root ? createPortal(children, root as unknown as Element) : null}
    </div>
  )
}
