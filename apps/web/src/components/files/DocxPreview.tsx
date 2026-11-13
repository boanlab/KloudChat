import { mountDocxEditor } from 'open-docx-editor'
import 'open-docx-editor/style.css'
import 'open-docx-editor/fonts.css'
import '@fontsource/nanum-gothic/400.css'
import '@fontsource/nanum-gothic/700.css'
import { useEffect, useRef } from 'react'
import { aliasKoreanFonts } from '@/components/docx/koreanFonts'
import { useStore } from '@/store/useStore'
import { reasonOf, type OfficePreviewProps } from './officePreview'

/** A `.docx` laid out in pages by the Word editor (open-docx-editor), read only: no ribbon. */
export default function DocxPreview({ bytes, name, label, onReady, onFail }: OfficePreviewProps) {
  const lang = useStore((s) => s.lang)
  const host = useRef<HTMLDivElement>(null)
  const latest = useRef({ onReady, onFail })
  useEffect(() => {
    latest.current = { onReady, onFail }
  })

  useEffect(() => {
    const el = host.current
    if (!el) return
    let gone = false
    aliasKoreanFonts()
    const api = mountDocxEditor(el, {
      fileName: name,
      readOnly: true,
      locale: lang === 'en' ? 'en' : 'ko',
      features: { newDocument: false, openFile: false },
      onError: (err) => {
        if (!gone) latest.current.onFail(reasonOf(err))
      },
    })
    api.setZoom('pageWidth')
    api.open(new Uint8Array(bytes), name).then(
      () => !gone && latest.current.onReady(),
      (err: unknown) => !gone && latest.current.onFail(reasonOf(err)),
    )
    return () => {
      gone = true
      api.destroy()
    }
    // One editor per file; the preview is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div
      ref={host}
      role="region"
      aria-label={label}
      data-docx-preview=""
      className="absolute inset-0 flex flex-col bg-white [&>.odx-canvas]:min-h-0 [&>.odx-canvas]:flex-1"
    />
  )
}
