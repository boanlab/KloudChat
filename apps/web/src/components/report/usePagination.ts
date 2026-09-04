import { useCallback, useEffect, useState } from 'react'

/** A4 at 96dpi. */
export const A4_HEIGHT_PX = 1123
export const A4_WIDTH_PX = 794

/** Read-only page-break estimates for the continuous web preview. */
export function usePagination(
  /** Document root, or null before the portal has rendered it. */
  root: HTMLElement | null,
  /** Changes with the content to force a re-measure. */
  revision: unknown,
) {
  const [height, setHeight] = useState(0)
  // Content height per page after the template's vertical padding.
  const [usable, setUsable] = useState(A4_HEIGHT_PX)
  const [breaks, setBreaks] = useState<number[]>([])

  const measure = useCallback(() => {
    if (!root) return
    const style = getComputedStyle(root)
    const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0)
    const room = A4_HEIGHT_PX - padding
    if (room > 0) setUsable(room)
    const contentHeight = root.scrollHeight
    setHeight(contentHeight)

    // Cuts fall between rendered lines, never through one; Range rects are the browser's own line layout.
    const rootTop = root.getBoundingClientRect().top
    const lines: { top: number; bottom: number }[] = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    while (walker.nextNode()) {
      const node = walker.currentNode
      if (!node.textContent?.trim()) continue
      const range = document.createRange()
      range.selectNodeContents(node)
      for (const rect of Array.from(range.getClientRects())) {
        if (rect.height > 0) lines.push({ top: rect.top - rootTop, bottom: rect.bottom - rootTop })
      }
    }
    lines.sort((a, b) => a.top - b.top)
    const next: number[] = []
    let target = room
    while (target < contentHeight) {
      const before = lines.filter((line) => line.bottom <= target).at(-1)
      const after = lines.find((line) => line.top > (before?.bottom ?? target))
      let cut = before ? before.bottom + Math.max(2, ((after?.top ?? before.bottom + 4) - before.bottom) / 2) : target
      if (cut <= (next.at(-1) ?? 0) + 20) cut = target
      next.push(cut)
      target = cut + room
    }
    setBreaks(next)
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

  const pages = breaks.length + 1
  return { pages, usable, height, breaks }
}
