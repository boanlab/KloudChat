import { useCallback, useEffect, useState } from 'react'

/** A4 at 96dpi. */
export const A4_HEIGHT_PX = 1123
export const A4_WIDTH_PX = 794
/** CSS pixels per millimetre (96 dpi). */
export const PX_PER_MM = 96 / 25.4

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
    if (room > 0) {
      setUsable(room)
      // The seeds size the cover from `--page-h`; the guides cut at `room`, so they must agree
      // or the second guide falls through the cover.
      root.style.setProperty('--page-h', `${room}px`)
    }
    const contentHeight = root.scrollHeight
    setHeight(contentHeight)

    // Cuts fall between rendered lines, never through one; Range rects are the browser's own
    // line layout. Each line remembers its block, so the cut can follow the page view's rules:
    // a paragraph keeps two lines on either side of a cut (orphans/widows), a figure, table or
    // quote is never split, and a heading goes with what follows it.
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
        // Several rects on one visual line (inline marks) are one line.
        const same = lines.find((line) => line.block === block && Math.abs(line.top - top) < 2)
        if (same) same.bottom = Math.max(same.bottom, bottom)
        else lines.push({ top, bottom, block, kind: kindOf(block) })
      }
    }
    // Pictures without text (a stored diagram) are lines too, and atomic ones.
    for (const img of Array.from(root.querySelectorAll<HTMLElement>('img'))) {
      const rect = img.getBoundingClientRect()
      if (rect.height > 0) lines.push({ top: rect.top - rootTop, bottom: rect.bottom - rootTop, block: img.closest(BLOCKS) ?? img, kind: 'atomic' })
    }
    lines.sort((a, b) => a.top - b.top)
    const indexOf = (line: Line) => lines.indexOf(line)
    const blockLines = (block: Element | null) => (block ? lines.filter((line) => line.block === block) : [])
    /** The last line before `line`'s block: where a cut goes when the block must stay whole. */
    const beforeBlock = (line: Line) => {
      const first = blockLines(line.block)[0] ?? line
      return lines[indexOf(first) - 1]
    }
    // A cover ends its page: the cut lands on its bottom edge even when its lower half is empty.
    const forced = Array.from(root.querySelectorAll<HTMLElement>('.cover, [data-page-break="true"]'))
      .map((el) => el.getBoundingClientRect().bottom - rootTop + (parseFloat(getComputedStyle(el).marginBottom) || 0))
      .sort((a, b) => a - b)
    const next: number[] = []
    let target = room
    while (target < contentHeight) {
      // A wall a little past the target still wins: the cover is sized to the page, give or take.
      const wall = forced.find((edge) => edge > (next.at(-1) ?? 0) + 20 && edge <= target + room * 0.12)
      if (wall !== undefined) {
        next.push(wall)
        target = wall + room
        continue
      }
      const floor = next.at(-1) ?? 0
      let before = lines.filter((line) => line.bottom <= target && line.top >= floor).at(-1)
      // Pull the cut up until it satisfies every rule; each step moves it earlier, so it ends.
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
      // The next page begins at the top of its first line, as in the page view, which drops
      // the gap (a section's margin, say) left at the foot of the page before it.
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
