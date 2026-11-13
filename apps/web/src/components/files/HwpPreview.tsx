import { createEditor, type RhwpEditor } from '@rhwp/editor'
import { useEffect, useRef, useState } from 'react'
import { isHwpx, STUDIO_URL } from '@/components/hwp/HwpPanel'
import { useT } from '@/lib/useT'
import { reasonOf, safePage, type OfficePreviewProps } from './officePreview'

/** Pages drawn for a preview; the rest are in the editor or the download. */
const MAX_PAGES = 30

/**
 * An HWP/HWPX file's pages as the HWP editor (rhwp-studio) draws them: the editor runs unseen
 * and hands over each page as SVG, so the preview reads like the document and cannot be edited.
 */
export default function HwpPreview({ bytes, label, onReady, onFail }: OfficePreviewProps) {
  const t = useT()
  const host = useRef<HTMLDivElement>(null)
  const pages = useRef<HTMLDivElement>(null)
  const [more, setMore] = useState(0)
  // the callbacks are the caller's; the editor is made once per file
  const latest = useRef({ onReady, onFail })
  useEffect(() => {
    latest.current = { onReady, onFail }
  })

  useEffect(() => {
    let gone = false
    let made: RhwpEditor | null = null
    const sheets = pages.current!
    const draw = async () => {
      const editor = await createEditor(host.current!, { studioUrl: STUDIO_URL })
      if (gone) {
        editor.destroy()
        return
      }
      made = editor
      editor.element.tabIndex = -1
      editor.element.setAttribute('aria-hidden', 'true')
      const hwpx = isHwpx(bytes)
      // the studio takes the buffer over: hand it a copy
      const { pageCount } = await editor.loadFile(bytes.slice(0), hwpx ? 'document.hwpx' : 'document.hwp', {
        skipUnsavedGuard: true,
      })
      const shown = Math.min(pageCount, MAX_PAGES)
      for (let page = 0; page < shown; page += 1) {
        const svg = safePage(await editor.getPageSvg(page, { profile: 'screen' }))
        if (gone) return
        if (!svg) throw new Error('page not drawn')
        svg.removeAttribute('width')
        svg.removeAttribute('height')
        svg.setAttribute('role', 'img')
        svg.setAttribute('aria-label', t('{n}쪽').replace('{n}', String(page + 1)))
        const sheet = document.createElement('div')
        sheet.className = 'w-full max-w-[800px] bg-white shadow-sm ring-1 ring-black/10 [&>svg]:block [&>svg]:h-auto [&>svg]:w-full'
        sheet.dataset.hwpPage = String(page + 1)
        sheet.append(svg)
        sheets.append(sheet)
        if (page === 0) latest.current.onReady()
      }
      if (shown === 0) throw new Error('no pages')
      setMore(pageCount - shown)
    }
    draw().catch((err: unknown) => {
      if (!gone) latest.current.onFail(reasonOf(err))
    })
    return () => {
      gone = true
      made?.destroy()
      sheets.replaceChildren()
    }
    // One drawing per file; the preview is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div className="absolute inset-0 overflow-auto bg-elevated" role="region" aria-label={label} data-hwp-preview="">
      {/* the editor that draws the pages, out of sight and out of the tab order */}
      <div ref={host} aria-hidden="true" className="pointer-events-none absolute h-px w-px overflow-hidden opacity-0" />
      <div ref={pages} className="flex flex-col items-center gap-4 p-4 max-sm:p-2" />
      {more > 0 && (
        <p className="pb-4 text-center text-sm text-faint">
          {t('앞 {n}쪽만 보여 줍니다. 나머지는 편집기나 내려받은 파일에서 보세요.').replace('{n}', String(MAX_PAGES))}
        </p>
      )}
    </div>
  )
}
