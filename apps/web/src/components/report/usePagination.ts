import { useCallback, useEffect, useState } from 'react'

/** A4 at 96 dpi. */
export const A4_HEIGHT_PX = 1123
export const A4_WIDTH_PX = 794

/** Read-only page-break estimates for the continuous web preview. */
export function usePagination(
  /** Null before the portal renders. */
  root: HTMLElement | null,
  /** Change forces a re-measure. */
  revision: unknown,
) {
  const [height, setHeight] = useState(0)
  // Per-page content height after template padding.
  const [usable, setUsable] = useState(A4_HEIGHT_PX)
  const [breaks, setBreaks] = useState<number[]>([])

  const measure = useCallback(() => {
    if (!root) return
    const style = getComputedStyle(root)
    const padding = (parseFloat(style.paddingTop) || 0) + (parseFloat(style.paddingBottom) || 0)
    const room = A4_HEIGHT_PX - padding
    if (room > 0) {
      setUsable(room)
      // Must match the seeds' `--page-h` cover size, or the second guide falls through the cover.
      root.style.setProperty('--page-h', `${room}px`)
    }
    const contentHeight = root.scrollHeight
    setHeight(contentHeight)

    // Cuts between rendered lines (Range rects); page-view rules: orphans/widows 2, figure/table/quote atomic, heading kept with next.
    const rootTop = root.getBoundingClientRect().top
    type Line = { top: number; bottom: number; block: Element | null; kind: 'text' | 'heading' | 'atomic' }
    const BLOCKS = 'p, li, h1, h2, h3, h4, dt, dd, figure, table, blockquote, pre'
    const kindOf = (block: Element | null): Line['kind'] => {
      if (!block) return 'text'
      if (/^H[1-4]$/.test(block.tagName)) return 'heading'
      if (['FIGURE', 'TABLE', 'BLOCKQUOTE', 'PRE'].includes(block.tagName)) return 'atomic'
      return 'text'
    }
    const lines: Line[] = []
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
    while (walker.nextNode()) {
      const node = walker.currentNode
      if (!node.textContent?.trim()) continue
      const block = node.parentElement?.closest(BLOCKS) ?? null
      const range = document.createRange()
      range.selectNodeContents(node)
      for (const rect of Array.from(range.getClientRects())) {
        if (rect.height <= 0) continue
        const top = rect.top - rootTop
        const bottom = rect.bottom - rootTop
        // Several rects on one visual line (inline marks): one line.
        const same = lines.find((line) => line.block === block && Math.abs(line.top - top) < 2)
        if (same) same.bottom = Math.max(same.bottom, bottom)
        else lines.push({ top, bottom, block, kind: kindOf(block) })
      }
    }
    // Text-less pictures (stored diagram): atomic lines.
    for (const img of Array.from(root.querySelectorAll<HTMLElement>('img'))) {
      const rect = img.getBoundingClientRect()
      if (rect.height > 0) lines.push({ top: rect.top - rootTop, bottom: rect.bottom - rootTop, block: img.closest(BLOCKS) ?? img, kind: 'atomic' })
    }
    lines.sort((a, b) => a.top - b.top)
    const indexOf = (line: Line) => lines.indexOf(line)
    const blockLines = (block: Element | null) => (block ? lines.filter((line) => line.block === block) : [])
    /** Last line before `line`'s block: cut point for an unsplittable block. */
    const beforeBlock = (line: Line) => {
      const first = blockLines(line.block)[0] ?? line
      return lines[indexOf(first) - 1]
    }
    // A cover ends its page: cut at its bottom edge.
    const forced = Array.from(root.querySelectorAll<HTMLElement>('.cover, [data-page-break="true"]'))
      .map((el) => el.getBoundingClientRect().bottom - rootTop + (parseFloat(getComputedStyle(el).marginBottom) || 0))
      .sort((a, b) => a - b)
    const next: number[] = []
    let target = room
    while (target < contentHeight) {
      // A wall slightly past the target still wins: cover sized to the page, give or take.
      const wall = forced.find((edge) => edge > (next.at(-1) ?? 0) + 20 && edge <= target + room * 0.12)
      if (wall !== undefined) {
        next.push(wall)
        target = wall + room
        continue
      }
      const floor = next.at(-1) ?? 0
      let before = lines.filter((line) => line.bottom <= target && line.top >= floor).at(-1)
      // Pulled up until every rule holds; each step moves earlier, so it ends.
      for (let step = 0; before && step < 12; step += 1) {
        const after = lines[indexOf(before) + 1]
        if (!after || after.top < floor) break
        let moved: Line | undefined = before
        if (after.block && after.block === before.block) {
          const group = blockLines(after.block)
          const i = group.indexOf(after)
          if (after.kind === 'atomic') moved = beforeBlock(after)
          else if (i < 2) moved = beforeBlock(after)
          else if (group.length - i < 2) moved = group[i - 2]
        }
        if (moved === before && before.kind === 'heading') moved = beforeBlock(before)
        if (moved === before || !moved || moved.top < floor) break
        before = moved
      }
      const after = before ? lines[indexOf(before) + 1] : undefined
      // Next page starts at its first line's top; the gap at the previous page's foot is dropped, as in the page view.
      let cut = before ? (after ? after.top - 1 : before.bottom + 2) : target
      if (cut <= floor + 20) cut = target
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
