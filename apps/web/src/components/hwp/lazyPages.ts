/**
 * HWP pages drawn lazily: every sheet is laid out up front (scroll length = document length),
 * but a page's SVG is requested from the editor only when its sheet nears the view.
 */

/** How far ahead of the view a page is drawn. */
const AHEAD = '1200px 0px'
/** A sheet's shape before its page is drawn: A4 portrait, until the first page says otherwise. */
const A4 = '210 / 297'
const SHEET =
  'w-full max-w-[800px] bg-white shadow-sm ring-1 ring-black/10 [&>svg]:block [&>svg]:h-auto [&>svg]:w-full'

export interface LazyPages {
  /** Pages drawn so far, by number from 1. */
  drawn: () => number[]
  /** Stops drawing and takes the sheets away. */
  stop: () => void
}

/**
 * Lays out `count` sheets in `container`, drawing each when it nears `scroller`'s view.
 * `draw(page)` returns the cleaned SVG or null; `label(n)` is the screen-reader name; `onFirst`/`onFail` fire on first-page success/failure.
 */
export function lazyPages(
  container: HTMLElement,
  scroller: HTMLElement | null,
  count: number,
  draw: (page: number) => Promise<SVGSVGElement | null>,
  label: (n: number) => string,
  onFirst?: () => void,
  onFail?: (err: unknown) => void,
): LazyPages {
  let gone = false
  const done = new Set<number>()
  const sheets: HTMLElement[] = []
  for (let page = 0; page < count; page += 1) {
    const sheet = document.createElement('div')
    sheet.className = SHEET
    sheet.style.aspectRatio = A4
    sheet.dataset.hwpPage = String(page + 1)
    sheet.setAttribute('aria-label', label(page + 1))
    sheets.push(sheet)
  }
  container.replaceChildren(...sheets)

  // one page at a time: the editor draws in order
  let queue: Promise<void> = Promise.resolve()
  const wanted = new Set<number>()
  const put = (page: number) => {
    if (gone || done.has(page) || wanted.has(page)) return
    wanted.add(page)
    queue = queue.then(async () => {
      if (gone) return
      const svg = await draw(page)
      if (gone) return
      wanted.delete(page)
      done.add(page)
      const sheet = sheets[page]
      if (!svg) {
        if (page === 0) onFail?.(new Error('page not drawn'))
        return
      }
      svg.removeAttribute('width')
      svg.removeAttribute('height')
      svg.setAttribute('role', 'img')
      svg.setAttribute('aria-label', label(page + 1))
      const box = svg.viewBox?.baseVal
      if (box && box.width > 0 && box.height > 0) {
        const shape = `${box.width} / ${box.height}`
        sheet.style.aspectRatio = shape
        if (page === 0) {
          // pages not drawn yet take the first page's shape
          for (const other of sheets) if (!done.has(Number(other.dataset.hwpPage) - 1)) other.style.aspectRatio = shape
        }
      }
      sheet.append(svg)
      if (page === 0) onFirst?.()
    }).catch((err: unknown) => {
      wanted.delete(page)
      if (page === 0 && !gone) onFail?.(err)
    })
  }

  const watch = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        if (entry.isIntersecting) put(Number((entry.target as HTMLElement).dataset.hwpPage) - 1)
      }
    },
    { root: scroller, rootMargin: AHEAD },
  )
  if (count > 0) put(0)
  for (const sheet of sheets) watch.observe(sheet)

  return {
    drawn: () => [...done].map((page) => page + 1).sort((a, b) => a - b),
    stop: () => {
      gone = true
      watch.disconnect()
      container.replaceChildren()
    },
  }
}
