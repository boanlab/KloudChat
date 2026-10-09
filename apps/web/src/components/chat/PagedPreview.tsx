import { useEffect, useMemo, useRef, useState } from 'react'
import { DEFAULT_PAGE_SETTINGS, pagedSheetCss } from '@/components/report/pagedSheet'
import { A4_HEIGHT_PX, A4_WIDTH_PX } from '@/components/report/usePagination'
import { useT } from '@/lib/useT'

/** The Paged.js polyfill as a file the frame loads by URL; the package exports no subpath for it,
 *  so it is named by path and Vite emits it as an asset. */
const PAGED_POLYFILL = new URL('../../../node_modules/pagedjs/dist/paged.polyfill.min.js', import.meta.url).href

/** Room around the pages in the full view, in frame pixels; the gap between two pages is the same. */
const GUTTER = 24

/** The preview page of a 서식, fetched once per id: the frame lays it out itself. */
const pages = new Map<string, Promise<string>>()
const previewPage = (id: string) => {
  let found = pages.get(id)
  if (!found) {
    found = fetch(`/api/design-templates/${id}/preview`, { credentials: 'same-origin' }).then((response) => {
      if (!response.ok) throw new Error(String(response.status))
      return response.text()
    })
    found.catch(() => pages.delete(id))
    pages.set(id, found)
  }
  return found
}

/**
 * The preview page laid out as the page view lays out a report: Paged.js with the same sheet
 * (`pagedSheetCss`), A4 pages on a grey backdrop. It runs inside the frame, which may run
 * scripts but has no origin of its own; it reports the page count and height back.
 */
function pagedFrame(page: string, polyfill: string, firstOnly: boolean): string {
  const parsed = new DOMParser().parseFromString(page, 'text/html')
  const css = [...parsed.querySelectorAll('style')].map((node) => node.textContent ?? '').join('\n')
  const display = firstOnly
    ? `html, body { background: white !important; overflow: hidden !important; }
      .pagedjs_pages { display: block; }
      .pagedjs_page + .pagedjs_page { display: none !important; }`
    : `html, body { background: #e3e6ec !important; overflow: hidden !important; }
      .pagedjs_pages { display: flex; flex-direction: column; align-items: center; gap: ${GUTTER}px; padding: ${GUTTER}px 0; }
      .pagedjs_page { flex: none; background: white; box-shadow: 0 2px 12px rgb(0 0 0 / 14%); }`
  const done = `window.PagedConfig = { auto: true, after: (flow) => parent.postMessage({ kind: 'kc-paged', pages: flow.total, height: document.documentElement.scrollHeight }, '*') }`
  return `<!doctype html><html lang="ko"><head><meta charset="utf-8">
<style>${pagedSheetCss(css, DEFAULT_PAGE_SETTINGS)}</style>
<style>${display}</style>
<script>${done}</script>
<script src="${new URL(polyfill, window.location.href).href}"></script>
</head><body>${parsed.body.innerHTML}</body></html>`
}

/**
 * A document 서식's sample as its pages: the whole document in the full view (scrolled by the
 * box around it, with 「n / m쪽」), or page 1 alone on a card.
 */
export function PagedPreview({ id, firstOnly = false }: { id: string; firstOnly?: boolean }) {
  const t = useT()
  const box = useRef<HTMLDivElement>(null)
  const frame = useRef<HTMLIFrameElement>(null)
  // Each keyed by the id it belongs to, so a new id starts blank without resetting state in an effect.
  const [loaded, setLoaded] = useState<{ id: string; html: string; failed: boolean } | null>(null)
  const [layout, setLayout] = useState<{ doc: string; pages: number; height: number } | null>(null)
  const [scale, setScale] = useState(0)
  const [current, setCurrent] = useState(1)
  const width = firstOnly ? A4_WIDTH_PX : A4_WIDTH_PX + GUTTER * 2

  useEffect(() => {
    let live = true
    previewPage(id)
      .then((page) => { if (live) setLoaded({ id, html: page, failed: false }) })
      .catch(() => { if (live) setLoaded({ id, html: '', failed: true }) })
    return () => { live = false }
  }, [id])

  const html = loaded?.id === id ? loaded.html : ''
  const failed = loaded?.id === id && loaded.failed
  const srcDoc = useMemo(() => (html ? pagedFrame(html, PAGED_POLYFILL, firstOnly) : ''), [html, firstOnly])
  const laid = layout && layout.doc === srcDoc ? layout : null

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.source !== frame.current?.contentWindow) return
      const data = event.data as { kind?: string; pages?: number; height?: number }
      if (data?.kind !== 'kc-paged') return
      setLayout({ doc: srcDoc, pages: Number(data.pages) || 1, height: Number(data.height) || A4_HEIGHT_PX })
    }
    window.addEventListener('message', onMessage)
    return () => window.removeEventListener('message', onMessage)
  }, [srcDoc])

  useEffect(() => {
    const node = box.current
    if (!node) return
    const fit = () => setScale(firstOnly
      // A card shows page 1 whole, as tall as its strip.
      ? Math.min(node.clientWidth / width, node.clientHeight / A4_HEIGHT_PX)
      : node.clientWidth / width)
    fit()
    const observer = new ResizeObserver(fit)
    observer.observe(node)
    return () => observer.disconnect()
  }, [width, firstOnly])

  // The page under the top of the scrolling box around the full view.
  useEffect(() => {
    if (firstOnly || !laid) return
    const scroller = box.current?.closest<HTMLElement>('[data-preview-scroll]')
    if (!scroller) return
    const stride = (A4_HEIGHT_PX + GUTTER) * scale
    const onScroll = () => setCurrent(Math.min(laid.pages, Math.max(1, Math.floor((scroller.scrollTop + stride / 3) / stride) + 1)))
    onScroll()
    scroller.addEventListener('scroll', onScroll, { passive: true })
    return () => scroller.removeEventListener('scroll', onScroll)
  }, [firstOnly, laid, scale])

  const height = firstOnly ? A4_HEIGHT_PX : laid?.height ?? A4_HEIGHT_PX + GUTTER * 2
  return (
    <div
      ref={box}
      className={firstOnly ? 'relative flex h-full w-full justify-center overflow-hidden' : 'relative w-full'}
      style={firstOnly ? undefined : { height: scale ? height * scale : undefined }}
      data-page-count={laid?.pages}
    >
      {!firstOnly && laid && (
        <span className="sticky top-2 z-10 float-right mr-2 mt-2 rounded-full bg-panel/90 px-2 py-0.5 text-xs tabular-nums text-muted shadow-sm" aria-live="polite">
          {t('{n} / {m}쪽').replace('{n}', String(current)).replace('{m}', String(laid.pages))}
        </span>
      )}
      {failed && <p className="p-4 text-sm text-muted">{t('미리보기를 불러오지 못했습니다')}</p>}
      {srcDoc && (
        <iframe
          ref={frame}
          srcDoc={srcDoc}
          // Scripts for the pager only: no origin, no forms, no navigation of the app.
          sandbox="allow-scripts"
          tabIndex={-1}
          title=""
          className={firstOnly ? 'pointer-events-none shrink-0 origin-top bg-white shadow-sm' : 'pointer-events-none absolute left-0 top-0 origin-top-left'}
          // Hidden until laid out, or the unpaged page flashes at full size.
          style={{
            width,
            height,
            border: 0,
            transform: `scale(${scale})`,
            visibility: scale && laid ? 'visible' : 'hidden',
            ...(firstOnly ? { marginBottom: -height * (1 - scale) } : {}),
          }}
        />
      )}
    </div>
  )
}
