/** Deck type scale in 400x225 slide units; mirrors `deck_type.py` (×2.4 for the 960x540-pt exports), test-enforced — change both. */
/** Points per slide unit: the exporters' 960-point page over this 400-unit panel. */
export const K = 2.4

/** Sizes in points: titles 26, body 18 (22 on a sparse deck) stepping down `STEPS`; floor 12. */
export const TYPE = {
  // Covers.
  cover: 36,
  coverPoster: 40,
  coverMono: 40,
  closing: 32,
  coverBody: 18,
  closingBody: 18,
  closingBullets: 18,
  sectionNumber: 18,
  splitNumber: 80,
  // Body slides.
  title: 26,
  body: 18,
  bodyNarrow: 18,
  paragraph: 18,
  agenda: 18,
  agendaNumber: 22,
  statement: 32,
  statementBody: 18,
  quote: 28,
  quoteBy: 16,
  bigNumber: 64,
  bigNumberLabel: 18,
  bigNumberBody: 18,
  metric: 44,
  metricLabel: 16,
  cardName: 18,
  cardText: 16,
  // a card row whose texts are three lines or fewer: number, larger name and text fill the card
  cardMark: 32,
  cardNameShort: 22,
  cardTextShort: 18,
  // comparison columns with few, short items: one row per item, aligned across the columns
  columnRow: 22,
  columnRowSmall: 18,
  stepBadge: 16,
  stepName: 18,
  stepText: 16,
  tileMark: 36,
  tileName: 16,
  bandMin: 14,
  bandMax: 18,
  lineMin: 14,
  lineMax: 18,
  tableMin: 12,
  tableMax: 16,
  caption: 14,
  footer: 12,
  pageNumber: 12,
  posterNumber: 30,
  gutterNumber: 22,
} as const

/** The body ladder in points. */
export const STEPS = [22, 18, 16, 14, 12] as const
/** No text is drawn smaller than this, whatever the scale. */
export const FLOOR_PT = 12

/** A size in points as slide units. */
export function units(pt: number): number {
  return pt / K
}

/** Slide titles are 32pt; one that would wrap steps down to 30, then 28, and stays at 28. */
export const TITLE_STEPS = [26] as const

/** How many lines `text` takes at `size` slide units in a column `width` units wide. */
export function lines(text: string, size: number, width: number): number {
  if (!text?.trim()) return 0
  const perLine = Math.max(1, width / size)
  return Math.max(1, Math.ceil(Math.floor(em(text) * 100) / Math.floor(perLine * 100)))
}

/** The title's size in points for its column width in slide units. */
export function titlePt(title: string, width: number = 400 - 2 * PAD_X): number {
  for (const size of TITLE_STEPS) if (lines(title, size / K, width) <= 1) return size
  return TITLE_STEPS[TITLE_STEPS.length - 1]
}

/** Line heights, as a multiple of the size. */
export const LEADING = {
  title: 1.25,
  body: 1.6,
  paragraph: 1.6,
  agenda: 1.5,
  cardText: 1.5,
  stepText: 1.5,
  band: 1.5,
  line: 1.5,
  table: 1.4,
} as const

/** Space between bullet items, as a multiple of the body size. */
export const BULLET_GAP = 0.35

/** Body-slide geometry in slide units; the title sits above the body box. */
export const BODY_TOP = 60
export const BODY_BOTTOM = 190
export const PAD_X = 28

/** Table cell size from the row count, one row in reserve for a cell that wraps. */
export function tableSize(rows: number): number {
  const perRow = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
  return Math.max(units(TYPE.tableMin), Math.min(units(TYPE.tableMax), perRow / 2.05))
}

/** Cell padding that goes with `tableSize`. */
export function tablePad(rows: number): number {
  const perRow = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
  return Math.max(2, (perRow - tableSize(rows) * LEADING.table) / 2)
}

/** Width of `text` in ems: a Hangul or CJK glyph is one em, anything else about half. */
export function em(text: string): number {
  let width = 0
  for (const char of text ?? '') {
    const code = char.codePointAt(0) ?? 0
    if ((code >= 0xac00 && code <= 0xd7a3) || (code >= 0x3000 && code <= 0x9fff) || (code >= 0xff00 && code <= 0xffef)) width += 1
    else if (char === ' ') width += 0.3
    else width += 0.55
  }
  return width
}

/** Shortest value drawn once across its row; a shorter shared mark (✓, Yes, N/A, —) keeps its cells. `deck_type.SHARED_MIN`. */
export const SHARED_MIN = 4

/** A body row's one value under every value column (2+), drawn once across them; `deck_type.shared_value`. */
export function sharedValue(row: string[], columns: number): string | null {
  if (row.length !== columns || columns < 3) return null
  const text = String(row[1] ?? '').trim()
  if ([...text].length < SHARED_MIN || row.slice(2).some((cell) => String(cell ?? '').trim() !== text)) return null
  return text
}

/** Each column's share of the table width, from its widest cell (3–22 ems, plus padding); a shared value counts a share of itself in each column it spans. */
export function columnShares(rows: string[][]): number[] {
  const count = Math.max(0, ...rows.map((row) => row.length))
  if (!count) return []
  const spread = rows.map((row, r) => r > 0 && sharedValue(row, count) !== null)
  const weights = Array.from({ length: count }, (_, column) => {
    const widest = Math.max(0, ...rows.map((row, r) => (column < row.length ? em(row[column]) / (spread[r] && column ? count - 1 : 1) : 0)))
    return Math.max(3, Math.min(22, widest)) + 2
  })
  const total = weights.reduce((sum, weight) => sum + weight, 0)
  return weights.map((weight) => weight / total)
}
