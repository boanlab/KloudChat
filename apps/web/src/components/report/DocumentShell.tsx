import { useEffect, useRef, useState, type ReactNode } from 'react'
import { A4_HEIGHT_PX } from '@/components/report/usePagination'
import { createPortal } from 'react-dom'

/** Shadow root for the template stylesheet: template and app CSS isolated; children via portal. */
export function DocumentShell({
  css,
  className,
  children,
}: {
  /** From `designTemplatesApi.style`. */
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
    // StrictMode re-runs this; a shadow root attaches once.
    const shadow = node.shadowRoot ?? node.attachShadow({ mode: 'open' })
    if (!sheet.current) {
      sheet.current = document.createElement('style')
      shadow.appendChild(sheet.current)
    }
    setRoot(shadow)
  }, [])

  useEffect(() => {
    if (!sheet.current) return
    // `--page-h` for the seeds; standalone they fall back to `100vh`.
    sheet.current.textContent = `:host { --page-h: ${A4_HEIGHT_PX}px; }\n${css}`
  }, [css])

  return (
    <div ref={host} className={className}>
      {root ? createPortal(children, root as unknown as Element) : null}
    </div>
  )
}
