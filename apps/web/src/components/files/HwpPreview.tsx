import { createEditor, type RhwpEditor } from '@rhwp/editor'
import { useEffect, useRef } from 'react'
import { isHwpx, STUDIO_URL } from '@/components/hwp/HwpPanel'
import { lazyPages, type LazyPages } from '@/components/hwp/lazyPages'
import { useT } from '@/lib/useT'
import { reasonOf, safePage, type OfficePreviewProps } from './officePreview'

/**
 * An HWP/HWPX file's pages as the HWP editor (rhwp-studio) draws them: the editor runs unseen
 * and hands over each page as SVG, so the preview reads like the document and cannot be edited.
 * Every page is there; each is drawn as the reader scrolls to it (`lazyPages`).
 */
export default function HwpPreview({ bytes, label, onReady, onFail }: OfficePreviewProps) {
  const t = useT()
  const host = useRef<HTMLDivElement>(null)
  const pages = useRef<HTMLDivElement>(null)
  const scroller = useRef<HTMLDivElement>(null)
  // the callbacks are the caller's; the editor is made once per file
  const latest = useRef({ onReady, onFail })
  useEffect(() => {
    latest.current = { onReady, onFail }
  })

  useEffect(() => {
    let gone = false
    let made: RhwpEditor | null = null
    let laid: LazyPages | null = null
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
      if (gone) return
      if (pageCount < 1) throw new Error('no pages')
      laid = lazyPages(
        sheets,
        scroller.current,
        pageCount,
        async (page) => safePage(await editor.getPageSvg(page, { profile: 'screen' })),
        (n) => t('{n}쪽').replace('{n}', String(n)),
        () => latest.current.onReady(),
        (err) => latest.current.onFail(reasonOf(err)),
      )
    }
    draw().catch((err: unknown) => {
      if (!gone) latest.current.onFail(reasonOf(err))
    })
    return () => {
      gone = true
      laid?.stop()
      made?.destroy()
      sheets.replaceChildren()
    }
    // One drawing per file; the preview is keyed by it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  return (
    <div ref={scroller} className="absolute inset-0 overflow-auto bg-elevated" role="region" aria-label={label} data-hwp-preview="">
      {/* the editor that draws the pages, out of sight and out of the tab order */}
      <div ref={host} aria-hidden="true" className="pointer-events-none absolute h-px w-px overflow-hidden opacity-0" />
      <div ref={pages} className="flex flex-col items-center gap-4 p-4 max-sm:p-2" />
    </div>
  )
}
