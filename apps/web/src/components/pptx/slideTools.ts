/**
 * What the PowerPoint editor's chrome (`SlidesEditor`) works out on its own: where a newly
 * inserted element goes so it does not sit exactly on the last one, the header row of a new
 * table, the editor's own Korean words, and plain-text offsets of a selection inside its
 * inline text editor.
 */

/** How far each newly inserted element moves off one already at its spot, px. */
export const STAGGER = 24

interface Box {
  id: string
  x: number
  y: number
  width: number
  height: number
}

/**
 * Where `added` goes so that no other element starts at the same spot: one step down and right
 * per element already there, wrapping back near the start where it would leave the slide.
 */
export function staggered(
  added: Box,
  others: readonly Box[],
  canvas: { width: number; height: number },
): { x: number; y: number } {
  const taken = (x: number, y: number) =>
    others.some((o) => o.id !== added.id && Math.abs(o.x - x) < 2 && Math.abs(o.y - y) < 2)
  let { x, y } = added
  for (let step = 1; step < 40 && taken(x, y); step += 1) {
    x = added.x + STAGGER * step
    y = added.y + STAGGER * step
    if (x + added.width > canvas.width || y + added.height > canvas.height) {
      // back near the start, one step aside of the first column of copies
      x = Math.max(0, added.x - STAGGER * (step % 4 + 1))
      y = Math.max(0, added.y - STAGGER * (step % 4 + 1))
    }
  }
  return { x, y }
}

interface TableCell {
  text?: string
  [key: string]: unknown
}
interface TableData {
  rows: { cells: TableCell[]; [key: string]: unknown }[]
  [key: string]: unknown
}

/** The editor names a new table's columns 「Header 1」…; in `label` instead (「항목 {n}」). */
export function relabelHeader(table: TableData, label: string): TableData | null {
  const first = table.rows[0]
  if (!first || !first.cells.some((cell) => /^Header \d+$/.test(cell.text ?? ''))) return null
  return {
    ...table,
    rows: [
      {
        ...first,
        cells: first.cells.map((cell) => {
          const found = /^Header (\d+)$/.exec(cell.text ?? '')
          return found ? { ...cell, text: label.replace('{n}', found[1]) } : cell
        }),
      },
      ...table.rows.slice(1),
    ],
  }
}

/** The editor's own words that still show inside the canvas and thumbnails, in Korean. */
export const CRV_KO: Record<string, string> = {
  'pptx.sections.slides': '슬라이드',
  'pptx.element.hasAction': '동작 있음',
  'pptx.element.linkFallback': '링크',
  'pptx.linkTooltip.followLink': '링크 열기',
  'pptx.linkTooltip.presentationMode': '슬라이드 쇼에서 동작',
  'pptx.selectionOverlay.resize': '크기 조절',
  'pptx.selectionOverlay.rotate': '회전',
  'pptx.canvas.adjustShape': '모양 조절',
}

/** Korean for the editor's keys; anything else falls back to its built-in English. */
export function translateKo(key: string): string | undefined {
  return CRV_KO[key]
}

function textNodes(root: Node): Text[] {
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT)
  const out: Text[] = []
  while (walker.nextNode()) out.push(walker.currentNode as Text)
  return out
}

export interface CharRange {
  start: number
  end: number
}

/** The DOM selection inside `editor` as plain-text offsets, or null when it is elsewhere. */
export function charSelection(editor: HTMLElement): CharRange | null {
  const selection = window.getSelection()
  if (!selection || !selection.rangeCount) return null
  const range = selection.getRangeAt(0)
  if (!editor.contains(range.startContainer)) return null
  const offset = (node: Node, at: number) => {
    let n = 0
    for (const text of textNodes(editor)) {
      if (text === node) return n + at
      n += text.data.length
    }
    const before = document.createRange()
    before.selectNodeContents(editor)
    before.setEnd(node, at)
    return before.toString().length
  }
  return {
    start: offset(range.startContainer, range.startOffset),
    end: offset(range.endContainer, range.endOffset),
  }
}

/** Puts the selection back at plain-text offsets inside `editor`. */
export function setCharSelection(editor: HTMLElement, chars: CharRange | null): void {
  if (!chars) return
  const texts = textNodes(editor)
  const at = (k: number): [Node, number] => {
    let n = 0
    for (const text of texts) {
      if (k <= n + text.data.length) return [text, k - n]
      n += text.data.length
    }
    const last = texts[texts.length - 1]
    return last ? [last, last.data.length] : [editor, 0]
  }
  const range = document.createRange()
  const [a, aOffset] = at(chars.start)
  const [b, bOffset] = at(chars.end)
  range.setStart(a, aOffset)
  range.setEnd(b, bOffset)
  const selection = window.getSelection()
  selection?.removeAllRanges()
  selection?.addRange(range)
}

/** The word around a collapsed caret at `at` in `text`; null when the caret is in space. */
export function wordAt(text: string, at: number): CharRange | null {
  let start = at
  let end = at
  while (start > 0 && !/\s/.test(text[start - 1])) start -= 1
  while (end < text.length && !/\s/.test(text[end])) end += 1
  return start === end ? null : { start, end }
}
