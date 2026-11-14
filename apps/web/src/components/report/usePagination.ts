import { useCallback, useEffect, useState } from 'react'

/** A4 at 96 dpi. */
export const A4_HEIGHT_PX = 1123
export const A4_WIDTH_PX = 794

/**
 * The edit view's sheet: how tall its content is and how much of an A4 page the
 * margins leave (`--page-h`, `--sheet-h` for the template seeds). It does not guess
 * where pages break: the edit view is a Markdown document; exact pages are the page
 * view's (Paged.js) and the exports'. Only the person's own page breaks show there.
 */
export function useSheet(
  /** Null before the portal renders. */
  root: HTMLElement | null,
  /** Change forces a re-measure. */
  revision: unknown,
) {
  const [height, setHeight] = useState(0)
  // Per-page content height after template padding.
  const [usable, setUsable] = useState(A4_HEIGHT_PX)

  const measure = useCallback(() => {
    if (!root) return
    const style = getComputedStyle(root)
    const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0)
    const room = A4_HEIGHT_PX - padding
    if (room > 0) {
      setUsable(room)
      root.style.setProperty('--page-h', `${room}px`)
    }
    setHeight(root.scrollHeight)
  }, [root])

  useEffect(() => {
    if (!root) return
    measure()
    const observer = new ResizeObserver(measure)
    observer.observe(root)
    // Re-measure once web fonts land.
    void document.fonts?.ready.then(measure)
    return () => observer.disconnect()
  }, [root, measure, revision])

  return { usable, height }
}
