/**
 * One type scale for documents, in points.
 *
 * `apps/api/app/services/doc_type.py` carries the same table for the page seeds, the
 * reportlab PDF, the `.docx` and the `.hwpx`; a test keeps the two equal. The web view
 * draws these at 96 dpi (`px`). Change a number in both files.
 */
export const TYPE = {
  title: 20,
  lead: 11.5,
  h1: 14,
  h2: 12,
  h3: 11,
  body: 10.5,
  table: 9.5,
  caption: 9,
  note: 9,
  small: 9,
  kpi: 20,
  kpiLabel: 9,
  sectionNumber: 10.5,
  pageNumber: 9,
} as const

/** Line height as a multiple of the size. */
export const LEADING = {
  title: 1.25,
  heading: 1.35,
  body: 1.6,
  table: 1.45,
  note: 1.5,
} as const

/** A size in CSS pixels at 96 dpi. */
export function px(pt: number): number {
  return Math.round((pt * 4) / 3 * 100) / 100
}

/** Custom properties for the web view: `--doc-<name>` in px and `--doc-leading-<name>`. */
export function docVariables(): Record<string, string> {
  const out: Record<string, string> = {}
  for (const [name, pt] of Object.entries(TYPE)) out[`--doc-${name}`] = `${px(pt)}px`
  for (const [name, ratio] of Object.entries(LEADING)) out[`--doc-leading-${name}`] = `${ratio}`
  return out
}
