/**
 * One type scale for the deck, in the 400x225 slide units `SlideView` draws in.
 *
 * `apps/api/app/services/deck_type.py` carries the same table for the `.pptx` and `.pdf`
 * exporters (multiplied by 2.4 for their 960x540-point page); a test keeps the two equal,
 * so what fits in the panel fits in the file. Change a number in both.
 */
export const TYPE = {
  // Covers.
  cover: 27,
  coverPoster: 30,
  coverMono: 32,
  closing: 24,
  coverBody: 13,
  closingBody: 15,
  closingBullets: 12,
  sectionNumber: 15,
  splitNumber: 34,
  // Body slides.
  title: 18,
  body: 12,
  bodyNarrow: 10.5,
  paragraph: 11.5,
  agenda: 11,
  agendaNumber: 13,
  statement: 26,
  statementBody: 11,
  quote: 20,
  quoteBy: 12,
  bigNumber: 46,
  bigNumberLabel: 11,
  bigNumberBody: 11,
  metric: 30,
  metricLabel: 11,
  cardName: 11,
  cardText: 9.5,
  stepBadge: 9,
  stepName: 11,
  stepText: 9.5,
  tileMark: 26,
  tileName: 10,
  bandMin: 7,
  bandMax: 10,
  lineMin: 7,
  lineMax: 10,
  tableMin: 7.5,
  tableMax: 12,
  caption: 10,
  footer: 7.5,
  pageNumber: 8,
  posterNumber: 28,
  gutterNumber: 18,
} as const

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
export const BODY_TOP = 66.5
export const BODY_BOTTOM = 190
export const PAD_X = 28

/** Table cell size from the row count, one row in reserve for a cell that wraps. */
export function tableSize(rows: number): number {
  const perRow = (BODY_BOTTOM - BODY_TOP) / (rows + 1.6)
  return Math.max(TYPE.tableMin, Math.min(TYPE.tableMax, perRow / 2.05))
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

/** Each column's share of the table width, from its widest cell (3–22 ems, plus padding). */
export function columnShares(rows: string[][]): number[] {
  const count = Math.max(0, ...rows.map((row) => row.length))
  if (!count) return []
  const weights = Array.from({ length: count }, (_, column) => {
    const widest = Math.max(0, ...rows.map((row) => (column < row.length ? em(row[column]) : 0)))
    return Math.max(3, Math.min(22, widest)) + 2
  })
  const total = weights.reduce((sum, weight) => sum + weight, 0)
  return weights.map((weight) => weight / total)
}
