import { useCallback, useEffect, useState } from 'react'

/** A4 at 96 dpi. */
export const A4_HEIGHT_PX = 1123
export const A4_WIDTH_PX = 794

/** Edit-view sheet height and A4 margin room (`--page-h`, `--sheet-h`); does not predict page breaks (Paged.js and exports do). */
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
